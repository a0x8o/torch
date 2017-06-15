## @package checkpoint
# Module caffe2.python.checkpoint
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import os
import logging
from caffe2.python import core, context
from caffe2.python.net_builder import ops
from caffe2.python.task import Node, Task, TaskGroup, TaskOutput, WorkspaceType

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# The name of the special net that is used to store all the blob names in the
# workspace.
__BLOB_NAMES_NET__ = 'get_blob_list'

@context.define_context()
class Job(object):
    """
    A Job defines three TaskGroups: the `init_group`, the `epoch_group` and the
    `exit_group` which will be run by a JobRunner.

    The `init_group` will be run only once at startup. Its role is to
    initialize globally persistent blobs such as model weights, accumulators
    and data file lists.

    The `epoch_group` will be run in a loop after init_group. The loop will
    exit when any of the stop signals added with `add_stop_signal` is True
    at the end of an epoch.

    The `exit_group` will be run only once at the very end of the job, when one
    of the stopping criterias for `epoch_group` was met. The role of this group
    is save the results of training in the end of the job.

    Jobs are context-driven, so that Tasks can be added to the active Job
    without having to explicitly pass the job object around.

    Example of usage:

    def build_reader(partitions):
        with Job.current().init_group:
            reader = HiveReader(init_reader, ..., partitions)
            Task(step=init_reader)
        with Job.current().epoch_group:
            limited_reader = ReaderWithLimit(reader, num_iter=10000)
            data_queue = pipe(limited_reader, num_threads=8)
            Job.current().add_stop_signal(limited_reader.data_finished())
        return data_queue

    def build_hogwild_trainer(reader, model):
        with Job.current().init_group:
            Task(step=model.param_init_net)
        with Job.current().epoch_group:
            pipe(reader, processor=model, num_threads=8)
        with Job.current().exit_group:
            Task(step=model.save_model_net)

    with Job() as job:
        reader = build_reader(partitions)
        model = build_model(params)
        build_hogwild_trainer(reader, model)
    """
    def __init__(self,
                 init_group=None, epoch_group=None,
                 exit_group=None, stop_signals=None,
                 nodes_to_checkpoint=None):
        self.init_group = init_group or TaskGroup(
            workspace_type=WorkspaceType.GLOBAL)
        self.epoch_group = epoch_group or TaskGroup()
        self.exit_group = exit_group or TaskGroup()
        self.stop_signals = stop_signals or []
        self._nodes_to_checkpoint = nodes_to_checkpoint

    def nodes_to_checkpoint(self):
        if self._nodes_to_checkpoint:
            return self._nodes_to_checkpoint
        else:
            return self.init_group.used_nodes()

    def compile(self, session_class):
        return Job(
            init_group=session_class.compile(self.init_group),
            epoch_group=session_class.compile(self.epoch_group),
            exit_group=session_class.compile(self.exit_group),
            stop_signals=self.stop_signals,
            nodes_to_checkpoint=self.nodes_to_checkpoint())

    def __enter__(self):
        self.epoch_group.__enter__()
        return self

    def __exit__(self, *args):
        self.epoch_group.__exit__()

    def add_stop_signal(self, output):
        if isinstance(output, core.BlobReference):
            t = Task(outputs=[output], group=self.epoch_group)
            output = t.outputs()[0]
        assert isinstance(output, TaskOutput)
        self.stop_signals.append(output)


class CheckpointManager(object):
    """
    Controls saving and loading of workspaces on every epoch boundary of a job.
    If a CheckpointManager instance is passed to JobRunner, then JobRunner will
    call `init`, `read` and `save` at different moments in between epoch runs.
    """
    def __init__(self, db, db_type):
        self._db = db
        self._db_type = db_type
        # make sure these blobs are the first in the checkpoint file.
        self._net = core.Net('!!checkpoint_mngr')
        self._blob_names = self._net.AddExternalInput('blob_names')
        self._names_output = None

    def init(self, nodes=None, retrieve_from_epoch=None):
        """
        Build a Task that will be run once after the job's `init_group` is run.
        This task will determine which blobs need to be checkpointed.
        If retrieve_from_epoch is not None, then the checkpoint metadata is
        retrieved from a previously saved checkpoint.
        """
        assert nodes is None or len(nodes) == 1, (
            'CheckpointManager only supports single node.')
        with Task(outputs=[self._blob_names]) as task:
            if retrieve_from_epoch is None:
                ops.GetAllBlobNames(
                    [],
                    self._blob_names,
                    include_shared=False)
            else:
                ops.Load(
                    [], self._blob_names,
                    db=self._db_name(retrieve_from_epoch),
                    db_type=self._db_type,
                    absolute_path=True)
        self._names_output = task.outputs()[0]
        return task

    def blob_list(self):
        assert self._names_output
        return self._names_output.fetch().tolist()

    def _db_name(self, epoch):
        return '%s.%06d' % (self._db, epoch)

    def load(self, epoch):
        """
        Build a Task that will be run by JobRunner when the job is to be
        resumed from a given epoch. This task will run a Load op that will
        load and deserialize all relevant blobs from a persistent storage.
        """
        logger.info('Load from %s' % self._db_name(epoch))
        with Task() as task:
            ops.Load(
                [],
                self.blob_list(),
                db=self._db_name(epoch),
                db_type=self._db_type,
                absolute_path=True)
        return task

    def load_blobs_from_checkpoint(self, blob_names, epoch):
        """
        Builds a Task that loads only the necessary blobs from a checkpoint of
        the given epoch. The necessary blobs are given in the blob_names
        argument.

        Args:
            blob_names: A list of strings. Each string is the name of a
                blob.
            epoch: The checkpoint epoch to load from.

        Returns:
            A Task which loads the specified blobs from the checkpoint of the
            given epoch.
        """
        logger.info('Load from %s' % self._db_name(epoch))
        with Task() as task:
            ops.Load(
                [],
                blob_names,
                db=self._db_name(epoch),
                db_type=self._db_type,
                absolute_path=True,
                allow_incomplete=True)
        return task

    def check_db_exists(self, epoch):
        logger.info('Check existence of %s' % self._db_name(epoch))
        with Task() as task:
            existence = ops.Const(False)
            ops.DBExists(
                [],
                [existence],
                db_name=self._db_name(epoch),
                db_type=self._db_type,
                absolute_path=True)
            task.add_output(existence)
        return task

    def save(self, epoch):
        """
        Build a Task that is run once after `init_group` and after each
        epoch is run. This will execute a Save ops to serialize and persist
        blobs present in the global workspaace.
        """
        logger.info('Save to %s' % self._db_name(epoch))
        with Task() as task:
            ops.Save(
                self.blob_list(), [], db=self._db_name(epoch),
                db_type=self._db_type, absolute_path=True)
        return task


class MultiNodeCheckpointManager(object):
    """
    Coordinates checkpointing and checkpointing across multiple nodes.
    Each of `init`, `load` and `save` will build TaskGroups which will
    trigger checkpointing on each of the nodes involved in a distributed job.
    """
    def __init__(
            self, db_prefix, db_type, node_manager_class=CheckpointManager):
        self._node_manager_class = node_manager_class
        self._node_managers = None
        self._db_prefix = db_prefix
        self._db_type = db_type

    def _task_group(self, func, *args, **kw):
        assert self._node_managers is not None, 'init must be called first.'
        with TaskGroup(WorkspaceType.GLOBAL) as task_group:
            for node, manager in self._node_managers:
                with Node(node):
                    func(manager, *args, **kw)
            return task_group

    def init(self, nodes, retrieve_from_epoch=None):
        if self._node_managers is not None:
            assert [node for node, _ in self._node_managers] == nodes
            return
        self._node_managers = []
        for node in nodes:
            with Node(node):
                manager = self._node_manager_class(
                    db=os.path.join(self._db_prefix, node),
                    db_type=self._db_type)
                self._node_managers.append((node, manager))
        return self._task_group(
            self._node_manager_class.init,
            nodes=[node],
            retrieve_from_epoch=retrieve_from_epoch)

    def load(self, epoch):
        return self._task_group(self._node_manager_class.load, epoch)

    def load_blobs_locally(self, nodes, blob_names, epoch, session):
        """Loads the necessary blobs from the checkpoints to the current node.

        Args:
            blob_names: A list of strings. Each string is the name of a
                blob.
            epoch: An integer. The checkpoint epoch to load from.
            session: A Session object to execute the Load ops.
        """
        if self._node_managers is not None:
            assert [node for node, _ in self._node_managers] == nodes
        else:
            self._node_managers = []
            for node in nodes:
                with Node(node):
                    manager = self._node_manager_class(
                        db=os.path.join(self._db_prefix, node),
                        db_type=self._db_type)
                    self._node_managers.append((node, manager))
        assert self._node_managers is not None, 'must initialize node managers'
        for _, manager in self._node_managers:
            existence_task = manager.check_db_exists(epoch)
            session.run(existence_task)
            existence = existence_task.outputs()[0].fetch()
            if not existence:
                logger.info('DB %s does not exist!' % manager._db_name(epoch))
                return False
            load_task = manager.load_blobs_from_checkpoint(blob_names, epoch)
            session.run(load_task)
        logger.info('Successfully loaded from checkpoints.')
        return True

    def save(self, epoch):
        return self._task_group(self._node_manager_class.save, epoch)


class JobRunner(object):
    """
    Implement the runtime logic for jobs with checkpointing at the level of
    epoch. Can be used to run either single-host or distributed jobs. Job
    runner is a callable to be called once from the client, passing a Session
    as argument. This call will block until the Job execution is complete.

    If a checkpoint_manager is passed, checkpoints will be taken after
    initialization and after each epoch execution. If, in addition,
    `resume_from_epoch` is an epoch number, the corresponding checkpoint will
    be loaded and job execution will continue from the given epoch. In
    this case, the job's init_group will not be run.

    Refer to checkpoint_test.py for an example.
    """
    def __init__(self, job, checkpoint_manager=None, resume_from_epoch=None):
        self.resume_from_epoch = resume_from_epoch
        self.checkpoint = checkpoint_manager
        self.job = job

    def __call__(self, client):
        from_scratch = self.resume_from_epoch is None
        if from_scratch:
            client.run(self.job.init_group)

        if self.checkpoint:
            logger.info('Preparing checkpoint ...')
            client.run(self.checkpoint.init(
                self.job.nodes_to_checkpoint(),
                retrieve_from_epoch=self.resume_from_epoch))
            if from_scratch:
                logger.info('Saving first checkpoint ...')
                client.run(self.checkpoint.save(0))
                logger.info('First checkpoint saved.')
            else:
                logger.info('Loading checkpoint for epoch {} ...'.format(
                    self.resume_from_epoch))
                client.run(self.checkpoint.load(self.resume_from_epoch))
                logger.info('Checkpoint loaded.')

        epoch = 1 if from_scratch else self.resume_from_epoch + 1
        while True:
            logger.info('Starting epoch %d.' % epoch)
            client.run(self.job.epoch_group)
            logger.info('Ran epoch %d.' % epoch)
            stop_signals = [o.fetch() for o in self.job.stop_signals]

            if self.checkpoint:
                logger.info('Saving checkpoint ...')
                client.run(self.checkpoint.save(epoch))
                logger.info('Checkpoint saved.')

            if any(stop_signals):
                logger.info('Stopping.')
                break
            epoch += 1
        client.run(self.job.exit_group)
        return epoch

    def load_blobs_from_checkpoints(self, blob_names, epoch, session):
        """Loads the necessary blobs from the checkpoints.

        Checkpoints store the snapshots of the workspace in each node.
        Sometimes we only need to load a subset of the blobs from the
        checkpoints. One common scenario is to load only the model blobs from
        the checkpoints for evaluation purpose. Given the names of the necessary
        blobs, this function goes over all the checkpoints of all the nodes, but
        only loads the blobs specified in the blob_names to the current
        workspace.

        Args:
            blob_names: A list of strings. Each string is the name of a
                blob.
            epoch: An integer. The checkpoint epoch to load from.
            session: A Session object to execute the load ops.

        Raises:
            ValueError: When the checkpoint manager is invalid.
        """
        if not self.checkpoint:
            raise ValueError('Checkpoint manager is None')
        logger.info('Loading checkpoint for epoch {} ...'.format(epoch))
        return self.checkpoint.load_blobs_locally(self.job.nodes_to_checkpoint(),
                                                  blob_names, epoch, session)


def epoch_limiter(num_epochs):
    """
    Creates a task that will output True when a given
    number of epochs has finished.
    """
    with Job.current().init_group:
        init_net = core.Net('epoch_counter_init')
        counter = init_net.CreateCounter([], init_count=num_epochs - 1)
        Task(step=init_net)
    epoch_net = core.Net('epoch_countdown')
    finished = epoch_net.CountDown(counter)
    output = Task(step=epoch_net, outputs=finished).outputs()[0]
    Job.current().add_stop_signal(output)
