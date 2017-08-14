## @package caffe_translator
# Module caffe2.python.caffe_translator
#!/usr/bin/env python2

import argparse
import copy
import logging
import numpy as np  # noqa

from caffe2.proto import caffe2_pb2, caffe2_legacy_pb2
from caffe.proto import caffe_pb2
from caffe2.python import core, utils, workspace
from google.protobuf import text_format

logging.basicConfig()
log = logging.getLogger("caffe_translator")
log.setLevel(logging.INFO)


def _StateMeetsRule(state, rule):
    """A function that reproduces Caffe's StateMeetsRule functionality."""
    if rule.HasField('phase') and rule.phase != state.phase:
        return False
    if rule.HasField('min_level') and state.level < rule.min_level:
        return False
    if rule.HasField('max_level') and state.level > rule.max_level:
        return False
    curr_stages = set(list(state.stage))
    # all stages in rule.stages should be in, otherwise it's not a match.
    if len(rule.stage) and any([s not in curr_stages for s in rule.stage]):
        return False
    # none of the stage in rule.stages should be in, otherwise it's not a match.
    if len(rule.not_stage) and any([s in curr_stages for s in rule.not_stage]):
        return False
    # If none of the nonmatch happens, return True.
    return True


def _ShouldInclude(net_state, layer):
    """A function that reproduces Caffe's inclusion and exclusion rule."""
    ret = (len(layer.include) == 0)
    # check exclude rules: if any exclusion is met, we shouldn't include.
    ret &= not any([_StateMeetsRule(net_state, rule) for rule in layer.exclude])
    if len(layer.include):
        # check include rules: if any inclusion is met, we should include.
        ret |= any([_StateMeetsRule(net_state, rule) for rule in layer.include])
    return ret


class TranslatorRegistry(object):
    registry_ = {}

    @classmethod
    def Register(cls, op_name):
        """A decorator for registering gradient mappings."""

        def Wrapper(func):
            cls.registry_[op_name] = func
            return func

        return Wrapper

    @classmethod
    def TranslateLayer(cls, layer, pretrained_blobs, is_test):
        try:
            caffe_ops, params = cls.registry_[layer.type](
                layer, pretrained_blobs, is_test)
        except KeyError:
            raise KeyError('No translator registered for layer: %s yet.' %
                           str(layer))
        if caffe_ops is None:
            caffe_ops = []
        if type(caffe_ops) is not list:
            caffe_ops = [caffe_ops]
        return caffe_ops, params

    @classmethod
    def TranslateModel(
        cls,
        caffe_net,
        pretrained_net,
        is_test=False,
        net_state=None,
    ):
        net_state = caffe_pb2.NetState() if net_state is None else net_state
        net = caffe2_pb2.NetDef()
        net.name = caffe_net.name
        net_params = caffe2_pb2.TensorProtos()
        if len(caffe_net.layers) > 0:
            raise ValueError(
                'I think something is wrong. This translation script '
                'only accepts new style layers that are stored in the '
                'layer field.'
            )
        for layer in caffe_net.layer:
            if not _ShouldInclude(net_state, layer):
                log.info('Current net state does not need layer {}'
                            .format(layer.name))
                continue
            log.info('Translate layer {}'.format(layer.name))
            # Get pretrained one
            pretrained_layers = (
                [l for l in pretrained_net.layer
                 if l.name == layer.name] + [l
                                             for l in pretrained_net.layers
                                             if l.name == layer.name]
            )
            if len(pretrained_layers) > 1:
                raise ValueError(
                    'huh? more than one pretrained layer of one name?')
            elif len(pretrained_layers) == 1:
                pretrained_blobs = [
                    utils.CaffeBlobToNumpyArray(blob)
                    for blob in pretrained_layers[0].blobs
                ]
            else:
                # No pretrained layer for the given layer name. We'll just pass
                # no parameter blobs.
                # print 'No pretrained layer for layer', layer.name
                pretrained_blobs = []
            operators, params = cls.TranslateLayer(
                layer, pretrained_blobs, is_test)
            net.op.extend(operators)
            net_params.protos.extend(params)
        return net, net_params


def TranslateModel(*args, **kwargs):
    return TranslatorRegistry.TranslateModel(*args, **kwargs)


def ConvertTensorProtosToInitNet(net_params, input_name):
    """Takes the net_params returned from TranslateModel, and wrap it as an
    init net that contain GivenTensorFill.

    This is a very simple feature that only works with float tensors, and is
    only intended to be used in an environment where you want a single
    initialization file - for more complex cases, use a db to store the
    parameters.
    """
    init_net = caffe2_pb2.NetDef()
    for tensor in net_params.protos:
        if len(tensor.float_data) == 0:
            raise RuntimeError(
                "Only float tensors are supported in this util.")
        op = core.CreateOperator(
            "GivenTensorFill", [], [tensor.name],
            arg=[
                utils.MakeArgument("shape", list(tensor.dims)),
                utils.MakeArgument("values", tensor.float_data)])
        init_net.op.extend([op])
    init_net.op.extend([core.CreateOperator("ConstantFill", [], [input_name], shape=[1])])
    return init_net


def BaseTranslate(layer, caffe2_type):
    """A simple translate interface that maps the layer input and output."""
    caffe2_op = caffe2_pb2.OperatorDef()
    caffe2_op.type = caffe2_type
    caffe2_op.input.extend(layer.bottom)
    caffe2_op.output.extend(layer.top)
    return caffe2_op


def AddArgument(op, key, value):
    """Makes an argument based on the value type."""
    op.arg.extend([utils.MakeArgument(key, value)])

################################################################################
# Common translators for layers.
################################################################################


@TranslatorRegistry.Register("Input")
def TranslateInput(layer, pretrained_blobs, is_test):
    return [], []


@TranslatorRegistry.Register("VideoData")
def TranslateVideoData(layer, pretrained_blobs, is_test):
    return [], []


@TranslatorRegistry.Register("Data")
def TranslateData(layer, pretrained_blobs, is_test):
    return [], []


# A function used in convolution, pooling and deconvolution to deal with
# conv pool specific parameters.
def _TranslateStridePadKernelHelper(param, caffe_op):
    try:
        if (len(param.stride) > 1 or len(param.kernel_size) > 1 or
                len(param.pad) > 1):
            raise NotImplementedError(
                "Translator currently does not support non-conventional "
                "pad/kernel/stride settings."
            )
        stride = param.stride[0] if len(param.stride) else 1
        pad = param.pad[0] if len(param.pad) else 0
        kernel = param.kernel_size[0] if len(param.kernel_size) else 0
    except TypeError:
        # This catches the case of a PoolingParameter, in which case we are
        # having non-repeating pad, stride and kernel.
        stride = param.stride
        pad = param.pad
        kernel = param.kernel_size
    # Get stride
    if param.HasField("stride_h") or param.HasField("stride_w"):
        AddArgument(caffe_op, "stride_h", param.stride_h)
        AddArgument(caffe_op, "stride_w", param.stride_w)
    else:
        AddArgument(caffe_op, "stride", stride)
    # Get pad
    if param.HasField("pad_h") or param.HasField("pad_w"):
        if param.pad_h == param.pad_w:
            AddArgument(caffe_op, "pad", param.pad_h)
        else:
            AddArgument(caffe_op, "pad_t", param.pad_h)
            AddArgument(caffe_op, "pad_b", param.pad_h)
            AddArgument(caffe_op, "pad_l", param.pad_w)
            AddArgument(caffe_op, "pad_r", param.pad_w)
    else:
        AddArgument(caffe_op, "pad", pad)
    # Get kernel
    if param.HasField("kernel_h") or param.HasField("kernel_w"):
        AddArgument(caffe_op, "kernel_h", param.kernel_h)
        AddArgument(caffe_op, "kernel_w", param.kernel_w)
    else:
        AddArgument(caffe_op, "kernel", kernel)


@TranslatorRegistry.Register("Convolution3D")
def TranslateConvNd(layer, pretrained_blobs, is_test):
    param = layer.convolution3d_param
    caffe_op = BaseTranslate(layer, "Conv")
    output = caffe_op.output[0]
    caffe_op.input.append(output + '_w')

    AddArgument(
        caffe_op,
        "kernels",
        [param.kernel_depth, param.kernel_size, param.kernel_size])
    AddArgument(
        caffe_op,
        "strides",
        [param.temporal_stride, param.stride, param.stride])
    temporal_pad = 0
    spatial_pad = 0
    if hasattr(param, 'temporal_pad'):
        temporal_pad = param.temporal_pad
    if hasattr(param, 'pad'):
        spatial_pad = param.pad
    AddArgument(caffe_op, "pads", [temporal_pad, spatial_pad, spatial_pad] * 2)

    # weight
    params = [
        utils.NumpyArrayToCaffe2Tensor(pretrained_blobs[0], output + '_w')]
    # bias
    if len(pretrained_blobs) == 2:
        caffe_op.input.append(output + '_b')
        params.append(
            utils.NumpyArrayToCaffe2Tensor(
                pretrained_blobs[1].flatten(), output + '_b'))
    return caffe_op, params


@TranslatorRegistry.Register("Convolution")
def TranslateConv(layer, pretrained_blobs, is_test):
    param = layer.convolution_param
    caffe_op = BaseTranslate(layer, "Conv")
    output = caffe_op.output[0]
    caffe_op.input.append(output + '_w')
    _TranslateStridePadKernelHelper(param, caffe_op)
    # weight
    params = [
        utils.NumpyArrayToCaffe2Tensor(pretrained_blobs[0], output + '_w')]
    # bias
    if len(pretrained_blobs) == 2:
        caffe_op.input.append(output + '_b')
        params.append(
            utils.NumpyArrayToCaffe2Tensor(
                pretrained_blobs[1].flatten(), output + '_b'))
    # Group convolution option
    if param.group != 1:
        AddArgument(caffe_op, "group", param.group)
    # Get dilation - not tested. If you have a model and this checks out,
    # please provide a test and uncomment this.
    if len(param.dilation) > 0:
        if len(param.dilation) == 1:
            AddArgument(caffe_op, "dilation", param.dilation[0])
        elif len(param.dilation) == 2:
            AddArgument(caffe_op, "dilation_h", param.dilation[0])
            AddArgument(caffe_op, "dilation_w", param.dilation[1])
    return caffe_op, params


@TranslatorRegistry.Register("Deconvolution")
def TranslateDeconv(layer, pretrained_blobs, is_test):
    param = layer.convolution_param
    if param.group > 1:
        raise NotImplementedError(
            "Translator currently does not support group deconvolution."
        )
    caffe_op = BaseTranslate(layer, "ConvTranspose")
    output = caffe_op.output[0]
    _TranslateStridePadKernelHelper(param, caffe_op)
    caffe_op.input.extend([output + '_w', output + '_b'])
    AddArgument(caffe_op, "order", "NCHW")
    weight = utils.NumpyArrayToCaffe2Tensor(pretrained_blobs[0], output + '_w')
    bias = utils.NumpyArrayToCaffe2Tensor(
        pretrained_blobs[1].flatten(), output + '_b'
    )
    return caffe_op, [weight, bias]


@TranslatorRegistry.Register("ReLU")
def TranslateRelu(layer, pretrained_blobs, is_test):
    return BaseTranslate(layer, "Relu"), []


@TranslatorRegistry.Register("Pooling")
def TranslatePool(layer, pretrained_blobs, is_test):
    param = layer.pooling_param
    if param.pool == caffe_pb2.PoolingParameter.MAX:
        caffe_op = BaseTranslate(layer, "MaxPool")
    elif param.pool == caffe_pb2.PoolingParameter.AVE:
        caffe_op = BaseTranslate(layer, "AveragePool")
    _TranslateStridePadKernelHelper(param, caffe_op)
    AddArgument(caffe_op, "order", "NCHW")
    try:
        # In the Facebook port of Caffe, a torch_pooling field was added to
        # map the pooling computation of Torch. Essentially, it uses
        #   floor((height + 2 * padding - kernel) / stride) + 1
        # instead of
        #   ceil((height + 2 * padding - kernel) / stride) + 1
        # which is Caffe's version.
        # Torch pooling is actually the same as Caffe2 pooling, so we don't
        # need to do anything.
        is_torch_pooling = param.torch_pooling
    except AttributeError:
        is_torch_pooling = False
    if not is_torch_pooling:
        AddArgument(caffe_op, "legacy_pad",
                    caffe2_legacy_pb2.CAFFE_LEGACY_POOLING)
    if param.global_pooling:
        AddArgument(caffe_op, "global_pooling", 1)
    return caffe_op, []


@TranslatorRegistry.Register("Pooling3D")
def TranslatePool3D(layer, pretrained_blobs, is_test):
    param = layer.pooling3d_param
    if param.pool == caffe_pb2.Pooling3DParameter.MAX:
        caffe_op = BaseTranslate(layer, "MaxPool")

    elif param.pool == caffe_pb2.Pooling3DParameter.AVE:
        caffe_op = BaseTranslate(layer, "AveragePool")
    AddArgument(caffe_op, "order", "NCHW")
    AddArgument(
        caffe_op,
        "kernels",
        [param.kernel_depth, param.kernel_size, param.kernel_size])

    AddArgument(
        caffe_op,
        "strides",
        [param.temporal_stride, param.stride, param.stride])
    temporal_pad = 0
    spatial_pad = 0
    if hasattr(param, 'temporal_pad'):
        temporal_pad = param.temporal_pad
    if hasattr(param, 'pad'):
        spatial_pad = param.pad
    AddArgument(caffe_op, "pads", [temporal_pad, spatial_pad, spatial_pad] * 2)
    return caffe_op, []


@TranslatorRegistry.Register("LRN")
def TranslateLRN(layer, pretrained_blobs, is_test):
    caffe_op = BaseTranslate(layer, "LRN")
    caffe_op.output.extend(['_' + caffe_op.output[0] + '_scale'])
    param = layer.lrn_param
    if param.norm_region != caffe_pb2.LRNParameter.ACROSS_CHANNELS:
        raise ValueError(
            "Does not support norm region other than across channels.")
    AddArgument(caffe_op, "size", int(param.local_size))
    AddArgument(caffe_op, "alpha", float(param.alpha))
    AddArgument(caffe_op, "beta", float(param.beta))
    AddArgument(caffe_op, "bias", float(param.k))
    AddArgument(caffe_op, "order", "NCHW")
    return caffe_op, []


@TranslatorRegistry.Register("InnerProduct")
def TranslateInnerProduct(layer, pretrained_blobs, is_test):
    param = layer.inner_product_param
    try:
        if param.axis != 1 or param.transpose:
            raise ValueError(
                "We don't have testing case for non-default axis and transpose "
                "cases yet so we are disabling it for now. If you have a model "
                "with this, please do send us your model for us to update this "
                "support, and you are more than welcome to send a PR for this.")
    except AttributeError:
        # We might be using an historic Caffe protobuf that does not have axis
        # and transpose arguments, so we will silently pass.
        pass
    caffe_op = BaseTranslate(layer, "FC")
    output = caffe_op.output[0]
    caffe_op.input.extend([output + '_w', output + '_b'])
    # To provide the old-style 4-dimensional blob (1, 1, dim_output, dim_input)
    # case, we always explicitly reshape the pretrained blob.
    if pretrained_blobs[0].ndim not in [2, 4]:
        raise ValueError("Unexpected weight ndim.")
    if (pretrained_blobs[0].ndim == 4 and
            list(pretrained_blobs[0].shape[:2]) != [1, 1]):
        raise ValueError(
            "If pretrained blob has 4 dims (old-style Caffe), the first two "
            "should be of value 1, but I got " + str(pretrained_blobs[0].shape))
    weight = utils.NumpyArrayToCaffe2Tensor(
        pretrained_blobs[0].reshape(-1, pretrained_blobs[0].shape[-1]),
        output + '_w'
    )
    bias = utils.NumpyArrayToCaffe2Tensor(
        pretrained_blobs[1].flatten(), output + '_b'
    )
    return caffe_op, [weight, bias]


@TranslatorRegistry.Register("Dropout")
def TranslateDropout(layer, pretrained_blobs, is_test):
    caffe_op = BaseTranslate(layer, "Dropout")
    caffe_op.output.extend(['_' + caffe_op.output[0] + '_mask'])
    param = layer.dropout_param
    AddArgument(caffe_op, "ratio", param.dropout_ratio)
    if (is_test):
        AddArgument(caffe_op, "is_test", 1)
    return caffe_op, []


@TranslatorRegistry.Register("Softmax")
def TranslateSoftmax(layer, pretrained_blobs, is_test):
    caffe_op = BaseTranslate(layer, "Softmax")
    return caffe_op, []


@TranslatorRegistry.Register("SoftmaxWithLoss")
def TranslateSoftmaxWithLoss(layer, pretrained_blobs, is_test):
    softmax_op = core.CreateOperator(
        "Softmax", [layer.bottom[0]],
        layer.bottom[0] + "_translator_autogen_softmax")
    xent_op = core.CreateOperator(
        "LabelCrossEntropy",
        [softmax_op.output[0], layer.bottom[1]],
        layer.bottom[0] + "_translator_autogen_xent")
    loss_op = core.CreateOperator(
        "AveragedLoss",
        xent_op.output[0],
        layer.top[0])
    return [softmax_op, xent_op, loss_op], []


@TranslatorRegistry.Register("Accuracy")
def TranslateAccuracy(layer, pretrained_blobs, is_test):
    caffe_op = BaseTranslate(layer, "Accuracy")
    if layer.accuracy_param.top_k != 1:
        AddArgument(caffe_op, "top_k", layer.accuracy_param.top_k)
    return caffe_op, []


@TranslatorRegistry.Register("Concat")
def TranslateConcat(layer, pretrained_blobs, is_test):
    caffe_op = BaseTranslate(layer, "Concat")
    caffe_op.output.extend(['_' + caffe_op.output[0] + '_dims'])
    AddArgument(caffe_op, "order", "NCHW")
    return caffe_op, []


@TranslatorRegistry.Register("TanH")
def TranslateTanH(layer, pretrained_blobs, is_test):
    caffe_op = BaseTranslate(layer, "Tanh")
    return caffe_op, []


@TranslatorRegistry.Register("InstanceNorm")
def TranslateInstanceNorm(layer, pretrained_blobs, is_test):
    caffe_op = BaseTranslate(layer, "InstanceNorm")
    output = caffe_op.output[0]
    weight = utils.NumpyArrayToCaffe2Tensor(
        pretrained_blobs[0].flatten(), output + '_w')
    bias = utils.NumpyArrayToCaffe2Tensor(
        pretrained_blobs[1].flatten(), output + '_b')
    caffe_op.input.extend([output + '_w', output + '_b'])
    AddArgument(caffe_op, "order", "NCHW")
    return caffe_op, [weight, bias]


@TranslatorRegistry.Register("BatchNorm")
def TranslateBatchNorm(layer, pretrained_blobs, is_test):
    caffe_op = BaseTranslate(layer, "SpatialBN")
    output = caffe_op.output[0]
    param = layer.batch_norm_param
    AddArgument(caffe_op, "is_test", is_test)
    AddArgument(caffe_op, "epsilon", param.eps)
    AddArgument(caffe_op, "order", "NCHW")

    caffe_op.input.extend(
        [output + "_scale",
         output + "_bias",
         output + "_mean",
         output + "_var"])
    if not is_test:
        caffe_op.output.extend(
            [output + "_mean",
             output + "_var",
             output + "_saved_mean",
             output + "_saved_var"])

    n_channels = pretrained_blobs[0].shape[0]
    if pretrained_blobs[2][0] != 0:
        mean = utils.NumpyArrayToCaffe2Tensor(
            (1. / pretrained_blobs[2][0]) * pretrained_blobs[0],
            output + '_mean')
        var = utils.NumpyArrayToCaffe2Tensor(
            (1. / pretrained_blobs[2][0]) * pretrained_blobs[1],
            output + '_var')
    else:
        raise RuntimeError("scalar is zero.")
    pretrained_blobs[2][0] = 1
    pretrained_blobs[2] = np.tile(pretrained_blobs[2], (n_channels, ))
    scale = utils.NumpyArrayToCaffe2Tensor(
        pretrained_blobs[2],
        output + '_scale')
    bias = utils.NumpyArrayToCaffe2Tensor(
        np.zeros_like(pretrained_blobs[2]),
        output + '_bias')

    return caffe_op, [scale, bias, mean, var]


@TranslatorRegistry.Register("Eltwise")
def TranslateElementWise(layer, pretrained_blobs, is_test):
    param = layer.eltwise_param
    # TODO(jiayq): if we have a protobuf that uses this, lift this constraint
    # and verify that we can correctly translate.
    if len(param.coeff) or param.operation != 1:
        raise RuntimeError("This eltwise layer is not yet supported.")
    caffe_op = BaseTranslate(layer, "Sum")
    return caffe_op, []


@TranslatorRegistry.Register("Scale")
def TranslateScale(layer, pretrained_blobs, is_test):
    mul_op = BaseTranslate(layer, "Mul")
    scale_param = layer.scale_param
    AddArgument(mul_op, "axis", scale_param.axis)
    AddArgument(mul_op, "broadcast", True)
    if len(mul_op.input) == 1:
        # the scale parameter is in pretrained blobs
        if scale_param.num_axes != 1:
            raise RuntimeError("This path has not been verified yet.")

        output = mul_op.output[0]
        mul_op_param = output + '_w'
        mul_op.input.append(mul_op_param)
        weights = []
        weights.append(utils.NumpyArrayToCaffe2Tensor(
            pretrained_blobs[0].flatten(), mul_op_param))

        add_op = None
        if len(pretrained_blobs) == 1:
            # No bias-term in Scale layer
            pass
        elif len(pretrained_blobs) == 2:
            # Caffe Scale layer supports a bias term such that it computes
            # (scale_param * X + bias), whereas Caffe2 Mul op doesn't.
            # Include a separate Add op for the bias followed by Mul.
            add_op = copy.deepcopy(mul_op)
            add_op.type = "Add"
            add_op_param = output + '_b'
            internal_blob = output + "_internal"
            del mul_op.output[:]
            mul_op.output.append(internal_blob)
            del add_op.input[:]
            add_op.input.append(internal_blob)
            add_op.input.append(add_op_param)
            weights.append(utils.NumpyArrayToCaffe2Tensor(
                pretrained_blobs[1].flatten(), add_op_param))
        else:
            raise RuntimeError("Unexpected number of pretrained blobs in Scale")

        caffe_ops = [mul_op]
        if add_op:
            caffe_ops.append(add_op)
        assert len(caffe_ops) == len(weights)
        return caffe_ops, weights
    elif len(mul_op.input) == 2:
        # TODO(jiayq): find a protobuf that uses this and verify.
        raise RuntimeError("This path has not been verified yet.")
    else:
        raise RuntimeError("Unexpected number of inputs.")


@TranslatorRegistry.Register("Reshape")
def TranslateReshape(layer, pretrained_blobs, is_test):
    caffe_op = BaseTranslate(layer, "Reshape")
    caffe_op.output.append("_" + caffe_op.input[0] + "_dims")
    reshape_param = layer.reshape_param
    AddArgument(caffe_op, 'shape', reshape_param.shape.dim)
    return caffe_op, []


@TranslatorRegistry.Register("Flatten")
def TranslateFlatten(layer, pretrained_blobs, is_test):
    param = layer.flatten_param
    if param.end_axis != -1:
        raise NotImplementedError("flatten_param.end_axis not supported yet.")

    if param.axis == 0:
        caffe_op = BaseTranslate(layer, "FlattenToVec")
    elif param.axis == 1:
        caffe_op = BaseTranslate(layer, "Flatten")
    else:
        # This could be a Reshape op, but dim size is not known here.
        raise NotImplementedError(
            "Not supported yet for flatten_param.axis {}.".format(param.axis))

    return caffe_op, []


@TranslatorRegistry.Register("Sigmoid")
def TranslateSigmoid(layer, pretrained_blobs, is_test):
    caffe_op = BaseTranslate(layer, "Sigmoid")
    return caffe_op, []


@TranslatorRegistry.Register("ROIPooling")
def TranslateROIPooling(layer, pretrained_blobs, is_test):
    caffe_op = BaseTranslate(layer, "RoIPool")
    AddArgument(caffe_op, "order", "NCHW")

    if is_test:
        AddArgument(caffe_op, "is_test", is_test)
    else:
        # Only used for gradient computation
        caffe_op.output.append(caffe_op.output[0] + '_argmaxes')

    param = layer.roi_pooling_param
    if param.HasField('pooled_h'):
        AddArgument(caffe_op, 'pooled_h', param.pooled_h)
    if param.HasField('pooled_w'):
        AddArgument(caffe_op, 'pooled_w', param.pooled_w)
    if param.HasField('spatial_scale'):
        AddArgument(caffe_op, 'spatial_scale', param.spatial_scale)

    return caffe_op, []


@TranslatorRegistry.Register("PReLU")
def TranslatePRelu(layer, pretrained_blobs, is_test):
    caffe_op = BaseTranslate(layer, "PRelu")
    output = caffe_op.output[0]
    caffe_op.input.extend([output + '_Slope'])
    slope = utils.NumpyArrayToCaffe2Tensor(pretrained_blobs[0], output + '_Slope')

    return caffe_op, [slope]


@TranslatorRegistry.Register("Reduction")
def TranslateReduction(layer, pretrained_blobs, is_test):
    param = layer.reduction_param
    if param.operation == caffe_pb2.ReductionParameter.SUM:
        caffe_op = BaseTranslate(layer, "ReduceBackSum")
    elif param.operation == caffe_pb2.ReductionParameter.MEAN:
        caffe_op = BaseTranslate(layer, "ReduceBackMean")
    else:
        raise NotImplementedError("Not yet supported")

    if param.axis > 0:
        # We can't figure out the number of dims to reduce from positive axis
        # for back reduction since the shape info is not known here.
        raise NotImplementedError("Not yet supported")
    num_reduce_dim = -param.axis
    AddArgument(caffe_op, "num_reduce_dim", num_reduce_dim)

    return caffe_op, []


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="Utilitity to convert pretrained caffe models to Caffe2 models.")
    parser.add_argument("prototext", help="Caffe prototext.")
    parser.add_argument("caffemodel", help="Caffe trained model.")
    parser.add_argument("--init_net", help="Caffe2 initialization net.", default="init_net.pb")
    parser.add_argument("--predict_net", help="Caffe2 prediction net.", default="predict_net.pb")
    args = parser.parse_args()

    caffenet = caffe_pb2.NetParameter()
    caffenet_pretrained = caffe_pb2.NetParameter()
    input_proto = args.prototext
    input_caffemodel = args.caffemodel
    output_init_net = args.init_net
    output_predict_net = args.predict_net

    text_format.Merge(
        open(input_proto, 'r').read(), caffenet
    )
    caffenet_pretrained.ParseFromString(
        open(input_caffemodel, 'rb').read()
    )
    net, pretrained_params = TranslateModel(
        caffenet, caffenet_pretrained, is_test=True
    )

    # Assume there is one input and one output
    external_input = net.op[0].input[0]
    external_output = net.op[-1].output[0]

    net.external_input.extend([external_input])
    net.external_input.extend([param.name for param in pretrained_params.protos])
    net.external_output.extend([external_output])
    init_net = ConvertTensorProtosToInitNet(pretrained_params, external_input)

    for param in pretrained_params.protos:
        workspace.FeedBlob(param.name, utils.Caffe2TensorToNumpyArray(param))
    with open(output_predict_net, 'wb') as f:
        f.write(net.SerializeToString())
    with open(output_init_net, 'wb') as f:
        f.write(init_net.SerializeToString())
