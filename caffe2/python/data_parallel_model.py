## @package data_parallel_model
# Module caffe2.python.data_parallel_model
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from collections import OrderedDict
import logging
import copy

from caffe2.python import model_helper, dyndep, scope, workspace, core, memonger
from caffe2.proto import caffe2_pb2

dyndep.InitOpsLibrary("@/caffe2/caffe2/contrib/nccl:nccl_ops")
dyndep.InitOpsLibrary("@/caffe2/caffe2/contrib/gloo:gloo_ops")
dyndep.InitOpsLibrary("@/caffe2/caffe2/contrib/gloo:gloo_ops_gpu")

log = logging.getLogger("data_parallel_model")
log.setLevel(logging.INFO)


def Parallelize_GPU(
    model_helper_obj,
    input_builder_fun,
    forward_pass_builder_fun,
    param_update_builder_fun,
    devices=range(0, workspace.NumCudaDevices()),
    rendezvous=None,
    net_type='dag',
    broadcast_computed_params=True,
    optimize_gradient_memory=False,
    use_nccl=False,
    max_concurrent_distributed_ops=4,
):
    '''
    Function to create a model that can run on many GPUs.
      model_helper_obj: an object of ModelHelper, such as CNNModelHelper
      input_builder_fun:
                         Function that adds the input operators
                         Note: Remember to instantiate reader outside of this
                         function so all GPUs share same reader object.
                         Signature:  input_builder_fun(model)
      forward_pass_builder_fun:
                        Function to add the operators to the model.
                        Must return list of loss-blob references that
                        are used to build the gradient. Loss scale parameter
                        is passed, as you should scale the loss of your model
                        by 1.0 / the total number of gpus.
                        Signature: forward_pass_builder_fun(model, loss_scale)
      param_update_builder_fun:
                        Function that adds operators that are run after
                        gradient update, such as updating the weights and
                        weight decaying.
                        Signature: param_update_builder_fun(model)
      devices:          List of GPU ids, such as [0, 1, 2, 3],
      rendezvous:       used for rendezvous in distributed computation, if None
                        then only one node is used. To create rendezvous,
                        use <TBD>.
      net_type:         Network type
      optimize_gradient_memory: whether to apply 'memonger' to share blobs
                        in gradient computation to reduce memory footprint

    '''
    log.info("Parallelizing model for devices: {}".format(devices))
    extra_workers = 8 if rendezvous is not None else 0  # best-guess
    num_workers = len(devices) * 4 + extra_workers
    max_concurrent_distributed_ops =\
        min(max_concurrent_distributed_ops, num_workers - 1)
    model_helper_obj.net.Proto().num_workers = num_workers
    model_helper_obj.net.Proto().type = net_type

    # Store some information in the model -- a bit ugly
    model_helper_obj._devices = devices
    model_helper_obj._rendezvous = rendezvous
    model_helper_obj._grad_names = []

    assert isinstance(model_helper_obj, model_helper.ModelHelper)

    # Keep track of params that were in the model before: they are not
    # data parallel, so we need to handle them separately
    non_datapar_params = copy.copy(model_helper_obj.params)

    # Add input and model
    log.info("Create input and model training operators")

    losses_by_gpu = {}
    num_shards = 1 if rendezvous is None else rendezvous['num_shards']
    loss_scale = 1.0 / (len(devices) * num_shards)

    for device in devices:
        device_opt = core.DeviceOption(caffe2_pb2.CUDA, device)
        with core.DeviceScope(device_opt):
            with core.NameScope("gpu_{}".format(device)):
                log.info("Model for GPU: {}".format(device))
                input_builder_fun(model_helper_obj)
                losses = forward_pass_builder_fun(model_helper_obj, loss_scale)
                # Losses are not needed for test net
                if param_update_builder_fun is not None:
                    assert isinstance(losses, list), \
                        'Model builder function must return list of loss blobs'
                    for loss in losses:
                        assert isinstance(loss, core.BlobReference), \
                            'Model builder func must return list of loss blobs'

                losses_by_gpu[device] = losses
    _ValidateParams(model_helper_obj.params)

    # Create parameter map
    model_helper_obj._device_grouped_blobs =\
        _GroupByDevice(devices, model_helper_obj.params, non_datapar_params)

    # computed params
    computed_params_grouped =\
        _GroupByDevice(devices, model_helper_obj.computed_params, [])
    model_helper_obj._device_grouped_blobs.update(computed_params_grouped)

    model_helper_obj._param_names =\
        model_helper_obj._device_grouped_blobs.keys()
    model_helper_obj._computed_param_names = computed_params_grouped.keys()

    if (param_update_builder_fun is None):
        log.info("Parameter update function not defined --> only forward")
        _InferBlobDevice(model_helper_obj)
        return

    log.info("Adding gradient operators")
    _AddGradientOperators(devices, model_helper_obj, losses_by_gpu)

    _ValidateParams(model_helper_obj.params)

    # Group gradients by device and register to blob lookup
    param_to_grad = model_helper_obj.param_to_grad
    grads_ordered = [param_to_grad[p] for p in
                     model_helper_obj.params if p in param_to_grad]
    non_datapar_grads = [param_to_grad[p] for p in non_datapar_params]

    gradients_grouped = _GroupByDevice(
        devices,
        grads_ordered,
        non_datapar_grads
    )
    model_helper_obj._device_grouped_blobs.update(gradients_grouped)
    model_helper_obj._grad_names = gradients_grouped.keys()
    model_helper_obj._losses_by_gpu = losses_by_gpu

    _InferBlobDevice(model_helper_obj)

    log.info("Add gradient all-reduces for SyncSGD")
    if broadcast_computed_params:
        _BroadcastComputedParams(devices, model_helper_obj, rendezvous)

    if len(model_helper_obj._grad_names) > 0:
        _AllReduceGradients(
            devices,
            model_helper_obj,
            rendezvous,
            use_nccl,
            max_concurrent_distributed_ops,
        )
    else:
        log.info("NOTE: Param builder function did not create any parameters.")

    log.info("Post-iteration operators for updating params")
    num_shards = 1 if rendezvous is None else rendezvous['num_shards']
    # The following check is necessary for ring reduce to work
    if rendezvous is not None:
        assert num_shards > 1, \
            "Please use more than one shard for distributed training"
    for device in devices:
        device_opt = core.DeviceOption(caffe2_pb2.CUDA, device)
        with core.DeviceScope(device_opt):
            with core.NameScope("gpu_{}".format(device)):
                param_update_builder_fun(model_helper_obj)

    (sync_blobs, sync_names) = _ComputeBlobsToSync(model_helper_obj)
    sync_blobs_grouped = _GroupByDevice(
        devices,
        sync_blobs,
        [],
    )
    model_helper_obj._device_grouped_blobs.update(sync_blobs_grouped)

    _InferBlobDevice(model_helper_obj)
    _AnalyzeOperators(model_helper_obj)

    # Configure dagnet to run with only one worker on the first iteration,
    # to prevent concurrency problems with allocs and nccl.
    arg = model_helper_obj.Proto().arg.add()
    arg.name = "first_iter_only_one_worker"
    arg.i = 1

    # Add initial parameter syncs
    log.info("Add initial parameter sync")
    if (rendezvous is not None):
        _AddDistributedParameterSync(
            devices,
            model_helper_obj,
            model_helper_obj.param_init_net,
            model_helper_obj.param_init_net,
            rendezvous,
            sync_names,
        )

    _SyncParams(
        devices, model_helper_obj, model_helper_obj.param_init_net, sync_names
    )

    if optimize_gradient_memory:
        _OptimizeGradientMemorySimple(model_helper_obj, losses_by_gpu, devices)

    model_helper_obj._data_parallel_model_init_nets = [
        model_helper_obj.param_init_net,
    ]
    model_helper_obj._data_parallel_model_nets = [model_helper_obj.net]


def Parallelize_GPU_BMUF(
    model_helper_obj,
    input_builder_fun,
    forward_pass_builder_fun,
    param_update_builder_fun,
    block_learning_rate=1.0,
    block_momentum=None,
    devices=range(0, workspace.NumCudaDevices()),
    net_type='dag',
    master_gpu=None,
    optimize_gradient_memory=False
):
    '''
    Function to create model that run on many GPUs and creates a net for
    parameter_updates that can be run independently for number of iterations
    then followed by another net that runs once to compute the final parameter
    updates according to block wise model update filtering rule described
    in : Scalable Training of Deep Learning Machines by Incremental Block
    Training with Intra-block Parallel Optimization and Blockwise Model-Update
    Filtering (ICASSP 2016).
    '''
    assert isinstance(model_helper_obj, model_helper.ModelHelper)

    if master_gpu is None:
        master_gpu = devices[0]

    model_helper_obj._devices = devices
    master_gpu_opt = core.DeviceOption(caffe2_pb2.CUDA, master_gpu)

    num_workers = len(devices)
    loss_scale = 1.0 / num_workers
    if block_momentum is None:
        block_momentum = 1.0 - 1.0 / num_workers

    model_helper_obj.net.Proto().num_workers = num_workers
    model_helper_obj.net.Proto().type = net_type

    # A net for initializing global model parameters. Its called once in the
    # same step as net parameters initialization.
    model_helper_obj._global_model_init_net = core.Net('global_model_init')
    model_helper_obj._global_model_init_net.Proto().type = net_type
    model_helper_obj._global_model_init_net.Proto().num_workers = num_workers

    # A net for computing final parameter updates. Its will run once after
    # running net (local models updates) for `num_local_iterations` times.
    model_helper_obj._global_model_param_updates_net = core.Net('global_model')
    model_helper_obj._global_model_param_updates_net.Proto().type = net_type
    model_helper_obj._global_model_param_updates_net.Proto().num_workers = \
        num_workers

    def _v(param):
        return "{}_v".format(param)

    def _g(param):
        return "{}_g".format(param)

    # Keep track of params that were in the model before: they are not
    # data parallel, so we need to handle them separately
    non_datapar_params = copy.copy(model_helper_obj.params)
    model_helper_obj._losses_by_gpu = {}

    def _InitializeModels(gpu_id):
        input_builder_fun(model_helper_obj)
        loss = forward_pass_builder_fun(model_helper_obj, loss_scale)
        model_helper_obj._losses_by_gpu[gpu_id] = loss
    _ForEachGPU(devices, _InitializeModels, scoped=True)

    model_helper_obj._device_grouped_blobs =\
        _GroupByDevice(devices, model_helper_obj.params, non_datapar_params)

    model_helper_obj._param_names =\
        model_helper_obj._device_grouped_blobs.keys()

    _AddGradientOperators(
        devices, model_helper_obj, model_helper_obj._losses_by_gpu
    )

    _InferBlobDevice(model_helper_obj)

    def _InitializeParamUpdate(gpu_id):
        param_update_builder_fun(model_helper_obj)
    _ForEachGPU(devices, _InitializeParamUpdate, scoped=True)

    # (Step-0) Initialize momentum parameters on master GPU.
    for param_name in model_helper_obj._device_grouped_blobs.keys():
        param = model_helper_obj._device_grouped_blobs[param_name][master_gpu]
        with core.DeviceScope(master_gpu_opt):
            model_helper_obj._global_model_init_net.ConstantFill(
                param, _v(param), value=0.0
            )
            model_helper_obj._global_model_init_net.Copy(param, _g(param))

    # (Step-1) Update models for num_local_iterations.

    # (Step-2) Comute post-local-updates average of the params.
    # Sum model params across GPUs and store resutls in param_avg blob.
    for param_name in model_helper_obj._device_grouped_blobs.keys():
        with core.DeviceScope(master_gpu_opt):
            _AllReduce(
                devices, model_helper_obj,
                model_helper_obj._global_model_param_updates_net,
                param_name
            )

    # (Step-3) Update momentum params :
    # param_v = block_momentum * param_v
    # + block_learning_Rate * (param_avg - param)
    # param = param + param_v
    for param_name in model_helper_obj._device_grouped_blobs.keys():
        param = model_helper_obj._device_grouped_blobs[param_name][master_gpu]
        with core.DeviceScope(master_gpu_opt):
            # TODO(ataei) : Stop building the graph here to get model average ?
            model_helper_obj._global_model_param_updates_net.Scale(
                param, param, scale=1.0 / num_workers
            )
            model_helper_obj._global_model_param_updates_net.Sub(
                [param, _g(param)], param
            )
            model_helper_obj._global_model_param_updates_net.Scale(
                param, param, scale=block_learning_rate
            )
            model_helper_obj._global_model_param_updates_net.Scale(
                _v(param), _v(param), scale=block_momentum
            )
            model_helper_obj._global_model_param_updates_net.Add(
                [_v(param), param], _v(param)
            )
            model_helper_obj._global_model_param_updates_net.Add(
                [_g(param), _v(param)], _g(param)
            )
            model_helper_obj._global_model_param_updates_net.Copy(
                _g(param), param
            )
            _Broadcast(
                devices, model_helper_obj,
                model_helper_obj._global_model_param_updates_net,
                param_name
            )

    if optimize_gradient_memory:
        _OptimizeGradientMemorySimple(
            model_helper_obj, model_helper_obj._losses_by_gpu, devices
        )

    model_helper_obj._data_parallel_model_init_nets = [
        model_helper_obj.param_init_net,
        model_helper_obj._global_model_init_net
    ]
    model_helper_obj._data_parallel_model_nets = [
        model_helper_obj.net,
        (model_helper_obj._global_model_param_updates_net, 1)
    ]


def RunInitNet(model):
    for init_net in model._data_parallel_model_init_nets:
        workspace.RunNetOnce(init_net)
    for net_iters in model._data_parallel_model_nets:
        if isinstance(net_iters, tuple):
            workspace.CreateNet(net_iters[0])
        else:
            workspace.CreateNet(net_iters)


def RunNet(model, num_iterations):
    for net_iter in model._data_parallel_model_nets:
        if isinstance(net_iter, tuple):
            workspace.RunNet(net_iter[0].Proto().name, net_iter[1])
        else:
            workspace.RunNet(model.net.Proto().name, num_iterations)


def _ForEachGPU(gpu_ids, f, scoped=False, *args, **kwargs):
    for gpu_id in gpu_ids:
        device_opt = core.DeviceOption(caffe2_pb2.CUDA, gpu_id)
        with core.DeviceScope(device_opt):
            if scoped:
                with core.NameScope("gpu_{}".format(gpu_id)):
                    f(gpu_id, *args, **kwargs)
            else:
                f(gpu_id, *args, **kwargs)


def _AddGradientOperators(devices, model, losses_by_gpu):
    def create_grad(lossp):
        return model.ConstantFill(lossp, str(lossp) + "_grad", value=1.0)

    loss_grad = {}
    # Explicitly need to create gradients on each GPU
    for gpu_id in devices:
        device = core.DeviceOption(caffe2_pb2.CUDA, gpu_id)
        with core.DeviceScope(device):
            for l in losses_by_gpu[gpu_id]:
                lg = create_grad(l)
                loss_grad[str(l)] = str(lg)

    model.AddGradientOperators(loss_grad)


def ExtractPredictorNet(model, inputs, outputs, device):
    '''
    Returns (net, params) that can be exported to be used as a prediction
    net.
    '''
    master_device = model._devices[0]
    prefix = "gpu_{}/".format(master_device)
    prefix_inputs = [prefix + str(b) for b in inputs]
    prefix_outputs = [prefix + str(b) for b in outputs]
    predictor_net = model_helper.ExtractPredictorNet(
        net_proto=model.net.Proto(),
        input_blobs=prefix_inputs,
        output_blobs=prefix_outputs,
        device=device,
        renames={
            a: b
            for (a, b) in zip(prefix_inputs + prefix_outputs, inputs + outputs)
        }
    )

    params = set(predictor_net.Proto().external_input) - set(inputs)
    return (predictor_net, params)


def GetCheckpointParams(model):
    '''
    Returns a set of blobs that are needed for a complete check point.
    They are blobs for the first gpu and iteration blobs.
    '''
    (all_blobs, _) = _ComputeBlobsToSync(model)
    return {
        b for b in all_blobs
        if str(b).startswith("gpu_{}/".format(model._devices[0]))}


def FinalizeAfterCheckpoint(model, blobs=None):
    '''
    This function should be called after loading parameters from a
    checkpoint / initial parameters file.
    '''

    if not hasattr(model, "_checkpoint_net"):
        if blobs is None:
            (_, uniq_blob_names) = _ComputeBlobsToSync(model)
        else:
            uniq_blob_names = [stripParamName(p) for p in blobs]

        # Synchronize to the blob lookup map, as the provided
        # blobs might have non-parameters, such as momemtum blobs.
        log.info("Creating checkpoint synchronization net")
        devices = model.GetDevices()
        for name in uniq_blob_names:
            if name not in model._device_grouped_blobs:
                grouped = {
                    d:
                    core.BlobReference("gpu_{}{}{}".format(
                        d,
                        scope._NAMESCOPE_SEPARATOR,
                        name)
                    ) for d in devices}
                model._device_grouped_blobs[name] = grouped

        model._checkpoint_net = core.Net("checkpoint_sync_net")
        model._checkpoint_net.RunAllOnGPU()

        if (model._rendezvous is not None):
            checkpoint_init_net = core.Net("checkpoint_init_net")
            checkpoint_init_net.RunAllOnGPU()
            _AddDistributedParameterSync(
                devices,
                model,
                checkpoint_init_net,
                model._checkpoint_net,
                model._rendezvous,
                uniq_blob_names,
            )
            workspace.RunNetOnce(checkpoint_init_net)

        # Setup sync of initial params
        _SyncParams(devices, model, model._checkpoint_net, uniq_blob_names)

        workspace.CreateNet(model._checkpoint_net)

    # Run the sync
    log.info("Run checkpoint net")
    workspace.RunNet(model._checkpoint_net.Proto().name)


def _Broadcast(devices, model, net, param):
    # TODO(akyrola): replace with NCCLBroadcast when it's working
    # Copy params from gpu_0 to other
    master_gpu = devices[0]
    for gpu_idx in devices[1:]:
        if _IsGPUBlob(model, param):
            device_opt = core.DeviceOption(caffe2_pb2.CUDA, gpu_idx)
        else:
            device_opt = core.DeviceOption(caffe2_pb2.CPU, 0)
        with core.DeviceScope(device_opt):
            net.Copy(
                model._device_grouped_blobs[param][master_gpu],
                model._device_grouped_blobs[param][gpu_idx]
            )


def _AllReduce(devices, model, net, param, use_nccl=False, control_input=None):
    blobs_group = model._device_grouped_blobs[param].values()
    if use_nccl:
        model.NCCLAllreduce(
            blobs_group, blobs_group, control_input=control_input
        )
        return

    def sum2(d1i, d2i):
        d1 = model._devices[d1i]
        d2 = model._devices[d2i]
        device_opt = core.DeviceOption(caffe2_pb2.CUDA, d1)
        with core.DeviceScope(device_opt):
            net.Sum(
                [blobs_group[d1], blobs_group[d2]], [blobs_group[d1]],
                name="dpm",
            )
    if len(devices) == 8:
        # Special tree reduction for 8 gpus, TODO generalize like in muji.py
        for j in range(4):
            sum2(j * 2, j * 2 + 1)
        for j in range(2):
            sum2(j * 4, j * 4 + 2)
        sum2(0, 4)
        _Broadcast(devices, model, net, param)
    elif len(devices) == 4:
        sum2(0, 1)
        sum2(2, 3)
        sum2(0, 2)
        _Broadcast(devices, model, net, param)
    else:
        net.Sum(blobs_group, blobs_group[0], name="dpm")
        _Broadcast(devices, model, net, param)


def _SyncParams(devices, model, net, unique_param_names):
    for param in unique_param_names:
        _Broadcast(devices, model, net, param)


def _AddDistributedParameterSync(
    devices,
    model,
    init_net,
    net,
    rendezvous,
    uniq_param_names,
):
    gpu_device_opt = core.DeviceOption(caffe2_pb2.CUDA, devices[0])
    cpu_device_opt = core.DeviceOption(caffe2_pb2.CPU)


    # Create a single common world for all broadcast operations.
    # This is not a problem since they are executed sequentially.
    comm_world = None
    for param_name in sorted(uniq_param_names):
        param = model._device_grouped_blobs[param_name][devices[0]]

        def broadcast(comm_world, param):
            if comm_world is None:
                comm_world = init_net.CreateCommonWorld(
                    rendezvous['kv_handler'],
                    "broadcast_cw",
                    name=net.Proto().name + ".broadcast_cw_op",
                    size=rendezvous['num_shards'],
                    rank=rendezvous['shard_id'],
                    engine=rendezvous['engine'],
                    status_blob="createcw_broadcast_status",
                )
            net.Broadcast(
                inputs=[comm_world, param],
                outputs=[param],
                engine=rendezvous['engine'],
                status_blob="broadcast_{}_status".format(str(param)),
            )
            return comm_world

        device_opt = gpu_device_opt if _IsGPUBlob(
            model, param_name
        ) else cpu_device_opt

        if rendezvous['engine'] == 'GLOO':
            with core.DeviceScope(device_opt):
                comm_world = broadcast(comm_world, param)
        else:
            # Copy between GPU and CPU
            with core.DeviceScope(device_opt):
                param_cpu = net.CopyGPUToCPU(param, str(param) + "cpu")
            with core.DeviceScope(cpu_device_opt):
                comm_world = broadcast(comm_world, param_cpu)
            with core.DeviceScope(device_opt):
                net.CopyCPUToGPU(param_cpu, param)


def _AllReduceGradients(devices, model, rendezvous, use_nccl,
                        max_concurrent_distributed_ops):
    if rendezvous is None:
        _AllReduceGradientsSingleHost(devices, model, use_nccl)
    else:
        _AllReduceGradientsDistributed(
            devices,
            model,
            rendezvous,
            max_concurrent_distributed_ops,
        )


def _AllReduceGradientsDistributed(
    devices,
    model,
    rendezvous,
    max_concurrent_distributed_ops,
):
    num_workers = model.net.Proto().num_workers
    assert num_workers > 1, "Please specify more than 1 worker"
    all_reduce_engine = rendezvous['engine']

    # Make list of gradients in reverse order
    reverse_ordered_grads = _GetReverseOrderedGrads(model)

    master_device_opt = core.DeviceOption(caffe2_pb2.CUDA, devices[0])
    reducing_device_opt = master_device_opt

    # We need to specify a partial order using control_input to ensure
    # progress (all machines need to do same allreduce in parallel)
    num_controls = max_concurrent_distributed_ops
    cyclical_controls = []

    # Since num_controls determines the partial ordering of
    # allreduces, there is no need for more common world instances
    # than there are parallel allreduce operations.
    num_comm_worlds = num_controls
    cyclical_comm_worlds = []

    counter = 0
    nccl_control_blob = None

    # Note: sorted order to ensure each host puts the operators in
    # same order.
    for grad_name in reverse_ordered_grads:
        master_grad = model._device_grouped_blobs[grad_name][devices[0]]
        grads_group = model._device_grouped_blobs[grad_name].values()

        assert master_grad in grads_group

        # Remark: NCCLReduce does not support in-place modifications
        # so we need a temporary gradient blob
        reduced_grad = str(master_grad) + "_red"

        control_input = None if len(cyclical_controls) < num_controls \
                        else cyclical_controls[counter % num_controls]
        comm_world = None if len(cyclical_comm_worlds) < num_comm_worlds \
                     else cyclical_comm_worlds[counter % num_comm_worlds]

        def allreduce(comm_world, grads):
            with core.DeviceScope(reducing_device_opt):
                if comm_world is None:
                    comm_number = len(cyclical_comm_worlds)
                    comm_world = model.param_init_net.CreateCommonWorld(
                        rendezvous['kv_handler'],
                        "allreduce_{}_cw".format(comm_number),
                        name="allreduce_{}_cw_op".format(comm_number),
                        size=rendezvous['num_shards'],
                        rank=rendezvous['shard_id'],
                        engine=rendezvous['engine'],
                        status_blob="create_cw_{}_status".format(comm_number),
                    )
                model.net.Allreduce(
                    inputs=[comm_world] + grads,
                    outputs=grads,
                    name=grad_name,
                    engine=all_reduce_engine,
                    control_input=control_input,
                    status_blob="allreduce_{}_status".format(grad_name),
                )
                return comm_world

        if rendezvous['engine'] == 'GLOO':
            # With Gloo cross GPU and cross machine allreduce
            # can be executed in a single operation
            comm_world = allreduce(comm_world, grads_group)
            control_output = grads_group[0]
        else:
            # Step 1: sum gradients from local GPUs to master GPU
            with core.DeviceScope(master_device_opt):
                model.ConstantFill(master_grad, reduced_grad, value=0.0)

                # Temp fix since NCCLReduce does not work
                model.net.NCCLAllreduce(
                    grads_group,
                    grads_group,
                    control_input=nccl_control_blob,
                )
                nccl_control_blob = grads_group[0]
                model.net.Copy(master_grad, reduced_grad)

            # Step 2: allreduce between all hosts, between master GPUs
            comm_world = allreduce(comm_world, [reduced_grad])
            control_output = reduced_grad

            with core.DeviceScope(master_device_opt):
                model.net.Copy(reduced_grad, master_grad)

            # Step 3: broadcast locally
            _Broadcast(devices, model, model.net, grad_name)

        if len(cyclical_controls) < num_controls:
            cyclical_controls.append(control_output)
        else:
            cyclical_controls[counter % num_controls] = control_output

        if len(cyclical_comm_worlds) < num_comm_worlds:
            cyclical_comm_worlds.append(comm_world)
        else:
            assert cyclical_comm_worlds[counter % num_comm_worlds] == comm_world

        counter += 1


def _AllReduceGradientsSingleHost(devices, model, use_nccl):
    """Performs NCCL AllReduce to distribute gradients to all the GPUs."""

    if len(devices) == 1:
        return

    # Gradients in reverse order
    reverse_ordered_grads = _GetReverseOrderedGrads(model)
    assert(len(reverse_ordered_grads) > 0)

    # Now we need to Allreduce gradients on all the GPUs.
    # Pick GPU #0 as a master GPU.
    master_device_opt = core.DeviceOption(caffe2_pb2.CUDA, devices[0])
    last_out = None
    concatenated_idx = set()

    for grad_name in reverse_ordered_grads:
        # Group by grads for reduce.
        grads_group = model._device_grouped_blobs[grad_name].values()
        assert len(grads_group) == len(devices), \
            "Each GPU from {}, should have a copy of {}.".format(
                devices, grad_name)

        if _IsGPUBlob(model, grad_name):
            with core.DeviceScope(master_device_opt):
                if not isinstance(grads_group[0], core.GradientSlice):
                    _AllReduce(
                        devices, model, model.net, grad_name, use_nccl, last_out
                    )
                    # last_out is used to serialize the execution of nccls
                    last_out = grads_group[0]

                else:
                    # Sparse gradients: all-gather for indices and values
                    master_ns = "gpu_{}".format(devices[0])
                    '''
                    Skip if we have already copied concatenated indices
                    to the indices of GradientSlice. This happens when two
                    or more grad blobs are gathered with the same indices
                    blob
                    '''
                    skip_idx_concat = False
                    for g in grads_group:
                        if g.indices in concatenated_idx:
                            skip_idx_concat = True

                    if not skip_idx_concat:
                        grad_idx_concat, _ = model.net.Concat(
                            [g.indices for g in grads_group],
                            ["{}/{}_index_concat".format(master_ns, grad_name),
                             "{}/{}_index_splitinfo".format(master_ns, grad_name)],
                            axis=0,
                            name="note:data_parallel_model")
                        for gpu, g in model._device_grouped_blobs[grad_name].items():
                            device_opt = core.DeviceOption(caffe2_pb2.CUDA, gpu)
                            with core.DeviceScope(device_opt):
                                model.Copy(grad_idx_concat, g.indices)
                                concatenated_idx.add(g.indices)

                    grad_val_concat, _ = model.net.Concat(
                        [g.values for g in grads_group],
                        ["{}/{}_val_concat".format(master_ns, grad_name),
                         "{}/{}_val_splitinfo".format(master_ns, grad_name)],
                        axis=0, name="note:data_parallel_model")
                    for gpu, g in model._device_grouped_blobs[grad_name].items():
                        device_opt = core.DeviceOption(caffe2_pb2.CUDA, gpu)
                        with core.DeviceScope(device_opt):
                            model.Copy(grad_val_concat, g.values)

        else:
            assert not isinstance(grads_group[0], core.GradientSlice), \
                "Synchronizing gradient slices not supported"
            with core.DeviceScope(core.DeviceOption(caffe2_pb2.CPU)):
                # Poor man's allreduce
                model.Sum(grads_group, grads_group[0])
                _Broadcast(devices, model, grad_name)


def _BroadcastComputedParams(devices, model, rendezvous):
    if rendezvous is None:
        _BroadcastComputedParamsSingleHost(devices, model)
    else:
        _BroadcastComputedParamsDistributed(devices, model, rendezvous)


def _BroadcastComputedParamsDistributed(
    devices,
    model,
    rendezvous,
):
    _BroadcastComputedParamsSingleHost(devices, model)
    log.warn("Distributed computed params all-reduce not implemented yet")


def _BroadcastComputedParamsSingleHost(devices, model):
    '''
    Average computed params over all devices
    '''
    if len(devices) == 1:
        return

    for param_name in model._computed_param_names:
        # Copy from master to others -- averaging would be perhaps better,
        # but currently NCCLAllReduce is too prone to deadlock
        _Broadcast(devices, model, model.net, param_name)


def _GetReverseOrderedGrads(model):
    '''
    Returns the gradients in reverse order (namespace stripped),
    for the optimal synchronization order.
    '''
    return list(reversed(model._grad_names))


# A helper function to extract a parameter's name
def stripParamName(param):
    # Format is "a/b/c/d" -> "b/c/d"
    if isinstance(param, core.GradientSlice):
        return stripParamName(param.indices) + ":" + stripParamName(param.values)
    else:
        name = str(param)
    return name[name.index(scope._NAMESCOPE_SEPARATOR) + 1:]


def _AnalyzeOperators(model):
    '''
    Look at all the operators and check that they do not cross device scopes
    '''
    for op in model.Proto().op:
        if "NCCL" in op.type or "Copy" in op.type or "Concat" in op.type:
            continue
        if "Sum" == op.type and op.name == "dpm":
            continue
        if "Allreduce" in op.type and "GLOO" in op.engine:
            continue

        op_dev = op.device_option
        op_gpu = op_dev.cuda_gpu_id

        # This avoids failing on operators that are only for CPU
        if op_dev.device_type == caffe2_pb2.CPU:
            continue

        namescope = "gpu_{}/".format(op_gpu)
        for inp in list(op.input) + list(op.output):
            if inp.startswith("gpu_") and not inp.startswith(namescope):
                raise Exception(
                    "Blob {} of op {}, should have namescope {}. Op: {}".format(
                        inp, op.type, "gpu_{}/".format(op_gpu), str(op),
                    ))


def _InferBlobDevice(model):
    '''
    Assign blob to device option based on the operator outputing it
    '''
    mapping = {}

    def map_ops(proto):
        for op in proto.op:
            device_option = op.device_option
            if op.type == "Iter":
                # Hack for Iters which have blob in CPU context
                device_option = caffe2_pb2.DeviceOption()
                device_option.device_type = caffe2_pb2.CPU
            for b in list(op.input) + list(op.output):
                if b not in mapping:
                    mapping[b] = device_option
            if op.type.startswith('RecurrentNetwork'):
                import google.protobuf.text_format as protobuftx
                step_args = [a for a in op.arg if a.name.endswith("step_net")]
                for step_arg in step_args:
                    step_proto = caffe2_pb2.NetDef()
                    protobuftx.Merge(step_arg.s, step_proto)
                    map_ops(step_proto)
    map_ops(model.net.Proto())
    model._blob_to_device = mapping


def _IsGPUBlob(model, blob_name):
    if blob_name in model._blob_to_device:
        return model._blob_to_device[blob_name].device_type == caffe2_pb2.CUDA
    else:
        blob_name = "gpu_{}/{}".format(model._devices[0], blob_name)
        if blob_name not in model._blob_to_device:
            return True
        return model._blob_to_device[blob_name].device_type == caffe2_pb2.CUDA


def _GroupByDevice(devices, params, non_data_params):
    '''
    Groups blobs by device, returning a map of [blobname] = {0: BlobRef, 1: ..}.
    Returns ordered dictionary, ensuring the original order.
    '''
    grouped = OrderedDict()
    # Only consider params that were created to be  "data parallel"
    params = params[len(non_data_params):]
    assert len(params) % len(devices) == 0,\
           "There should be equal number of params per device"

    num_params_per_device = int(len(params) / len(devices))

    for i, p in enumerate(params):
        assert isinstance(p, core.BlobReference) or \
            isinstance(p, core.GradientSlice), \
            "Param {} is not BlobReference or GradientSlice".format(p)

        name = stripParamName(p)
        gpuid = devices[i // num_params_per_device]

        if isinstance(p, core.BlobReference):
            assert "gpu_{}/".format(gpuid) in p.GetNameScope(),\
                "Param {} expected to have namescope 'gpu_{}'".format(str(p), gpuid)
        else:
            assert "gpu_{}/".format(gpuid) in p.indices.GetNameScope(),\
                "Indices {} expected to have namescope 'gpu_{}'".format(str(p), gpuid)
            assert "gpu_{}/".format(gpuid) in p.values.GetNameScope(),\
                "Values {} expected to have namescope 'gpu_{}'".format(str(p), gpuid)

        if name not in grouped:
            grouped[name] = {}
        grouped[name][gpuid] = p

    # Confirm consistency
    for j, (p, ps) in enumerate(grouped.items()):
        assert \
            len(ps) == len(devices), \
            "Param {} does not have value for each device (only {}: {})".format(
                p, len(ps), ps,
            )
        # Ensure ordering
        if (ps[devices[0]] != params[j]):
            log.error("Params: {}".format(params))
            log.error("Grouped: {}".format(grouped.keys()))
            assert ps[devices[0]] == params[j], \
                "Incorrect ordering: {}".format(ps)

    return grouped


def _ValidateParams(params):
    set_params = set(params)
    if len(params) > len(set_params):
        dupes = []
        sp = sorted(params)
        for j, p in enumerate(sp):
            if j > 0 and params[j - 1] == p:
                dupes.append(p)

        assert len(params) == len(set_params), \
            "Duplicate entries in params: {}".format(dupes)


def _ComputeBlobsToSync(model):
    '''
    We sync all blobs that are generated by param init net and
    are 'data parallel', i.e assigned to a gpu
    '''
    sync_names = set()
    blobs_to_sync = []
    for op in model.param_init_net.Proto().op:
        dp_outputs = [o for o in op.output if o.startswith("gpu_")]
        sync_names.update([stripParamName(o) for o in dp_outputs])
        blobs_to_sync.extend(dp_outputs)

    # Sanity check
    diff = set(model._param_names) - sync_names
    assert diff == set(), \
       "Some params not instantiated in param init net: {}".format(diff)

    # Remove duplicates and sort
    blobs_to_sync = sorted(list(set(blobs_to_sync)))

    blobs_to_sync = [core.BlobReference(b) for b in blobs_to_sync]
    return (blobs_to_sync, sync_names)


def _OptimizeGradientMemorySimple(model, losses_by_gpu, devices):
    log.warning("------- DEPRECATED API, please use " +
                   "data_parallel_model.OptimizeGradientMemory() ----- ")
    for device in devices:
        namescope = "gpu_{}/".format(device)
        model.net._net = memonger.share_grad_blobs(
            model.net,
            losses_by_gpu[device],
            set(model.param_to_grad.values()),
            namescope,
            share_activations=False,
        )


def OptimizeGradientMemory(model,
                           input_shapes,
                           excluded_blobs,
                           recycle_activations):
    """
    Optimize memory usage of the backward pass by recycling blobs for gradient
    inputs that have been 'used'.
    input_shapes:  dict of blob name to shape for the inputs of the model.
                   Pass empty dictionary if not known.
    excluded_blobs: list of blobs that cannot be recycled. These are blobs
                   that you will access externally.
    recycle_activations: whether to also recycle forward pass activations
    """
    input_shapes_all_devices = {}
    for b, shp in input_shapes.items():
        for d in model._devices:
            input_shapes_all_devices["gpu_{}/{}".format(d, b)] = shp

    (shapes, types) = workspace.InferShapesAndTypes(
        [model.param_init_net, model.net],
        input_shapes_all_devices,
    )

    for device in model._devices:
        namescope = "gpu_{}/".format(device)
        excluded_blobs_by_device = set([namescope + b for b in excluded_blobs])
        model.net._net = memonger.share_grad_blobs(
            model.net,
            model._losses_by_gpu[device],
            set(model.param_to_grad.values()),
            namescope,
            dont_share_blobs=excluded_blobs_by_device,
            share_activations=recycle_activations,
            blob_shapes=shapes,
        )
