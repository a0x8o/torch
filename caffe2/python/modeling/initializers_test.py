from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import unittest
from caffe2.python import brew, model_helper
from caffe2.python.modeling.initializers import (
    Initializer,
    create_xavier_fill_initializer,
)


class InitializerTest(unittest.TestCase):
    def test_fc_initializer(self):
        model = model_helper.ModelHelper(name="test")
        data = model.net.AddExternalInput("data")
        fc1 = brew.fc(model, data, "fc1", dim_in=1, dim_out=1)
        fc2 = brew.fc(model, fc1, "fc2", dim_in=1, dim_out=1,
                      weight_initializer=create_xavier_fill_initializer())

        # no operator name set, will use default
        fc3 = brew.fc(model, fc2, "fc3", dim_in=1, dim_out=1,
                      weight_initializer=Initializer())

        # no operator name set, will use custom
        fc4 = brew.fc(model, fc3, "fc4", dim_in=1, dim_out=1,
                      weight_initializer=Initializer(),
                      weight_init=("ConstantFill", {}),
        )

        # conflicting operator name
        with self.assertRaises(Exception):
            brew.fc(model, fc4, "fc5", dim_in=1, dim_out=1,
                    weight_initializer=create_xavier_fill_initializer(),
                    weight_init=("ConstantFill", {}),
            )
