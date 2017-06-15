## @package optimizer_test_util
# Module caffe2.python.optimizer_test_util
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals
import numpy as np
from caffe2.python import core, workspace, cnn


class OptimizerTestBase(object):
    """
    This is an abstract base class.
    Don't inherit from unittest.TestCase, and don't name it 'Test*'.
    Do, however, do these things in classes which inherit from this.
    """

    def testDense(self):
        perfect_model = np.array([2, 6, 5, 0, 1]).astype(np.float32)
        np.random.seed(123)  # make test deterministic
        data = np.random.randint(
            2,
            size=(20, perfect_model.size)).astype(np.float32)
        label = np.dot(data, perfect_model)[:, np.newaxis]

        model = cnn.CNNModelHelper("NCHW", name="test")
        out = model.FC(
            'data', 'fc', perfect_model.size, 1, ('ConstantFill', {}),
            ('ConstantFill', {}), axis=0
        )
        sq = model.SquaredL2Distance([out, 'label'])
        loss = model.AveragedLoss(sq, "avg_loss")
        grad_map = model.AddGradientOperators([loss])
        self.assertIsInstance(grad_map['fc_w'], core.BlobReference)
        optimizer = self.build_optimizer(model)

        workspace.FeedBlob('data', data[0])
        workspace.FeedBlob('label', label[0])
        workspace.RunNetOnce(model.param_init_net)
        workspace.CreateNet(model.net, True)
        for _ in range(2000):
            idx = np.random.randint(data.shape[0])
            workspace.FeedBlob('data', data[idx])
            workspace.FeedBlob('label', label[idx])
            workspace.RunNet(model.net.Proto().name)

        np.testing.assert_allclose(
            perfect_model[np.newaxis, :],
            workspace.FetchBlob('fc_w'),
            atol=1e-2
        )
        self.check_optimizer(optimizer)

    def testSparse(self):
        # to test duplicated indices we assign two indices to each weight and
        # thus each weight might count once or twice
        DUPLICATION = 2
        perfect_model = np.array([2, 6, 5, 0, 1]).astype(np.float32)
        np.random.seed(123)  # make test deterministic
        data = np.random.randint(
            2,
            size=(20, perfect_model.size * DUPLICATION)).astype(np.float32)
        label = np.dot(data, np.repeat(perfect_model, DUPLICATION))

        model = cnn.CNNModelHelper("NCHW", name="test")
        # imitate what model wrapper does
        w = model.param_init_net.ConstantFill(
            [], 'w', shape=[perfect_model.size], value=0.0)
        model.params.append(w)
        picked = model.net.Gather([w, 'indices'], 'gather')
        out = model.ReduceFrontSum(picked, 'sum')

        sq = model.SquaredL2Distance([out, 'label'])
        loss = model.AveragedLoss(sq, "avg_loss")
        grad_map = model.AddGradientOperators([loss])
        self.assertIsInstance(grad_map['w'], core.GradientSlice)
        optimizer = self.build_optimizer(model)

        workspace.CreateBlob('indices')
        workspace.CreateBlob('label')

        for indices_type in [np.int32, np.int64]:
            workspace.RunNetOnce(model.param_init_net)
            workspace.CreateNet(model.net, True)
            for _ in range(2000):
                idx = np.random.randint(data.shape[0])
                # transform into indices of binary features
                indices = np.repeat(np.arange(perfect_model.size),
                                    DUPLICATION)[data[idx] == 1]
                if indices.size == 0:
                    continue
                workspace.FeedBlob(
                    'indices',
                    indices.reshape((indices.size,)).astype(indices_type)
                )
                workspace.FeedBlob('label',
                                   np.array(label[idx]).astype(np.float32))
                workspace.RunNet(model.net.Proto().name)

            np.testing.assert_allclose(
                perfect_model,
                workspace.FetchBlob('w'),
                atol=1e-2
            )
        self.check_optimizer(optimizer)
