## @package model_helper
# Module caffe2.python.model_helper
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from caffe2.python import core, scope, workspace
from caffe2.python.modeling import parameter_info


import logging

# _known_working_ops are operators that do not need special care.
_known_working_ops = [
    "Accuracy",
    "Adam",
    "Add",
    "Adagrad",
    "SparseAdagrad",
    "AveragedLoss",
    "Cast",
    "Checkpoint",
    "ConstantFill",
    "Copy",
    "CopyGPUToCPU",
    "CopyCPUToGPU",
    "DequeueBlobs",
    "EnsureCPUOutput",
    "Flatten",
    "FlattenToVec",
    "LabelCrossEntropy",
    "LearningRate",
    "MakeTwoClass",
    "MatMul",
    "NCCLAllreduce",
    "NHWC2NCHW",
    "PackSegments",
    "Print",
    "PRelu",
    "Scale",
    "ScatterWeightedSum",
    "Sigmoid",
    "SortedSegmentSum",
    "Snapshot",  # Note: snapshot is deprecated, use Checkpoint
    "Softmax",
    "SoftmaxWithLoss",
    "SquaredL2Distance",
    "Squeeze",
    "StopGradient",
    "Summarize",
    "Tanh",
    "UnpackSegments",
    "WeightedSum",
    "ReduceFrontSum",
]


class ModelHelper(object):
    """A helper model so we can manange models more easily. It contains net def
    and parameter storages. You can add an Operator yourself, e.g.

        model = model_helper.ModelHelper(name="train_net")
        # init your weight and bias as w and b
        w = model.param_init_net.XavierFill(...)
        b = model.param_init_net.ConstantFill(...)
        fc1 = model.FC([input, w, b], output, **kwargs)

    or you can use helper functions in brew module without manually
    defining parameter initializations and operators.

        model = model_helper.ModelHelper(name="train_net")
        fc1 = brew.fc(model, input, output, dim_in, dim_out, **kwargs)

    """

    def __init__(self, name=None, init_params=True, allow_not_known_ops=True,
                 skip_sparse_optim=False, param_model=None, arg_scope=None):
        self.name = name or "model"
        self.net = core.Net(self.name)

        if param_model is not None:
            self.param_init_net = param_model.param_init_net
            self.param_to_grad = param_model.param_to_grad
            self.params = param_model.params
            self.computed_params = param_model.computed_params
        else:
            self.param_init_net = core.Net(name + '_init')
            self.param_to_grad = {}
            self.params = []
            self.computed_params = []

        self._param_info_deprecated = []
        self._parameters_info = {}
        self._devices = []
        self.gradient_ops_added = False
        self.init_params = init_params
        self.allow_not_known_ops = allow_not_known_ops
        self.skip_sparse_optim = skip_sparse_optim
        self.weights = []
        self.biases = []
        self._arg_scope = {
            'order': "NCHW",
            'use_cudnn': True,
            'cudnn_exhaustive_search': False,
        }
        if arg_scope is not None:
            # Please notice value as None is not acceptable. We are not checking it
            # here because we already have check in MakeArgument.
            self._arg_scope.update(arg_scope)

    @property
    def arg_scope(self):
        return self._arg_scope

    def get_name(self):
        return self.name

    def _infer_param_shape(self, param):
        for op in self.param_init_net.Proto().op:
            if str(param) in op.output:
                for arg in op.arg:
                    if arg.name == "shape":
                        return list(arg.ints)
        return None

    def _update_param_info_deprecated(self):
        assert len(self._param_info_deprecated) <= len(self.params)
        for param in self.params[len(self._param_info_deprecated):]:
            if not isinstance(param, core.BlobReference):
                raise ValueError("Param %s must be a BlobReference!" % str(param))
            self._param_info_deprecated.append(parameter_info.ParameterInfo(
                param_id=len(self._param_info_deprecated),
                param=param,
                shape=self._infer_param_shape(param)))
        for info in self._param_info_deprecated:
            info.grad = self.param_to_grad.get(info.name)

    def create_param(self, param_name, shape, initializer):
        param_info = initializer.create_param(
            param_name=param_name,
            init_net=self.param_init_net,
            shape=shape,
        )
        self._parameters_info[param_info.blob] = param_info
        return param_info.blob

    def get_param_info(self, param):
        assert isinstance(param, core.BlobReference)
        return self._parameters_info.get(param, None)

    # This method is deprecated, use create_param method which
    # also does parameter initialization when needed
    def add_param_DEPRECATED(self, param, key=None, shape=None, length=None):
        logging.warning("add_param method is DEPRECATED")
        self._update_param_info_deprecated()
        if key is not None and self.net.input_record() is not None:
            idx = self.net.input_record().field_blobs().index(key)
            key = self.net.input_record().field_names()[idx]
        shape = shape if shape is not None else self._infer_param_shape(param)
        self.params.append(param)
        if not isinstance(param, core.BlobReference):
            raise ValueError("Param %s must be a BlobReference!" % str(param))
        self._param_info_deprecated.append(parameter_info.ParameterInfo(
            param_id=len(self._param_info_deprecated),
            param=param,
            shape=shape,
            key=key,
            length=length,
        ))
        return self._param_info_deprecated[-1]

    # This method is deprecated, use get_param_info method
    def param_info(self, grad_type=None, id=None):
        logging.info("param_info method is DEPRECATED")
        self._update_param_info_deprecated()
        if id is not None:
            assert grad_type is None
            info = self._param_info_deprecated[id]
            assert info.param_id == id
            return info
        elif grad_type is not None:
            return [
                info for info in self._param_info_deprecated
                if info.grad_type() == grad_type]
        else:
            return self._param_info_deprecated

    def GetParams(self, namescope=None, top_scope=False):
        '''
        Returns the params in current namescope
        '''
        if namescope is None:
            namescope = scope.CurrentNameScope()
        else:
            if not namescope.endswith(scope._NAMESCOPE_SEPARATOR):
                namescope += scope._NAMESCOPE_SEPARATOR

        if namescope == '':
            return self.params[:]
        elif top_scope:
            return [
                p for p in self.params
                if p.GetNameScope().startswith(namescope)
            ]
        else:
            return [p for p in self.params if
                    p.GetNameScope().startswith(namescope)]

    def Proto(self):
        return self.net.Proto()

    def InitProto(self):
        return self.param_init_net.Proto()

    def RunAllOnGPU(self, *args, **kwargs):
        self.param_init_net.RunAllOnGPU(*args, **kwargs)
        self.net.RunAllOnGPU(*args, **kwargs)

    def CreateDB(self, blob_out, db, db_type, **kwargs):
        dbreader = self.param_init_net.CreateDB(
            [], blob_out, db=db, db_type=db_type, **kwargs)
        return dbreader

    def AddGradientOperators(self, *args, **kwargs):
        if self.gradient_ops_added:
            raise RuntimeError("You cannot run AddGradientOperators twice.")
        self.gradient_ops_added = True
        self.grad_map = self.net.AddGradientOperators(*args, **kwargs)
        self.param_to_grad = self.get_param_to_grad(self.params)

        # Populate ParameterInfo for all parameters if missing
        # and add gradient blob information. So optimizers can use it
        for param, grad in self.param_to_grad.items():
            param_info = self.get_param_info(param)
            if param_info:
                param_info.grad = grad
            else:
                self._parameters_info[param] = parameter_info.ParameterInfo(
                    param_id=None,
                    param=param,
                    grad=grad,
                )

        return self.grad_map

    def get_param_to_grad(self, params):
        '''
        Given a list of parameters returns a dict from a parameter
        to a corresponding gradient
        '''

        param_to_grad = {}
        if not self.gradient_ops_added:
            raise RuntimeError("You need to run AddGradientOperators first.")
        # We need to use empty namescope when creating the gradients
        # to prevent duplicating the namescope prefix for gradient blobs.
        for p in params:
            if str(p) in self.grad_map:
                param_to_grad[p] = self.grad_map[str(p)]
        return param_to_grad

    def GetOptimizationParamInfo(self, params=None):
        '''
        Returns a map for param => grad.
        If params is not specified, all parameters will be considered.
        '''
        if not self.gradient_ops_added:
            raise RuntimeError("Need to call AddGradientOperators first")

        param_to_grad = self.param_to_grad
        if params:
            param_to_grad = self.get_param_to_grad(params)

        return [
            self.get_param_info(param) for param, grad in param_to_grad.items()
            if (
                not self.skip_sparse_optim or
                not isinstance(grad, core.GradientSlice)
            )
        ]


    def GetComputedParams(self, namescope=None):
        '''
        Returns the computed params in current namescope. 'Computed params'
        are such parameters that are not optimized via gradient descent but are
        directly computed from data, such as the running mean and variance
        of Spatial Batch Normalization.
        '''
        if namescope is None:
            namescope = scope.CurrentNameScope()
        else:
            if not namescope.endswith(scope._NAMESCOPE_SEPARATOR):
                namescope += scope._NAMESCOPE_SEPARATOR

        if namescope == '':
            return self.computed_params[:]
        else:
            return [p for p in self.computed_params
                    if p.GetNameScope() == namescope]

    def GetAllParams(self, namescope=None):
        return self.GetParams(namescope) + self.GetComputedParams(namescope)

    def TensorProtosDBInput(
        self, unused_blob_in, blob_out, batch_size, db, db_type, **kwargs
    ):
        """TensorProtosDBInput."""
        dbreader_name = "dbreader_" + db
        dbreader = self.param_init_net.CreateDB(
            [], dbreader_name,
            db=db, db_type=db_type)
        return self.net.TensorProtosDBInput(
            dbreader, blob_out, batch_size=batch_size)

    def GetDevices(self):
        assert len(self._devices) > 0, \
            "Use data_parallel_model to run model on multiple GPUs."
        return self._devices

    def __getattr__(self, op_type):
        """Catch-all for all other operators, mostly those without params."""
        if op_type.startswith('__'):
            raise AttributeError(op_type)

        if not core.IsOperator(op_type):
            raise RuntimeError(
                'Method ' + op_type + ' is not a registered operator.' +
                ' Did you mean: [' +
                ','.join(workspace.C.nearby_opnames(op_type)) + ']'
            )
        if op_type not in _known_working_ops:
            if not self.allow_not_known_ops:
                raise RuntimeError(
                    "Operator {} is not known to be safe".format(op_type))

            logging.warning("You are creating an op that the ModelHelper "
                            "does not recognize: {}.".format(op_type))
        return self.net.__getattr__(op_type)

    def __dir__(self):
        return sorted(set(
            dir(type(self)) +
            self.__dict__.keys() +
            _known_working_ops))


def ExtractPredictorNet(
    net_proto,
    input_blobs,
    output_blobs,
    device=None,
    renames=None,
    disabled_inputs=None
):
    '''
    Takes a model net for training and returns a net which can be
    used for prediction. For example, all gradient operators and
    input operators are removed.
    @param net_proto protobuf of the net you want to process (net.Proto())
    @param input_blobs list/set of blob names that are the inputs of predictor
    @param output_blobs list/set of blob names that are outputs of predictor
    @param device optional device option that is assigned
    @param renames dictionary of blob name to a new name (optional)
    @param disabled_inputs optional set of blobs that are 'switched off'. This
                will cause branches with those blobs as inputs to be removed
    '''
    predict_net = core.Net(net_proto.name + "_predict")
    predict_proto = predict_net.Proto()

    orig_external_inputs = set(net_proto.external_input)
    orig_external_outputs = set(net_proto.external_output)
    input_blobs = {str(b) for b in input_blobs}
    known_blobs = set(orig_external_inputs).union(input_blobs)
    output_blobs = {str(b) for b in output_blobs}
    external_inputs = set(input_blobs)
    external_outputs = set(output_blobs)

    if disabled_inputs is not None:
        known_blobs = known_blobs - set(disabled_inputs)

    ops = list(net_proto.op)

    # Find the range of ops that we should include
    try:
        first_op_with_input = min(
            [
                j for j in range(len(ops))
                if input_blobs.intersection(ops[j].input) and ops[j].type !=
                'StopGradient'
            ]
        )
    except ValueError:
        raise Exception("No ops with input={}".format(input_blobs))
    try:
        last_op_with_output = max(
            [
                j for j in range(len(ops))
                if output_blobs.intersection(ops[j].output)
            ]
        )
    except ValueError:
        raise Exception("No ops with output={}".format(output_blobs))

    def validate_op(op):
        # Check that the op does not have is_test = 0 set. This is a common
        # pitfall with SpatialBN op, at lest.
        for arg in op.arg:
            if arg.name == "is_test" and arg.i == 0:
                raise Exception(
                    "A operator had is_test=0, did you try to extract a " +
                    "predictor from a train model (instead of test model)?" +
                    " Op was: {}".format(str(op))
                )

    # Iterate through the ops and only include those whose inputs
    # we can satisfy.
    for op in ops[first_op_with_input:(last_op_with_output + 1)]:
        if known_blobs.issuperset(op.input):
            if device is not None:
                op.device_option.device_type = device.device_type
                op.device_option.cuda_gpu_id = device.cuda_gpu_id
            validate_op(op)
            predict_proto.op.extend([op])
            known_blobs.update(op.output)
            external_inputs.update(
                set(op.input).intersection(orig_external_inputs)
            )
            external_outputs.update(
                set(op.output).intersection(orig_external_outputs)
            )
        else:
            logging.debug(
                "Op {} had unknown inputs: {}".format(
                    op.type, set(op.input).difference(known_blobs)
                )
            )

    def rename_list(proto_list):
        if renames is None:
            return

        # proto lists don't support assignments
        new_list = proto_list[:]
        for j, b in enumerate(new_list):
            if b in renames:
                new_list[j] = renames[b]

        del proto_list[:]
        proto_list.extend(new_list)

    # Predictor net's external inputs and outputs include only those
    # that are part of this net.
    predict_proto.external_input.extend(external_inputs)
    predict_proto.external_output.extend(external_outputs)

    rename_list(predict_proto.external_input)
    rename_list(predict_proto.external_output)

    for op in predict_proto.op:
        rename_list(op.input)
        rename_list(op.output)

    return predict_net
