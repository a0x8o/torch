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

from caffe2.python.schema import Struct, ConstRecord
from caffe2.python import core, workspace
from caffe2.python.session import LocalSession
from caffe2.python.dataset import Dataset
from caffe2.python.pipeline import pipe
from caffe2.python.checkpoint import (
    CheckpointManager, MultiNodeCheckpointManager, Job, JobRunner,
    UploadTaskGroupBuilder)
from caffe2.python.net_builder import ops
from caffe2.python.task import Node, Task, TaskGroup, WorkspaceType, Cluster
from caffe2.python.test_util import TestCase
from caffe2.python.dataio import ReaderWithLimit

import numpy as np
import os
import shutil
import tempfile

def build_pipeline(node_id):
    with Node('trainer_%d' % node_id):
        with Job.current().init_group, Task():
            data_arr = Struct(('val', np.array(list(range(10)))))
            data = ConstRecord(ops, data_arr)
            ds = Dataset(data, name='dataset:%d' % node_id)
            full_reader = ds.reader(ops)
            total = ops.Const([100])

        def inc_total(rec):
            ops.Add([total, rec.val()], [total])

        epoch_reader = ReaderWithLimit(full_reader, num_iter=3)
        pipe(epoch_reader, processor=inc_total)
        Job.current().add_stop_signal(epoch_reader.data_finished())
    return [total]


EXPECTED_TOTALS = [103, 115, 136, 145]


def local_copy_op(src, dest):
    def copy_op(inputs, outputs):
        shutil.copyfile(src, dest)
    return copy_op


class UploadToLocalFile(UploadTaskGroupBuilder):
    def __init__(self, dest_dir):
        self.dest_dir = dest_dir

    def build(self, epoch, checkpoint_manager):
        with TaskGroup(WorkspaceType.GLOBAL) as upload_task_group:
            for node, manager in checkpoint_manager._node_managers:
                with Node(str(node)), Task():
                    src_path = manager._db_name(epoch)
                    dest_path = os.path.join(self.dest_dir, str(node))
                    ops.Python((local_copy_op,
                                [src_path, dest_path], {}))([], [])
        return upload_task_group

class TestCheckpoint(TestCase):
    def run_with(self, builder):
        with Cluster():
            with Job() as job:
                outputs = build_pipeline(node_id=0)
            output_fetcher = Task(step=core.Net('empty'), outputs=outputs)

            def fetch_total(session):
                session.run(output_fetcher)
                return output_fetcher.outputs()[0].fetch()

            session, checkpoint = builder()
            compiled_job = job.compile(LocalSession)
            num_epochs = JobRunner(compiled_job, checkpoint)(session)
            self.assertEquals(num_epochs, len(EXPECTED_TOTALS))
            self.assertEquals(fetch_total(session), EXPECTED_TOTALS[-1])

            for initial_epoch in range(1, num_epochs + 1):
                session, checkpoint = builder()
                JobRunner(
                    compiled_job,
                    checkpoint, resume_from_epoch=initial_epoch)(session)
                self.assertEquals(fetch_total(session), EXPECTED_TOTALS[-1])

            for epoch in range(1, num_epochs + 1):
                session.run(checkpoint.load(epoch))
                self.assertEquals(fetch_total(session),
                                  EXPECTED_TOTALS[epoch - 1])

    def test_single_checkpoint(self):
        # test single node
        try:
            tmpdir = tempfile.mkdtemp()

            def builder():
                ws = workspace.C.Workspace()
                session = LocalSession(ws)
                checkpoint = CheckpointManager(tmpdir, 'temp_node', 'minidb')
                return session, checkpoint

            self.run_with(builder)
        finally:
            shutil.rmtree(tmpdir)

        # test multi-node
        try:
            tmpdir = tempfile.mkdtemp()

            def builder():
                ws = workspace.C.Workspace()
                session = LocalSession(ws)
                checkpoint = MultiNodeCheckpointManager(tmpdir, 'minidb')
                return session, checkpoint

            self.run_with(builder)
        finally:
            shutil.rmtree(tmpdir)

    def test_ckpt_name_and_load_model_from_ckpts(self):
        try:
            num_nodes = 3
            tmpdir = tempfile.mkdtemp()
            # First, check if the checkpoint name generation mechanism is
            # correct.
            checkpoint = MultiNodeCheckpointManager(tmpdir, 'minidb')
            with Cluster():
                with Job() as job:
                    for node_id in range(num_nodes):
                        build_pipeline(node_id)
                compiled_job = job.compile(LocalSession)
                checkpoint.init(compiled_job.nodes_to_checkpoint())

                for node_id in range(num_nodes):
                    epoch = 5
                    node_name = 'trainer_%d' % node_id
                    expected_db_name = tmpdir + '/' + node_name + '.5'
                    self.assertEquals(
                        checkpoint.get_ckpt_db_name(node_name, epoch),
                        expected_db_name)
            shutil.rmtree(tmpdir)

            # Next, check mechanism to load model from checkpoints.
            tmpdir = tempfile.mkdtemp()
            workspace.ResetWorkspace()
            for node_id in range(num_nodes):
                ws = workspace.C.Workspace()
                session = LocalSession(ws)
                checkpoint = MultiNodeCheckpointManager(tmpdir, 'minidb')
                with Cluster():
                    with Job() as job:
                        build_pipeline(node_id)
                    compiled_job = job.compile(LocalSession)
                    job_runner = JobRunner(compiled_job, checkpoint)
                    num_epochs = job_runner(session)
                self.assertEquals(num_epochs, len(EXPECTED_TOTALS))

                # There are 12 global blobs after finishing up the job runner.
                # (only blobs on init_group are checkpointed)
                self.assertEquals(len(ws.blobs), 12)

            ws = workspace.C.Workspace()
            session = LocalSession(ws)
            self.assertEquals(len(ws.blobs), 0)
            model_blob_names = ['trainer_1/task_2/GivenTensorInt64Fill:0',
                                'trainer_2/task_2/GivenTensorInt64Fill:0']
            checkpoint = MultiNodeCheckpointManager(tmpdir, 'minidb')
            with Cluster():
                with Job() as job:
                    for node_id in range(num_nodes):
                        build_pipeline(node_id)
                compiled_job = job.compile(LocalSession)
                job_runner = JobRunner(compiled_job, checkpoint)
                job_runner.load_blobs_from_checkpoints(
                    blob_names=model_blob_names, epoch=1, session=session)

                # Check that we can successfully load from checkpoints of epochs
                # 1 to 4, but not epoch 5.
                for epoch in range(1, 5):
                    self.assertTrue(
                        job_runner.load_blobs_from_checkpoints(
                            blob_names=model_blob_names, epoch=epoch,
                            session=session))
                    # Check that all the model blobs are loaded.
                    for blob_name in model_blob_names:
                        self.assertTrue(ws.has_blob(blob_name))
                        self.assertEquals(
                            ws.fetch_blob(blob_name),
                            np.array([EXPECTED_TOTALS[epoch - 1]]))
                self.assertFalse(
                    job_runner.load_blobs_from_checkpoints(
                        blob_names=model_blob_names, epoch=5, session=session))

        finally:
            shutil.rmtree(tmpdir)

    def test_upload_checkpoint(self):
        try:
            tmpdir = tempfile.mkdtemp()
            upload_dir = os.path.join(tmpdir, "upload")
            os.mkdir(upload_dir)
            num_nodes = 3

            # The uploaded files do not exist yet.
            for node_id in range(num_nodes):
                node_name = 'trainer_%d' % node_id
                upload_path = os.path.join(upload_dir, node_name)
                self.assertFalse(os.path.exists(upload_path))

            # Create and run the job runner.
            for node_id in range(3):
                ws = workspace.C.Workspace()
                session = LocalSession(ws)
                checkpoint = MultiNodeCheckpointManager(tmpdir, 'minidb')
                with Cluster():
                    with Job() as job:
                        build_pipeline(node_id)
                    compiled_job = job.compile(LocalSession)
                    local_upload_builder = UploadToLocalFile(upload_dir)
                    job_runner = JobRunner(
                        compiled_job, checkpoint,
                        upload_task_group_builder=local_upload_builder)
                    num_epochs = job_runner(session)
                    self.assertEquals(num_epochs, len(EXPECTED_TOTALS))

            # The uploaded files should exist now.
            for node_id in range(num_nodes):
                node_name = 'trainer_%d' % node_id
                upload_path = os.path.join(upload_dir, node_name)
                self.assertTrue(os.path.exists(upload_path))

        finally:
            shutil.rmtree(tmpdir)
