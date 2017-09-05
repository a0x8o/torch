#include "caffe2/core/net_async_dag_gpu.h"

#include <set>
#include <stack>
#include <unordered_map>
#include <unordered_set>

#include "caffe2/core/operator.h"
#include "caffe2/core/static_tracepoint.h"
#include "caffe2/core/timer.h"
#include "caffe2/proto/caffe2.pb.h"
#include "caffe2/utils/proto_utils.h"

#ifdef CAFFE2_USE_NVTX
#include <nvToolsExt.h>
#endif

CAFFE2_DEFINE_bool(caffe2_use_nvtx, false, "Use NVTX ranges for profiling");

namespace caffe2 {

namespace {

using Color = int32_t;
constexpr Color kRunColor = 0x0000CCFF; // blue
constexpr Color kRecordColor = 0x00FF3300; // red
constexpr Color kWaitColor = 0x0066FF33; // green

#ifdef CAFFE2_USE_NVTX

class ProfiledRange {
 public:
  ProfiledRange(const OperatorDef& def, Color color) {
    if (!FLAGS_caffe2_use_nvtx) {
      return;
    }
    nvtxEventAttributes_t eventAttrib = {0};
    eventAttrib.version = NVTX_VERSION;
    eventAttrib.size = NVTX_EVENT_ATTRIB_STRUCT_SIZE;
    eventAttrib.colorType = NVTX_COLOR_ARGB;
    eventAttrib.color = color;
    eventAttrib.messageType = NVTX_MESSAGE_TYPE_ASCII;
    eventAttrib.message.ascii = def.type().c_str();
    range_ = nvtxRangeStartEx(&eventAttrib);
    CAFFE_ENFORCE(range_, "Start range is invalid.");
  }

  ~ProfiledRange() {
    if (!FLAGS_caffe2_use_nvtx) {
      return;
    }
    nvtxRangeEnd(range_);
  }

 private:
  nvtxRangeId_t range_ = 0;
  DISABLE_COPY_AND_ASSIGN(ProfiledRange);
};

#else

class ProfiledRange {
 public:
  ProfiledRange(const OperatorDef& def, Color color) {}

 private:
  DISABLE_COPY_AND_ASSIGN(ProfiledRange);
};

#endif // ifdef CAFFE2_USE_NVTX

} // namespace

AsyncDAGNet::AsyncDAGNet(
    const std::shared_ptr<const NetDef>& net_def,
    Workspace* ws)
    : DAGNetBase(net_def, ws) {
  VLOG(1) << "Constructing Async DAG Net " << net_def->name();
  eventRecorded_.resize(net_def->op_size());
}

bool AsyncDAGNet::RunAt(const std::vector<int>& chain) {
  CAFFE_ENFORCE(!chain.empty(), "Chain should not be empty.");
  const auto source_idx = chain.front();
  const auto& parents = operator_nodes_[source_idx].parents_;
  // Help ensure that our chaining is correct by verifying at least
  // one parent recorded an event.
  CAFFE_ENFORCE(
      parents.empty() ||
          std::any_of(
              parents.begin(),
              parents.end(),
              [this](int p) { return eventRecorded_[p]; }),
      "None of the parent is recorded for an event.");

  for (auto source_parent_idx : operator_nodes_[source_idx].parents_) {
    ProfiledRange r(
        operator_nodes_[source_parent_idx].operator_->debug_def(), kWaitColor);
    operator_nodes_[source_idx].operator_->Wait(
        *operator_nodes_[source_parent_idx].operator_);
  }

  // We've waited on all our parent indices.
  bool success = true;
  for (auto idx : chain) {
    ProfiledRange r(operator_nodes_[idx].operator_->debug_def(), kRunColor);
    success &= operator_nodes_[idx].operator_->RunAsync();
  }

  // Record an event for the sink of the chain.
  const auto& sink_idx = chain.back();
  {
    ProfiledRange r(
        operator_nodes_[sink_idx].operator_->debug_def(), kRecordColor);
    operator_nodes_[sink_idx].operator_->Record();
  }
  CAFFE_ENFORCE(
      !eventRecorded_[sink_idx],
      "An event for ",
      sink_idx,
      " should not be recorded.");
  eventRecorded_[sink_idx] = 1;
  return success;
}

bool AsyncDAGNet::Run() {
  // Reset the event tracking at each iteration
  eventRecorded_.assign(eventRecorded_.size(), 0);

  const auto result = DAGNetBase::Run();

  // Potential optimization: we can pre-compute outstanding events, as some
  // chain's tail may already be covered by other chains.
  for (const auto& chain : execution_chains_) {
    const int tail_op_idx = chain.second.back();
    operator_nodes_[tail_op_idx].operator_->event().Finish();
  }
  return result;
}

REGISTER_NET(async_dag, AsyncDAGNet);

} // namespace caffe2
