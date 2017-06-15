from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from caffe2.python import workspace
from caffe2.python.core import Plan, to_execution_step
from caffe2.python.task import Task, final_output
from caffe2.python.net_builder import ops, NetBuilder
from caffe2.python.session import LocalSession
import unittest


def _test_loop():
    x = ops.Const(5)
    y = ops.Const(0)
    with ops.loop():
        ops.stop_if(ops.EQ([x, ops.Const(0)]))
        ops.Add([x, ops.Const(-1)], [x])
        ops.Add([y, ops.Const(1)], [y])
    return y


def _test_inner_stop(x):
    ops.stop_if(ops.LT([x, ops.Const(5)]))


def _test_outer():
    x = ops.Const(10)
    # test stop_if(False)
    with ops.stop_guard() as g1:
        _test_inner_stop(x)

    # test stop_if(True)
    y = ops.Const(3)
    with ops.stop_guard() as g2:
        _test_inner_stop(y)

    # test no stop
    with ops.stop_guard() as g4:
        ops.Const(0)

    # test empty clause
    with ops.stop_guard() as g3:
        pass

    return (
        g1.has_stopped(), g2.has_stopped(), g3.has_stopped(), g4.has_stopped())


def _test_if(x):
    y = ops.Const(1)
    with ops.If(ops.GT([x, ops.Const(50)])):
        ops.Const(2, blob_out=y)
    with ops.If(ops.LT([x, ops.Const(50)])):
        ops.Const(3, blob_out=y)
        ops.stop()
        ops.Const(4, blob_out=y)
    return y


class TestNetBuilder(unittest.TestCase):
    def test_ops(self):
        with NetBuilder() as nb:
            y = _test_loop()
            z, w, a, b = _test_outer()
            p = _test_if(ops.Const(75))
            q = _test_if(ops.Const(25))
        plan = Plan('name')
        plan.AddStep(to_execution_step(nb))
        ws = workspace.C.Workspace()
        ws.run(plan)
        expected = [
            (y, 5),
            (z, False),
            (w, True),
            (a, False),
            (b, False),
            (p, 2),
            (q, 3),
        ]
        for b, expected in expected:
            actual = ws.blobs[str(b)].fetch()
            self.assertEquals(actual, expected)

    def _expected_loop(self):
        total = 0
        total_large = 0
        total_small = 0
        total_tiny = 0
        for loop_iter in range(10):
            outer = loop_iter * 10
            for inner_iter in range(loop_iter):
                val = outer + inner_iter
                if val >= 80:
                    total_large += val
                elif val >= 50:
                    total_small += val
                else:
                    total_tiny += val
                total += val
        return total, total_large, total_small, total_tiny

    def _actual_loop(self):
        total = ops.Const(0)
        total_large = ops.Const(0)
        total_small = ops.Const(0)
        total_tiny = ops.Const(0)
        with ops.loop(10) as loop:
            outer = ops.Mul([loop.iter(), ops.Const(10)])
            with ops.loop(loop.iter()) as inner:
                val = ops.Add([outer, inner.iter()])
                with ops.If(ops.GE([val, ops.Const(80)])) as c:
                    ops.Add([total_large, val], [total_large])
                with c.Elif(ops.GE([val, ops.Const(50)])) as c:
                    ops.Add([total_small, val], [total_small])
                with c.Else():
                    ops.Add([total_tiny, val], [total_tiny])
                ops.Add([total, val], total)
        return map(final_output, (total, total_large, total_small, total_tiny))

    def test_loops(self):
        with Task() as task:
            out_actual = self._actual_loop()
        with LocalSession() as session:
            session.run(task)
            expected = self._expected_loop()
            actual = [o.fetch() for o in out_actual]
            for e, a in zip(expected, actual):
                self.assertEquals(e, a)

    def test_setup(self):
        with Task() as task:
            with ops.task_init():
                one = ops.Const(1)
            two = ops.Add([one, one])
            with ops.task_init():
                three = ops.Const(3)
            accum = ops.Add([two, three])
            # here, accum should be 5
            with ops.task_exit():
                # here, accum should be 6, since this executes after lines below
                seven_1 = ops.Add([accum, one])
            six = ops.Add([accum, one])
            ops.Add([accum, one], [accum])
            seven_2 = ops.Add([accum, one])
            o6 = final_output(six)
            o7_1 = final_output(seven_1)
            o7_2 = final_output(seven_2)
        with LocalSession() as session:
            session.run(task)
            self.assertEquals(o6.fetch(), 6)
            self.assertEquals(o7_1.fetch(), 7)
            self.assertEquals(o7_2.fetch(), 7)
