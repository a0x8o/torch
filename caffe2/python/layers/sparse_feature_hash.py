## @package sparse_feature_hash
# Module caffe2.python.layers.sparse_feature_hash
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from caffe2.python import schema
from caffe2.python.layers.layers import (
    ModelLayer,
    IdList,
    IdScoreList,
)

import numpy as np


class SparseFeatureHash(ModelLayer):

    def __init__(self, model, input_record, seed,
                 name='sparse_feature_hash', **kwargs):
        super(SparseFeatureHash, self).__init__(model, name, input_record, **kwargs)

        self.seed = seed
        self.lengths_blob = schema.Scalar(
            np.int32,
            self.get_next_blob_reference("lengths"),
        )

        if schema.equal_schemas(input_record, IdList):
            self.modulo = self.extract_hash_size(input_record.items.metadata)
            metadata = schema.Metadata(
                categorical_limit=self.modulo,
                feature_specs=input_record.items.metadata.feature_specs,
            )
            hashed_indices = schema.Scalar(
                np.int64,
                self.get_next_blob_reference("hashed_idx")
            )
            hashed_indices.set_metadata(metadata)
            self.output_schema = schema.List(
                values=hashed_indices,
                lengths_blob=self.lengths_blob,
            )
        elif schema.equal_schemas(input_record, IdScoreList):
            self.values_blob = schema.Scalar(
                np.float32,
                self.get_next_blob_reference("values"),
            )
            self.modulo = self.extract_hash_size(input_record.keys.metadata)
            metadata = schema.Metadata(
                categorical_limit=self.modulo,
                feature_specs=input_record.keys.metadata.feature_specs,
            )
            hashed_indices = schema.Scalar(
                np.int64,
                self.get_next_blob_reference("hashed_idx")
            )
            hashed_indices.set_metadata(metadata)
            self.output_schema = schema.Map(
                keys=hashed_indices,
                values=self.values_blob,
                lengths_blob=self.lengths_blob,
            )
        else:
            assert False, "Input type must be one of (IdList, IdScoreList)"

    def extract_hash_size(self, metadata):
        if metadata.feature_specs and metadata.feature_specs.desired_hash_size:
            return metadata.feature_specs.desired_hash_size
        elif metadata.categorical_limit is not None:
            return metadata.categorical_limit
        else:
            assert False, "desired_hash_size or categorical_limit must be set"

    def add_ops(self, net):
        if schema.equal_schemas(self.output_schema, IdList):
            input_blobs = self.input_record.items.field_blobs()
            output_blobs = self.output_schema.items.field_blobs()

            net.Alias(
                self.input_record.lengths.field_blobs(),
                self.lengths_blob.field_blobs()
            )
        elif schema.equal_schemas(self.output_schema, IdScoreList):
            input_blobs = self.input_record.keys.field_blobs()
            output_blobs = self.output_schema.keys.field_blobs()

            net.Alias(
                self.input_record.values.field_blobs(),
                self.values_blob.field_blobs()
            )
            net.Alias(
                self.input_record.lengths.field_blobs(),
                self.lengths_blob.field_blobs()
            )
        else:
            raise NotImplementedError()
        net.IndexHash(input_blobs,
                      output_blobs,
                      seed=self.seed,
                      modulo=self.modulo)
