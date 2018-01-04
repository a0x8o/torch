# Copyright (c) 2016-present, Facebook, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
##############################################################################

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals
import numpy as np

from caffe2.python import core
from hypothesis import given
import caffe2.python.hypothesis_test_util as hu
import hypothesis.strategies as st
import hypothesis.extra.numpy as hnp


class TestGatherOps(hu.HypothesisTestCase):
    @given(rows_num=st.integers(1, 10000),
           index_num=st.integers(0, 5000),
           **hu.gcs)
    def test_gather_ops(self, rows_num, index_num, gc, dc):
        data = np.random.random((rows_num, 10, 20)).astype(np.float32)
        ind = np.random.randint(rows_num, size=(index_num, )).astype('int32')
        op = core.CreateOperator(
            'Gather',
            ['data', 'ind'],
            ['output'])

        def ref_gather(data, ind):
            if ind.size == 0:
                return [np.zeros((0, 10, 20)).astype(np.float32)]

            output = [r for r in [data[i] for i in ind]]
            return [output]

        self.assertReferenceChecks(gc, op, [data, ind], ref_gather)


@st.composite
def _inputs(draw):
    rows_num = draw(st.integers(1, 100))
    index_num = draw(st.integers(1, 10))
    batch_size = draw(st.integers(2, 10))
    return (
        draw(hnp.arrays(
            np.float32,
            (batch_size, rows_num, 2),
            elements=st.floats(-10.0, 10.0),
        )),
        draw(hnp.arrays(
            np.int32,
            (index_num, 1),
            elements=st.integers(0, rows_num - 1),
        )),
    )


class TestBatchGatherOps(hu.HypothesisTestCase):
    @given(inputs=_inputs(),
           **hu.gcs)
    def test_batch_gather_ops(self, inputs, gc, dc):
        data, ind = inputs
        op = core.CreateOperator(
            'BatchGather',
            ['data', 'ind'],
            ['output'])

        def ref_batch_gather(data, ind):
            output = []
            for b in range(data.shape[0]):
                output.append([r for r in [data[b][i] for i in ind]])
            return [output]

        self.assertReferenceChecks(gc, op, [data, ind], ref_batch_gather)
        self.assertGradientChecks(gc, op, [data, ind], 0, [0])


if __name__ == "__main__":
    import unittest
    unittest.main()
