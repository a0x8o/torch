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

## @package batch_mse_loss
# Module caffe2.python.layers.batch_mse_loss
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from caffe2.python import schema
from caffe2.python.layers.layers import (
    ModelLayer,
)
from caffe2.python.layers.tags import (
    Tags
)
import numpy as np


class BatchMSELoss(ModelLayer):

    def __init__(self, model, input_record, name='batch_mse_loss', **kwargs):
        super(BatchMSELoss, self).__init__(model, name, input_record, **kwargs)

        assert schema.is_schema_subset(
            schema.Struct(
                ('label', schema.Scalar()),
                ('prediction', schema.Scalar())
            ),
            input_record
        )
        self.tags.update([Tags.EXCLUDE_FROM_PREDICTION])

        self.output_schema = schema.Scalar(
            np.float32,
            self.get_next_blob_reference('output'))

    def add_ops(self, net):
        prediction = net.Squeeze(
            self.input_record.prediction(),
            net.NextScopedBlob('squeezed_prediction'),
            dims=[1]
        )

        label = self.input_record.label.field_blobs()
        if self.input_record.label.field_type().base != (
                self.input_record.prediction.field_type().base):

            label = net.Cast(
                label,
                net.NextScopedBlob('cast_label'),
                to=schema.data_type_for_dtype(
                    self.input_record.prediction.field_type()
                )
            )

        label = net.StopGradient(
            label,
            net.NextScopedBlob('stopped_label')
        )

        l2dist = net.SquaredL2Distance(
            [label, prediction],
            net.NextScopedBlob('l2')
        )

        net.AveragedLoss(l2dist, self.output_schema.field_blobs())
