from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import numpy as np
import unittest

from caffe2.proto import caffe2_pb2
from caffe2.python import core, workspace, test_util, model_helper, brew


class TestShapeInference(test_util.TestCase):

    def testShapeInferenceSimpleFC(self):
        m = model_helper.ModelHelper(name="test_model")

        brew.fc(m, "data", "fc1", dim_in=96, dim_out=32)
        brew.fc(m, "fc1", "fc2", dim_in=32, dim_out=55)

        (shapes, types) = workspace.InferShapesAndTypes(
            [m.param_init_net, m.net],
            {'data': [64, 96]}
        )

        self.assertEquals(shapes['data'], [64, 96])
        self.assertEquals(shapes['fc1_w'], [32, 96])
        self.assertEquals(shapes['fc1_b'], [32])
        self.assertEquals(shapes['fc1'], [64, 32])
        self.assertEquals(shapes['fc2_w'], [55, 32])
        self.assertEquals(shapes['fc2_b'], [55])
        self.assertEquals(shapes['fc2'], [64, 55])

    def testShapeInferenceDistances(self):
        model = model_helper.ModelHelper(name="test_model")
        model.SquaredL2Distance(["x", "y"], "zsq")
        model.CosineSimilarity(["x", "y"], "zcos")
        model.DotProduct(["x", "y"], "zdot")

        workspace.FeedBlob("x", np.random.rand(10).astype(np.float32))
        workspace.FeedBlob("y", np.random.rand(10).astype(np.float32))
        self.InferTensorRunAndCompare(model)

    def testShapeInferenceConvNet(self):
        model = model_helper.ModelHelper(name="convtest")
        model.NHWC2NCHW("data", "data_nchw")
        brew.conv(model, "data_nchw", 'conv1', 3, 64,
                   weight_init=("MSRAFill", {}), kernel=7,
                   stride=2, pad=3, no_bias=0)
        brew.spatial_bn(model, 'conv1', 'conv1_spatbn_relu', 64, epsilon=1e-3)
        brew.relu(model, 'conv1_spatbn_relu', 'conv1_spatbn_relu')
        brew.max_pool(model, 'conv1_spatbn_relu', 'pool1', kernel=3, stride=2)
        brew.fc(model, 'pool1', 'fc', dim_in=(64 * 56 * 56), dim_out=100)
        model.Sigmoid('fc', 'fc_sigm')
        brew.softmax(model, 'fc_sigm', 'softmax')
        model.LabelCrossEntropy(['softmax', 'label'], 'xent')
        loss = model.AveragedLoss('xent', 'loss')

        model.AddGradientOperators([loss])

        LR = model.param_init_net.ConstantFill(
            [], 'LR', shape=[1], value=0.1
        )

        for param in model.GetParams():
            param_grad = model.param_to_grad[param]
            param_momentum = model.param_init_net.ConstantFill(
                [param], param + '_momentum', value=0.0
            )
            model.net.MomentumSGDUpdate(
                [param_grad, param_momentum, LR, param],
                [param_grad, param_momentum, param],
            )

        workspace.FeedBlob(
            "data",
            np.random.rand(16, 227, 227, 3).astype(np.float32),
        )
        workspace.FeedBlob(
            "label",
            (100 * np.random.rand(16)).astype(np.int32),
        )
        workspace.FeedBlob(
            "label",
            (100 * np.random.rand(16)).astype(np.int32),
        )
        # Then do automatic comparison test: run the next once to
        # initialize everything
        self.InferTensorRunAndCompare(model)

    def testShapeInferenceTranspose(self):
        model = model_helper.ModelHelper(name="test_model")

        workspace.FeedBlob(
            "tensor",
            np.random.rand(4, 2, 3, 3, 5).astype(np.float32)
        )

        # Testing with axes undefined
        brew.transpose(
            model,
            ["tensor"],
            "transpose",
        )
        self.InferTensorRunAndCompare(model)

        # Testing with axes defined
        brew.transpose(
            model,
            ["tensor"],
            "transpose",
            axes=np.random.permutation(5)
        )

        return self.InferTensorRunAndCompare(model)

    def testShapeInferencePad(self):
        model = model_helper.ModelHelper(name="padtest")
        model.PadImage("data", 'padded', pad_t=100, pad_l=37, pad_b=28,
                       pad_r=20, mode="constant", order="NCHW")

        workspace.FeedBlob(
            "data",
            np.random.rand(16, 3, 228, 228).astype(np.float32),
        )

        self.InferTensorRunAndCompare(model)

    def testShapeInferenceTwoClass(self):
        model = model_helper.ModelHelper(name="twoclass")
        model.MakeTwoClass("v", "v2")
        workspace.FeedBlob("v", np.random.rand(32).astype(np.float32))
        self.InferTensorRunAndCompare(model)

    def testShapeInferencePadZero(self):
        model = model_helper.ModelHelper(name="padtest")
        model.PadImage("data", 'padded', pad=0, mode="constant",
                       order="NCHW")

        workspace.FeedBlob(
            "data",
            np.random.rand(16, 3, 228, 228).astype(np.float32),
        )

        self.InferTensorRunAndCompare(model)

    def testShapeInferenceMatMul(self):
        model = model_helper.ModelHelper(name="test_model")

        model.MatMul(["x", "y"], "MatMul")

        workspace.FeedBlob("x", np.random.rand(10, 5).astype(np.float32))
        workspace.FeedBlob("y", np.random.rand(5, 10).astype(np.float32))

        self.InferTensorRunAndCompare(model)

    def testShapeInferenceSoftmaxWithLoss(self):
        model = model_helper.ModelHelper(name="test_model")

        model.SoftmaxWithLoss(
            ["logits", "labels"],
            ["softmax", "loss"],
        )

        # 2D Shape of [batch_size, num_classes]
        workspace.FeedBlob(
            "logits",
            np.random.rand(4, 3).astype(np.float32),
        )

        # Shape of size batch_size with all values [0, num_classes)
        workspace.FeedBlob(
            "labels",
            np.random.randint(low=0, high=3, size=(4, 1)).astype(np.int32),
        )
        self.InferTensorRunAndCompare(model)

        # Testing with 1D labels arg
        workspace.FeedBlob(
            "logits",
            np.random.rand(4, 3).astype(np.float32),
        )

        workspace.FeedBlob(
            "labels",
            np.random.randint(low=0, high=3, size=4).astype(np.int32),
        )
        self.InferTensorRunAndCompare(model)

        # Testing with weight_tensor
        model.SoftmaxWithLoss(
            ["logits", "labels", "weight_tensor"],
            ["softmax", "loss"],
        )

        workspace.FeedBlob(
            "logits",
            np.random.rand(4, 3).astype(np.float32),
        )

        workspace.FeedBlob(
            "labels",
            np.random.randint(low=0, high=3, size=4).astype(np.int32),
        )

        workspace.FeedBlob(
            "weight_tensor",
            np.random.rand(4).astype(np.float32),
        )
        self.InferTensorRunAndCompare(model)

        # Test spatial model
        model = model_helper.ModelHelper(name="test_model")
        workspace.FeedBlob(
            "img",
            np.random.rand(32, 19, 33, 28).astype(np.float32)
        )
        workspace.FeedBlob(
            "img_labels",
            (np.random.rand(32, 33, 28) * 19).astype(np.int32)
        )
        model.SpatialSoftmaxWithLoss(
            ["img", "img_labels"],
            ["softmax_img", "loss"],
        )
        self.InferTensorRunAndCompare(model)

    def testShapeInferenceIm2Col(self):
        # Test with NCHW
        model = model_helper.ModelHelper(name="test_model")
        model.Im2Col("X", "Y", pad=1, kernel=4, dilation=2, stride=2,
                     order="NCHW")

        workspace.FeedBlob(
            "X",
            np.random.rand(16, 3, 228, 228).astype(np.float32),
        )

        self.InferTensorRunAndCompare(model)

        # Test with NHWC
        model = model_helper.ModelHelper(name="test_model")
        model.Im2Col("X", "Y", pad=1, kernel=4, dilation=2, stride=2,
                     order="NHWC")

        workspace.FeedBlob(
            "X",
            np.random.rand(16, 228, 228, 3).astype(np.float32),
        )

        self.InferTensorRunAndCompare(model)

        # Test with different width and height
        model = model_helper.ModelHelper(name="test_model")
        model.Im2Col("X", "Y", pad=1, kernel_h=8, kernel_w=4,
                     dilation=2, stride=2)

        workspace.FeedBlob(
            "X",
            np.random.rand(16, 3, 228, 114).astype(np.float32),
        )

        self.InferTensorRunAndCompare(model)

    def testShapeInferenceTile(self):
        m = model_helper.ModelHelper(name="test_model")

        workspace.FeedBlob(
            "tensor",
            np.random.rand(4, 2, 3, 3, 5).astype(np.float32)
        )

        # Testing with axes undefined
        for i in range(0, 4):
            m.net.Tile(
                "tensor", "tiled_tensor_{}".format(i), tiles=5, axis=i)
        self.InferTensorRunAndCompare(m)

    def testShapeInferenceFlatten(self):
        model = model_helper.ModelHelper(name="test_model")
        model.FlattenToVec("X", "FlatVec")
        model.FlattenToVec("empty", "EmptyFlatVec")
        workspace.FeedBlob("X", np.random.rand(17, 5, 13).astype(np.float32))
        workspace.FeedBlob("empty", np.random.rand(0, 2, 3).astype(np.float32))

        self.InferTensorRunAndCompare(model)

        model = model_helper.ModelHelper(name="test_model")
        model.Flatten("X", "Flat")
        model.Flatten("empty", "EmptyFlat")
        workspace.FeedBlob("X", np.random.rand(17, 5, 13).astype(np.float32))
        workspace.FeedBlob("empty", np.random.rand(0, 2, 3).astype(np.float32))

        self.InferTensorRunAndCompare(model)

    def testShapeInferenceReshape(self):
        model = model_helper.ModelHelper(name="test_model")
        model.Reshape("X", ["Reshaped", "Old_Shape"], shape=[8, 0, -1, 2])
        workspace.FeedBlob("X", np.random.rand(4, 26, 32).astype(np.float32))

        self.InferTensorRunAndCompare(model)

    def testSqueeze(self):
        net = core.Net("sq")
        net.Squeeze(["data"], ["data_squeezed"], dims=[3, 1])
        (shapes, types) = workspace.InferShapesAndTypes(
            [net],
            {'data': [64, 1, 96, 1, 4]}
        )
        self.assertEqual(shapes['data_squeezed'], [64, 96, 4])

    def testCast(self):
        model = model_helper.ModelHelper(name="test_model")

        types = [
            ('bool', np.bool, caffe2_pb2.TensorProto.BOOL),
            #('byte', None, caffe2_pb2.TensorProto.BYTE),
            ('int8', np.int8, caffe2_pb2.TensorProto.INT8),
            ('uint8', np.uint8, caffe2_pb2.TensorProto.UINT8),
            ('int16', np.int16, caffe2_pb2.TensorProto.INT16),
            ('uint16', np.uint16, caffe2_pb2.TensorProto.UINT16),
            #('float16', np.float16, caffe2_pb2.TensorProto.FLOAT16),
            ('int32', np.int32, caffe2_pb2.TensorProto.INT32),
            ('float', np.float32, caffe2_pb2.TensorProto.FLOAT),
            ('int64', np.int64, caffe2_pb2.TensorProto.INT64),
            ('double', np.float64, caffe2_pb2.TensorProto.DOUBLE),
            #('string', None, caffe2_pb2.TensorProto.STRING),
        ]

        for (xstr, xnp, _) in types:
            xname = 'X%s' % xstr
            workspace.FeedBlob(xname, np.random.rand(1).astype(xnp))
            for (ystr, _, yc2) in types:
                yname = 'Y%s_to_%s' % (xstr, ystr)
                model.Cast(xname, yname, to=yc2)

        self.InferTensorRunAndCompare(model)

    def testShapeInferenceRoiPool(self):
        for is_test in [True, False]:
            model = model_helper.ModelHelper(name="test_model")
            outputs = ['Y'] if is_test else ['Y', 'argmaxes']
            model.net.RoIPool(
                ['X', 'R'], outputs, pooled_h=4, pooled_w=5, is_test=is_test)
            workspace.FeedBlob(
                "X",
                np.random.rand(100, 3, 4, 5).astype(np.float32))
            workspace.FeedBlob(
                "R",
                np.random.rand(2, 5).astype(np.float32))
            self.InferTensorRunAndCompare(model)

    def InferTensorRunAndCompare(self, model):
        '''
        Runs shape inference, and then the model to check
        that the inferred shapes agree with the actual ones
        '''
        (shapes, types) = workspace.InferShapesAndTypes(
            [model.param_init_net, model.net],
        )

        # .. Create net
        workspace.RunNetOnce(model.param_init_net)
        workspace.CreateNet(model.net, True)
        workspace.RunNet(model.Proto().name)

        # ... and then check the shapes mismatch
        correct_shapes = {}
        correct_types = {}
        for b in workspace.Blobs():
            arr = workspace.FetchBlob(b)
            correct_shapes[b] = arr.shape
            if type(arr) is np.ndarray:
                if arr.dtype == np.dtype('float32'):
                    correct_types[b] = caffe2_pb2.TensorProto.FLOAT
                elif arr.dtype == np.dtype('int32'):
                    correct_types[b] = caffe2_pb2.TensorProto.INT32
                # BYTE
                # STRING
                elif arr.dtype == np.dtype('bool'):
                    correct_types[b] = caffe2_pb2.TensorProto.BOOL
                elif arr.dtype == np.dtype('uint8'):
                    correct_types[b] = caffe2_pb2.TensorProto.UINT8
                elif arr.dtype == np.dtype('int8'):
                    correct_types[b] = caffe2_pb2.TensorProto.INT8
                elif arr.dtype == np.dtype('uint16'):
                    correct_types[b] = caffe2_pb2.TensorProto.UINT16
                elif arr.dtype == np.dtype('int16'):
                    correct_types[b] = caffe2_pb2.TensorProto.INT16
                elif arr.dtype == np.dtype('int64'):
                    correct_types[b] = caffe2_pb2.TensorProto.INT64
                elif arr.dtype == np.dtype('float16'):
                    correct_types[b] = caffe2_pb2.TensorProto.FLOAT16
                elif arr.dtype == np.dtype('float64'):
                    correct_types[b] = caffe2_pb2.TensorProto.DOUBLE
                else:
                    correct_types[b] = "unknown {}".format(arr.dtype)
            else:
                correct_types[b] = str(type(arr))

        for b in correct_shapes:
            self.assertTrue(
                np.array_equal(
                    np.array(shapes[b]).astype(np.int32),
                    np.array(correct_shapes[b]).astype(np.int32)
                ),
                "Shape {} mismatch: {} vs. {}".format(
                    b, shapes[b], correct_shapes[b]
                )
            )
            self.assertFalse(
                b not in types and b in correct_types,
                "Type for {} not defined".format(b),
            )
            self.assertEqual(
                types[b],
                correct_types[b],
                "Type {} mismatch: {} vs. {}".format(
                    b, types[b], correct_types[b],
                )
            )


if __name__ == "__main__":
    unittest.main()
