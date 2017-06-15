## @package net_printer
# Module caffe2.python.net_printer
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from caffe2.proto.caffe2_pb2 import OperatorDef
from caffe2.python.checkpoint import Job
from caffe2.python.core import Net, ExecutionStep, Plan
from caffe2.python.task import Task, TaskGroup, WorkspaceType, TaskOutput
from collections import defaultdict
from contextlib import contextmanager
from copy import copy


class Visitor(object):
    @classmethod
    def register(cls, Type):
        if not(hasattr(cls, 'visitors')):
            cls.visitors = []

        def _register(func):
            cls.visitors.append((Type, func))
            return func

        return _register

    def __call__(self, obj, *args, **kwargs):
        if obj is None:
            return
        for Type, func in self.__class__.visitors:
            if isinstance(obj, Type):
                return func(self, obj, *args, **kwargs)
        raise TypeError('%s: unsupported object type: %s' % (
            self.__class__.__name__, type(obj)))


class Analyzer(Visitor):
    PREFIXES_TO_IGNORE = {'distributed_ctx_init'}

    def __init__(self):
        self.workspaces = defaultdict(lambda: defaultdict(lambda: 0))
        self.workspace_ctx = []

    @property
    def workspace(self):
        return self.workspace_ctx[-1]

    @contextmanager
    def set_workspace(self, node=None, ws=None, do_copy=False):
        if ws is not None:
            ws = ws
        elif node is not None:
            ws = self.workspaces[str(node)]
        else:
            ws = self.workspace
        if do_copy:
            ws = copy(ws)
        self.workspace_ctx.append(ws)
        yield ws
        del self.workspace_ctx[-1]

    def define_blob(self, blob):
        self.workspace[blob] += 1

    def need_blob(self, blob):
        if any(blob.startswith(p) for p in Analyzer.PREFIXES_TO_IGNORE):
            return
        assert blob in self.workspace, 'Blob undefined: %s' % blob


@Analyzer.register(OperatorDef)
def analyze_op(analyzer, op):
    map(analyzer.need_blob, op.input)
    map(analyzer.define_blob, op.output)


@Analyzer.register(Net)
def analyze_net(analyzer, net):
    map(analyzer, net.Proto().op)


@Analyzer.register(ExecutionStep)
def analyze_step(analyzer, step):
    proto = step.Proto()
    if proto.report_net:
        with analyzer.set_workspace(do_copy=True):
            analyzer(step.get_net(proto.report_net))
    all_new_blobs = set()
    substeps = step.Substeps() + [step.get_net(n) for n in proto.network]
    for substep in substeps:
        with analyzer.set_workspace(do_copy=proto.concurrent_substeps) as ws_in:
            analyzer(substep)
            if proto.should_stop_blob:
                analyzer.need_blob(proto.should_stop_blob)
        if proto.concurrent_substeps:
            new_blobs = set(ws_in.keys()) - set(analyzer.workspace.keys())
            assert len(all_new_blobs & new_blobs) == 0, (
                'Error: Blobs created by multiple parallel steps: %s' % (
                    ', '.join(all_new_blobs & new_blobs)))
            all_new_blobs |= new_blobs
    map(analyzer.define_blob, all_new_blobs)


@Analyzer.register(Task)
def analyze_task(analyzer, task):
    # check that our plan protobuf is not too large (limit of 64Mb)
    step = task.get_step()
    plan = Plan(task.node)
    plan.AddStep(step)
    proto_len = len(plan.Proto().SerializeToString())
    assert proto_len < 2 ** 26, (
        'Due to a protobuf limitation, serialized tasks must be smaller '
        'than 64Mb, but this task has {} bytes.' % proto_len)

    is_private = task.workspace_type() != WorkspaceType.GLOBAL
    with analyzer.set_workspace(do_copy=is_private):
        analyzer(step)


@Analyzer.register(TaskGroup)
def analyze_task_group(analyzer, tg):
    for task in tg.tasks_by_node().tasks():
        with analyzer.set_workspace(node=task.node):
            analyzer(task)


@Analyzer.register(Job)
def analyze_job(analyzer, job):
    analyzer(job.init_group)
    analyzer(job.epoch_group)


def analyze(obj):
    """
    Given a Job, visits all the execution steps making sure that:
      - no undefined blobs will be found during excution
      - no blob with same name is defined in concurrent steps
    """
    Analyzer()(obj)


class Text(object):
    def __init__(self):
        self._indent = 0
        self._lines_in_context = [0]
        self.lines = []

    @contextmanager
    def context(self, text):
        if text is not None:
            self.add('with %s:' % text)
            self._indent += 4
            self._lines_in_context.append(0)
        yield
        if text is not None:
            if self._lines_in_context[-1] == 0:
                self.add('pass')
            self._indent -= 4
            del self._lines_in_context[-1]

    def add(self, text):
        self._lines_in_context[-1] += 1
        self.lines.append((' ' * self._indent) + text)

    def __str__(self):
        return '\n'.join(self.lines)


class Printer(Visitor, Text):
    def __init__(self, factor_prefixes=False):
        super(Visitor, self).__init__()
        super(Text, self).__init__()
        self.factor_prefixes = factor_prefixes


def _sanitize_str(s):
    s = str(s)
    return s if len(s) < 64 else (s[:64] + '...<+len=%d>' % (len(s) - 64))


def _arg_val(arg):
    if arg.HasField('f'):
        return str(arg.f)
    if arg.HasField('i'):
        return str(arg.i)
    if arg.HasField('s'):
        return _sanitize_str(arg.s)
    if arg.floats:
        return str(list(arg.floats))
    if arg.ints:
        return str(list(arg.ints))
    if arg.strings:
        return str([_sanitize_str(s) for s in arg.strings])
    return '[]'


def commonprefix(m):
    "Given a list of strings, returns the longest common prefix"
    if not m:
        return ''
    s1 = min(m)
    s2 = max(m)
    for i, c in enumerate(s1):
        if c != s2[i]:
            return s1[:i]
    return s1


def factor_prefix(vals, do_it):
    vals = map(str, vals)
    prefix = commonprefix(vals) if len(vals) > 1 and do_it else ''
    joined = ', '.join(v[len(prefix):] for v in vals)
    return '%s[%s]' % (prefix, joined) if prefix else joined


def call(op, inputs=None, outputs=None, factor_prefixes=False):
    if not inputs:
        inputs = ''
    else:
        inputs_v = [a for a in inputs if not isinstance(a, tuple)]
        inputs_kv = [a for a in inputs if isinstance(a, tuple)]
        inputs = ', '.join(filter(
            bool,
            [factor_prefix(inputs_v, factor_prefixes)] +
            ['%s=%s' % kv for kv in inputs_kv]))
    call = '%s(%s)' % (op, inputs)
    return call if not outputs else '%s = %s' % (
        factor_prefix(outputs, factor_prefixes), call)


@Printer.register(OperatorDef)
def print_op(text, op):
    text.add(call(
        op.type,
        list(op.input) + [(a.name, _arg_val(a)) for a in op.arg],
        op.output,
        factor_prefixes=text.factor_prefixes))


@Printer.register(Net)
def print_net(text, net):
    text.add('# net: %s' % str(net))
    for op in net.Proto().op:
        text(op)


def _get_step_context(step):
    proto = step.Proto()
    if proto.should_stop_blob:
        return call('loop'), False
    if proto.num_iter and proto.num_iter != 1:
        return call('loop', [proto.num_iter]), False
    concurrent = proto.concurrent_substeps and len(step.Substeps()) > 1
    if concurrent:
        return call('parallel'), True
    if proto.report_net:
        return call('run_once'), False
    return None, False


@Printer.register(ExecutionStep)
def print_step(text, step):
    proto = step.Proto()
    step_ctx, do_substep = _get_step_context(step)
    with text.context(step_ctx):
        if proto.report_net:
            with text.context(call('report_net', [proto.report_interval])):
                text(step.get_net(proto.report_net))
        substeps = step.Substeps() + [step.get_net(n) for n in proto.network]
        for substep in substeps:
            if (isinstance(substep, ExecutionStep) and
                    substep.Proto().run_every_ms):
                substep_ctx = call(
                    'reporter',
                    [str(substep), ('interval_ms', substep.Proto().run_every_ms)])
            elif do_substep:
                substep_ctx = call('step', [str(substep)])
            else:
                substep_ctx = None
            with text.context(substep_ctx):
                text(substep)
                if proto.should_stop_blob:
                    text.add(call('yield stop_if', [proto.should_stop_blob]))


def _print_task_output(x):
    assert isinstance(x, TaskOutput)
    return 'Output[' + ', '.join(map(str, x.names)) + ']'


@Printer.register(Task)
def print_task(text, task):
    outs = ', '.join(map(_print_task_output, task.outputs()))
    context = [('node', task.node), ('name', task.name), ('outputs', outs)]
    with text.context(call('Task', context)):
        text(task.get_step())


@Printer.register(TaskGroup)
def print_task_group(text, tg, header=None):
    with text.context(header or call('TaskGroup')):
        for task in tg.tasks_by_node().tasks():
            text(task)


@Printer.register(Job)
def print_job(text, job):
    text(job.init_group, 'Job.current().init_group')
    text(job.epoch_group, 'Job.current().epoch_group')
    with text.context('Job.current().stop_signals'):
        for out in job.stop_signals:
            text.add(_print_task_output(out))
    text(job.exit_group, 'Job.current().exit_group')


def to_string(obj):
    """
    Given a Net, ExecutionStep, Task, TaskGroup or Job, produces a string
    with detailed description of the execution steps.
    """
    printer = Printer()
    printer(obj)
    return str(printer)


def debug_net(net):
    """
    Given a Net, produce another net that logs info about the operator call
    before each operator execution. Use for debugging purposes.
    """
    assert isinstance(net, Net)
    debug_net = Net(str(net))
    assert isinstance(net, Net)
    for op in net.Proto().op:
        text = Text()
        print_op(op, text)
        debug_net.LogInfo(str(text))
        debug_net.Proto().op.extend([op])
    return debug_net
