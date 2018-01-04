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

from caffe2.python import core
from hypothesis import given

import caffe2.python.hypothesis_test_util as hu
import hypothesis.strategies as st
import numpy as np


@st.composite
def _data(draw):
    dtype = draw(st.sampled_from([np.int32, np.int64]))
    return draw(hu.tensor(dtype=dtype))


class TestMod(hu.HypothesisTestCase):
    @given(
        data=_data(),
        divisor=st.integers(min_value=1, max_value=np.iinfo(np.int64).max),
        inplace=st.booleans(),
        **hu.gcs_cpu_only
    )
    def test_mod(self, data, divisor, inplace, gc, dc):

        def ref(data):
            output = data % divisor
            return [output]

        op = core.CreateOperator(
            'Mod',
            ['data'],
            ['data' if inplace else 'output'],
            divisor=divisor,
        )

        self.assertReferenceChecks(gc, op, [data], ref)


if __name__ == "__main__":
    import unittest
    unittest.main()
