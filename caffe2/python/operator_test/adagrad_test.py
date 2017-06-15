from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import functools

from hypothesis import given, strategies as st
import numpy as np

from caffe2.python import core
import caffe2.python.hypothesis_test_util as hu


class TestAdagrad(hu.HypothesisTestCase):

    @staticmethod
    def ref_adagrad(param_in, mom_in, grad, lr, epsilon):
        mom_out = mom_in + np.square(grad)
        grad_adj = lr * grad / (np.sqrt(mom_out) + epsilon)
        param_out = param_in + grad_adj
        return (param_out, mom_out)

    @given(inputs=hu.tensors(n=3),
           lr=st.floats(min_value=0.01, max_value=0.99,
                        allow_nan=False, allow_infinity=False),
           epsilon=st.floats(min_value=0.01, max_value=0.99,
                             allow_nan=False, allow_infinity=False),
           **hu.gcs)
    def test_adagrad(self, inputs, lr, epsilon, gc, dc):
        param, momentum, grad = inputs
        lr = np.array([lr], dtype=np.float32)

        op = core.CreateOperator(
            "Adagrad",
            ["param", "momentum", "grad", "lr"],
            ["param", "momentum"],
            epsilon=epsilon,
            device_option=gc,
        )

        self.assertReferenceChecks(
            gc, op,
            [param, momentum, grad, lr],
            functools.partial(self.ref_adagrad, epsilon=epsilon))

    @given(inputs=hu.tensors(n=3),
           lr=st.floats(min_value=0.01, max_value=0.99,
                        allow_nan=False, allow_infinity=False),
           epsilon=st.floats(min_value=0.01, max_value=0.99,
                             allow_nan=False, allow_infinity=False),
           **hu.gcs)
    def test_sparse_adagrad(self, inputs, lr, epsilon, gc, dc):
        param, momentum, grad = inputs
        indices = np.arange(grad.shape[0])
        indices = indices[indices % 2 == 0]
        grad = grad[indices]
        momentum = np.abs(momentum)
        lr = np.array([lr], dtype=np.float32)

        op = core.CreateOperator(
            "SparseAdagrad",
            ["param", "momentum", "indices", "grad", "lr"],
            ["param", "momentum"],
            epsilon=epsilon,
            device_option=gc)

        def ref_sparse(param, momentum, indices, grad, lr):
            param_out = np.copy(param)
            momentum_out = np.copy(momentum)
            for i, index in enumerate(indices):
                param_out[index], momentum_out[index] = self.ref_adagrad(
                    param[index], momentum[index], grad[i], lr, epsilon)
            return (param_out, momentum_out)

        self.assertReferenceChecks(
            gc, op,
            [param, momentum, indices, grad, lr],
            ref_sparse)
