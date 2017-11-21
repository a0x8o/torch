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

from caffe2.python.dataio import ReaderWithLimit
from caffe2.python.dataset import Dataset
from caffe2.python.pipeline import pipe
from caffe2.python.schema import Struct, NewRecord, FeedRecord
from caffe2.python.session import LocalSession
from caffe2.python.task import TaskGroup, final_output, WorkspaceType
from caffe2.python.test_util import TestCase
from caffe2.python.cached_reader import CachedReader
from caffe2.python import core, workspace
from caffe2.python.net_builder import ops
import numpy as np
import os
import shutil
import tempfile


def init_dataset(ws, size=100):
    src_init = core.Net('src_init')
    with core.NameScope('src'):
        src_values = Struct(('label', np.array(range(size))))
        src_blobs = NewRecord(src_init, src_values)
        src_ds = Dataset(src_blobs)
        FeedRecord(src_blobs, src_values, ws)
    ws.run(src_init)
    return src_ds


def read_all_data(ws, reader, session):
    dst_init = core.Net('dst_init')
    with core.NameScope('dst'):
        dst_ds = Dataset(reader.schema().clone_schema())
        dst_ds.init_empty(dst_init)
    session.run(dst_init)

    with TaskGroup(workspace_type=WorkspaceType.GLOBAL) as tg:
        pipe(reader, dst_ds.writer(), num_threads=8)
    session.run(tg)

    return ws.blobs[str(dst_ds.content().label())].fetch()


class TestReaderWithLimit(TestCase):
    def test_runtime_threads(self):
        ws = workspace.C.Workspace()
        session = LocalSession(ws)
        src_ds = init_dataset(ws)
        totals = [None] * 3

        def proc(rec):
            # executed once
            with ops.task_init():
                counter1 = ops.CreateCounter([], ['global_counter'])
                counter2 = ops.CreateCounter([], ['global_counter2'])
                counter3 = ops.CreateCounter([], ['global_counter3'])
            # executed once per thread
            with ops.task_instance_init():
                task_counter = ops.CreateCounter([], ['task_counter'])
            # executed on each iteration
            ops.CountUp(counter1)
            ops.CountUp(task_counter)
            # executed once per thread
            with ops.task_instance_exit():
                with ops.loop(ops.RetrieveCount(task_counter)):
                    ops.CountUp(counter2)
                ops.CountUp(counter3)
            # executed once
            with ops.task_exit():
                totals[0] = final_output(ops.RetrieveCount(counter1))
                totals[1] = final_output(ops.RetrieveCount(counter2))
                totals[2] = final_output(ops.RetrieveCount(counter3))
            return rec

        """ 1. Feed full dataset """
        with TaskGroup() as tg:
            pipe(src_ds.reader(), num_runtime_threads=8, processor=proc)
        session.run(tg)
        self.assertEquals(totals[0].fetch(), 100)
        self.assertEquals(totals[1].fetch(), 100)
        self.assertEquals(totals[2].fetch(), 8)

        """ 2. Add a few steps in between """
        with TaskGroup() as tg:
            q1 = pipe(src_ds.reader(), num_runtime_threads=2)
            q2 = pipe(
                ReaderWithLimit(q1.reader(), num_iter=25),
                num_runtime_threads=3)
            pipe(q2, processor=proc, num_runtime_threads=6)
        session.run(tg)
        self.assertEquals(totals[0].fetch(), 25)
        self.assertEquals(totals[1].fetch(), 25)
        self.assertEquals(totals[2].fetch(), 6)

    def test_reader_with_limit(self):
        ws = workspace.C.Workspace()
        session = LocalSession(ws)

        """ 1. feed full dataset """
        src_ds = init_dataset(ws)

        """ 2. Read with limit smaller than size of dataset """
        dst_init = core.Net('dst_init')
        with core.NameScope('dst'):
            dst_ds = Dataset(src_ds.content().clone_schema())
            dst_ds.init_empty(dst_init)
        ws.run(dst_init)

        # WorkspaceType.GLOBAL is required because we are fetching
        # reader.data_finished() after the TaskGroup finishes.
        with TaskGroup(workspace_type=WorkspaceType.GLOBAL) as tg:
            reader = ReaderWithLimit(src_ds.reader(), num_iter=10)
            pipe(reader, dst_ds.writer(), num_threads=8)
        session.run(tg)

        self.assertFalse(ws.blobs[str(reader.data_finished())].fetch())
        self.assertEquals(
            sorted(ws.blobs[str(dst_ds.content().label())].fetch()),
            list(range(10))
        )

        """ 3. Read with limit larger than size of dataset """
        ws.run(dst_init)
        with TaskGroup(workspace_type=WorkspaceType.GLOBAL) as tg:
            reader = ReaderWithLimit(src_ds.reader(), num_iter=110)
            pipe(reader, dst_ds.writer(), num_runtime_threads=8)
        session.run(tg)
        self.assertEquals(
            sorted(ws.blobs[str(dst_ds.content().label())].fetch()),
            list(range(100))
        )
        self.assertTrue(ws.blobs[str(reader.data_finished())].fetch())

        """ 4. Read without counter """
        ws.run(dst_init)
        with TaskGroup(workspace_type=WorkspaceType.GLOBAL) as tg:
            reader = ReaderWithLimit(src_ds.reader(), num_iter=None)
            pipe(reader, dst_ds.writer(), num_threads=8)
        session.run(tg)
        self.assertEquals(
            sorted(ws.blobs[str(dst_ds.content().label())].fetch()),
            list(range(100))
        )
        self.assertTrue(ws.blobs[str(reader.data_finished())].fetch())

        """ 5. Read using the same reader without resetting workspace """
        session.run(tg)
        self.assertEquals(
            sorted(ws.blobs[str(dst_ds.content().label())].fetch()),
            sorted(list(range(100)) * 2)
        )

    def test_cached_reader(self):
        ws = workspace.C.Workspace()
        session = LocalSession(ws)

        def build_source_reader(size):
            src_ds = init_dataset(ws, size)
            return src_ds.reader()

        with tempfile.NamedTemporaryFile(delete=False) as f:
            path = f.name
            f.close()
            os.remove(path)

            """ 1. Read data for the first time. """
            cached_reader1 = CachedReader(build_source_reader(100))
            init_step = cached_reader1.build_cache(path)
            session.run(init_step)

            data = read_all_data(ws, cached_reader1, session)
            self.assertEqual(sorted(data), list(range(100)))

            """ 2. Read data from cache. """
            workspace.ResetWorkspace()
            cached_reader2 = CachedReader(build_source_reader(200))
            init_step = cached_reader2.build_cache(path)
            session.run(init_step)

            data = read_all_data(ws, cached_reader2, session)
            self.assertEqual(sorted(data), list(range(100)))

            shutil.rmtree(path)

            """ 3. We removed cache so we expect to receive data from original
            reader. """
            workspace.ResetWorkspace()
            cached_reader3 = CachedReader(build_source_reader(300))
            init_step = cached_reader3.build_cache(path)
            session.run(init_step)

            data = read_all_data(ws, cached_reader3, session)
            self.assertEqual(sorted(data), list(range(300)))

            shutil.rmtree(path)
