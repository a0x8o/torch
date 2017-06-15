from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals
from caffe2.python import core, workspace
from caffe2.python.core import CreatePythonOperator
import caffe2.python.hypothesis_test_util as hu
from hypothesis import given
import hypothesis.strategies as st
import numpy as np


def SubFunctionThatThrowsRuntimeError():
    raise RuntimeError("This is an intentional exception.")


def MainOpFunctionThatThrowsRuntimeError(inputs, _):
    return SubFunctionThatThrowsRuntimeError()


class PythonOpTest(hu.HypothesisTestCase):
    @given(x=hu.tensor())
    def test_feed(self, x):
        def f(inputs, _):
            self.assertEqual(x.shape, inputs[0].shape)
            self.assertEqual(type(inputs[0].shape), tuple)
            self.assertEqual(type(inputs[0].data), np.ndarray)
            np.testing.assert_almost_equal(x, inputs[0].data)
        op = CreatePythonOperator(f, ["x"], [])
        workspace.FeedBlob("x", x)
        workspace.RunOperatorOnce(op)

    def test_exception(self):
        op = CreatePythonOperator(MainOpFunctionThatThrowsRuntimeError, [], [])
        with self.assertRaises(RuntimeError):
            workspace.RunOperatorOnce(op)

    @given(x=hu.tensor())
    def test_feed_with_helper_function(self, x):
        def f(inputs, _):
            self.assertEqual(x.shape, inputs[0].shape)
            self.assertEqual(type(inputs[0].shape), tuple)
            self.assertEqual(type(inputs[0].data), np.ndarray)
            np.testing.assert_almost_equal(x, inputs[0].data)
        net = core.Net("test")
        net.Python(f)(["x"], [])
        workspace.FeedBlob("x", x)
        workspace.RunNetOnce(net)

    @given(x=hu.tensor())
    def test_feed_with_gc(self, x):
        def f(inputs, _):
            self.assertEqual(x.shape, inputs[0].shape)
            np.testing.assert_almost_equal(x, inputs[0].data)
        op = CreatePythonOperator(f, ["x"], [])
        workspace.FeedBlob("x", x)
        workspace.RunOperatorOnce(op)
        del f
        workspace.FeedBlob("x", x)
        workspace.RunOperatorOnce(op)

    @given(x=hu.tensor())
    def test_reshape(self, x):
        def f(inputs, outputs):
            outputs[0].reshape(inputs[0].shape)
            self.assertEqual(x.shape, inputs[0].shape)
            self.assertEqual(x.shape, outputs[0].shape)
            outputs[0].data[...] = inputs[0].data

        op = CreatePythonOperator(f, ["x"], ["y"])
        workspace.FeedBlob("x", x)
        workspace.RunOperatorOnce(op)
        y = workspace.FetchBlob("y")
        np.testing.assert_almost_equal(x, y)

    @given(x=hu.tensor())
    def test_workspace_manipulation(self, x):
        """
        Verify that python op can manipulate workspace directly
        """
        def f(inputs, outputs, ws):
            fetched = ws.blobs['internal'].fetch()
            np.testing.assert_almost_equal(fetched, x)

        ws = workspace.C.Workspace()
        net = core.Net("test")
        net.GivenTensorFill([], ['internal'], values=x, shape=x.shape)
        net.Python(f, pass_workspace=True)([], [])
        ws.run(net)

    @given(x=hu.tensor())
    def test_caught_exception_doesnt_terminate(self, x):
        def f(inputs, outputs):
            try:
                raise Exception("Exception in handler")
            except Exception:
                pass

        op = CreatePythonOperator(f, ["x"], ["y"])
        workspace.FeedBlob("x", x)
        workspace.RunOperatorOnce(op)

    @given(x=hu.tensor(),
           n=st.integers(min_value=1, max_value=20),
           w=st.integers(min_value=1, max_value=20))
    def test_multithreaded_evaluation(self, x, n, w):
        def f(inputs, outputs):
            outputs[0].reshape(inputs[0].shape)
            outputs[0].data[...] = inputs[0].data
        ops = [CreatePythonOperator(f, ["x"], [str(i)]) for i in range(n)]
        net = core.Net("net")
        net.Proto().op.extend(ops)
        net.Proto().type = "dag"
        net.Proto().num_workers = w
        iters = 100
        plan = core.Plan("plan")
        plan.AddStep(core.ExecutionStep("test-step", net, iters))
        workspace.FeedBlob("x", x)
        workspace.RunPlan(plan.Proto().SerializeToString())
        for i in range(n):
            y = workspace.FetchBlob(str(i))
            np.testing.assert_almost_equal(x, y)

    @given(x=hu.tensor(), in_place=st.booleans(), **hu.gcs)
    def test_gradient(self, x, in_place, gc, dc):
        def f(inputs, outputs):
            outputs[0].reshape(inputs[0].shape)
            outputs[0].data[...] = inputs[0].data * 2

        def grad_f(inputs, outputs):
            # Ordering is [inputs, outputs, grad_outputs]
            grad_output = inputs[2]

            grad_input = outputs[0]
            grad_input.reshape(grad_output.shape)
            grad_input.data[...] = grad_output.data * 2

        op = CreatePythonOperator(
            f, ["x"], ["x" if in_place else "y"], grad_f=grad_f)
        self.assertGradientChecks(gc, op, [x], 0, [0])
        self.assertDeviceChecks(dc, op, [x], [0])

    @given(inputs=hu.tensors(n=2), **hu.gcs)
    def test_gradient_multiple(self, inputs, gc, dc):
        (x1, x2) = inputs

        def f(inputs, outputs):
            for idx in [0, 1]:
                self.assertEqual(type(inputs[idx].shape), tuple)
                outputs[idx].reshape(inputs[idx].shape)
                outputs[idx].data[...] = inputs[idx].data * 2

        def grad_f(inputs, outputs):
            # Ordering is [inputs, outputs, grad_outputs]
            self.assertEqual(len(inputs), 6)
            self.assertEqual(len(outputs), 2)
            for (grad_output_idx, grad_input_idx) in [(4, 0), (5, 1)]:
                grad_output = inputs[grad_output_idx]
                grad_input = outputs[grad_input_idx]
                grad_input.reshape(grad_output.shape)
                grad_input.data[...] = grad_output.data * 2

        op = CreatePythonOperator(f, ["x1", "x2"], ["y1", "y2"], grad_f=grad_f)

        for idx in [0, 1]:
            self.assertGradientChecks(gc, op, [x1, x2], idx, [0, 1])
        self.assertDeviceChecks(dc, op, [x1, x2], [0, 1])
