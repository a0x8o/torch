/**
 * Copyright (c) 2016-present, Facebook, Inc.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#include "caffe2/core/net_dag.h"

#include <set>
#include <stack>
#include <unordered_map>
#include <unordered_set>

#include "caffe2/core/operator.h"
#include "caffe2/core/static_tracepoint.h"
#include "caffe2/core/timer.h"
#include "caffe2/proto/caffe2.pb.h"
#include "caffe2/utils/proto_utils.h"

CAFFE2_DEFINE_bool(
    caffe2_disable_chaining,
    false,
    "Disable chaining logic (some latent multi-device issues).");

CAFFE2_DEFINE_bool(
    caffe2_dag_net_collect_stats,
    false,
    "Collect time stats in DAG net");

namespace caffe2 {

DAGNetBase::DAGNetBase(
    const std::shared_ptr<const NetDef>& net_def,
    Workspace* ws)
    : NetBase(net_def, ws), iter_(0) {
  // Blob creator allows us to track which operator created which blob.
  VLOG(1) << "Constructing DAGNet " << net_def->name();

  operator_nodes_ = dag_utils::prepareOperatorNodes(net_def, ws);

  execution_chains_ =
      (FLAGS_caffe2_disable_chaining
           ? dag_utils::singleChains(operator_nodes_)
           : dag_utils::computeChains(operator_nodes_));

  operators_.reserve(operator_nodes_.size());
  for (const auto& node : operator_nodes_) {
    operators_.push_back(node.operator_.get());
  }

  LOG(INFO) << "Number of parallel execution chains "
            << execution_chains_.size()
            << " Number of operators = " << net_def->op_size();
  // TODO: do we want to make sure that there are no loops in the
  // dependency graph?

  // Figure out the initial frontier - this is the one we will feed into the job
  // queue to start a run.
  for (int idx = 0; idx < operator_nodes_.size(); ++idx) {
    if (operator_nodes_[idx].parents_.size() == 0) {
      initial_frontier_.push_back(idx);
    }
  }
  // Finally, start the workers.
  int num_workers = net_def->has_num_workers() ? net_def->num_workers() : 1;
  CAFFE_ENFORCE(num_workers > 0, "Must have a positive number of workers.");
  if (num_workers == 1) {
    LOG(WARNING) << "Number of workers is 1: this means that all operators "
                 << "will be executed sequentially. Did you forget to set "
                 << "num_workers in the NetDef?";
  }
  num_workers_ = num_workers;

  for (int idx = 0; idx < operator_nodes_.size(); ++idx) {
    if (operator_nodes_[idx].is_chain_start_) {
      task_timers_[idx] = caffe2::make_unique<Timer>();
    }
  }
  stats_.reserve(DeviceType::COMPILE_TIME_MAX_DEVICE_TYPES);
  for (auto device_idx = 0;
       device_idx < DeviceType::COMPILE_TIME_MAX_DEVICE_TYPES;
       ++device_idx) {
    stats_.emplace_back(
        "dag_net/stats/" + net_def->name() + "/" +
        caffe2::DeviceTypeName(device_idx));
  }
}

DAGNetBase::~DAGNetBase() {
  if (job_queue_) {
    job_queue_->NoMoreJobs();
    VLOG(1) << "Joining workers.";
    for (auto& worker : workers_) {
      worker.join();
    }
  }
}

bool DAGNetBase::DoRunAsync() {
  StartAllObservers();

  // Lock run_in_progress_ to prevent concurrent Run()s.
  std::unique_lock<std::mutex> run_lock(run_in_progress_);
  VLOG(1) << "Running parallel net.";
  // First, set up job queue.
  remaining_ops_ = operator_nodes_.size();
  success_ = true;
  iter_++;
  if (!job_queue_) {
    job_queue_ = caffe2::make_unique<SimpleQueue<int>>();
  }
  // Figure out number of workers to start.
  auto num_workers_to_start = num_workers_ - workers_.size();

  // Ensure the number of workers matches the defined in case
  // any of the previously started threads terminated.
  for (auto i = 0; i < num_workers_to_start; i++) {
    VLOG(1) << "Start worker #" << workers_.size();
    workers_.push_back(std::thread(&DAGNetBase::WorkerFunction, this));
  }
  // Initialize the runtime parent count.
  for (auto& node : operator_nodes_) {
    node.runtime_parent_count_ = node.parents_.size();
  }
  // Kickstart the job queue.
  for (auto& value : initial_frontier_) {
    if (FLAGS_caffe2_dag_net_collect_stats) {
      task_timers_[value]->Start();
    }
    job_queue_->Push(value);
  }
  // Wait for failure or completed execution.
  {
    std::unique_lock<std::mutex> mutex_lock(remaining_ops_mutex_);
    for (;;) {
      if (remaining_ops_ == 0 || !success_) {
        break;
      }
      cv_.wait(mutex_lock);
    }
  }
  // Wait for all workers to terminate after failure.
  // If there is a failure, it is unlikely that the net is executed
  // again without modifications. Therefore it's easier to let the
  // workers terminate here, versus adding a drain state to make the
  // sure the job queue is cleared.
  if (!success_) {
    for (auto& worker : workers_) {
      worker.join();
    }
    workers_.clear();
    job_queue_.reset(nullptr);
    return success_;
  }
  VLOG(2) << "All ops finished running.";
  for (const auto& op : operator_nodes_) {
    CAFFE_ENFORCE(
        op.runtime_parent_count_ == 0,
        "Operator ",
        op.operator_->debug_def().name(),
        "(",
        op.operator_->debug_def().type(),
        ") has some runtime parents left.");
  }

  StopAllObservers();
  // If the above while loop finished, we know that the current run finished.
  return success_;
}

void DAGNetBase::WorkerFunction() {
  // WorkerFunctions() is an infinite loop until there are no more jobs to run.
  while (true) {
    int idx = 0;

    // Return if there are no more operators to run (e.g. the
    // DAGNetBase is destructing, or there was an error on another
    // worker and we're cleaning up).
    if (!job_queue_->Pop(&idx)) {
      return;
    }
    if (FLAGS_caffe2_dag_net_collect_stats) {
      auto device_option =
          operator_nodes_[idx].operator_->event().GetDeviceOption();
      CAFFE_EVENT(
          stats_[device_option.device_type()],
          task_pool_wait_time_us,
          task_timers_[idx]->MicroSeconds());
    }

    VLOG(1) << "Running operator #" << idx << " "
            << operator_nodes_[idx].operator_->debug_def().name() << "("
            << operator_nodes_[idx].operator_->debug_def().type() << ").";
    CAFFE_ENFORCE(
        execution_chains_.find(idx) != execution_chains_.end(),
        "Can't find chain ",
        idx,
        ".");
    const auto& chain = execution_chains_[idx];
    bool this_success = RunAt(idx, execution_chains_[idx]);
    if (!this_success) {
      LOG(ERROR) << "Operator chain failed: "
                 << ProtoDebugString(
                        operator_nodes_[idx].operator_->debug_def());
    }

    // Do book-keeping
    std::vector<int> chains_to_queue;
    for (const auto idx : chain) {
      for (const auto child : operator_nodes_[idx].children_) {
        const int count = --operator_nodes_[child].runtime_parent_count_;
        CAFFE_ENFORCE(
            count >= 0,
            "Found runtime parent count smaller than zero for ",
            "operator node ",
            operator_nodes_[child].operator_->debug_def().name(),
            "(",
            operator_nodes_[child].operator_->debug_def().type(),
            ").");

        if (count != 0) {
          continue;
        }

        if (operator_nodes_[child].is_chain_start_) {
          VLOG(2) << "Pushing chain #" << child << " to queue.";
          chains_to_queue.push_back(child);
        }
      }
    }

    // Notify the caller of Run
    {
      std::unique_lock<std::mutex> mutex_lock(remaining_ops_mutex_);
      remaining_ops_ -= chain.size();
      CAFFE_ENFORCE(remaining_ops_ >= 0);
      success_ &= this_success;
      if (remaining_ops_ == 0 || !success_) {
        cv_.notify_one();
      }

      // Terminate thread if this or any other operator chain failed.
      if (!success_) {
        job_queue_->NoMoreJobs();
        return;
      }

      // Queue follow up operator chains.
      // Can't do this inline because it can race with another thread
      // calling NoMoreJobs(). So the lock needs to be held on push.
      for (const auto idx : chains_to_queue) {
        if (FLAGS_caffe2_dag_net_collect_stats) {
          task_timers_[idx]->Start();
        }
        job_queue_->Push(idx);
      }
    }

    VLOG(2) << "Finished executing operator #" << idx;
  }
}

vector<float> DAGNetBase::TEST_Benchmark(
    const int warmup_runs,
    const int main_runs,
    const bool run_individual) {
  LOG(INFO) << "Starting benchmark.";
  LOG(INFO) << "Running warmup runs.";
  CAFFE_ENFORCE(
      warmup_runs >= 0,
      "Number of warm up runs should be non negative, provided ",
      warmup_runs,
      ".");
  for (int i = 0; i < warmup_runs; ++i) {
    CAFFE_ENFORCE(Run(), "Warmup run ", i, " has failed.");
  }

  LOG(INFO) << "Main runs.";
  CAFFE_ENFORCE(
      main_runs >= 0,
      "Number of main runs should be non negative, provided ",
      main_runs,
      ".");
  Timer timer;
  for (int i = 0; i < main_runs; ++i) {
    CAFFE_ENFORCE(Run(), "Main run ", i, " has failed.");
  }
  auto millis = timer.MilliSeconds();
  LOG(INFO) << "Main run finished. Milliseconds per iter: "
            << millis / main_runs
            << ". Iters per second: " << 1000.0 * main_runs / millis;

  if (run_individual) {
    LOG(INFO) << "DAGNet does not do per-op benchmark. To do so, "
                 "switch to a simple net type.";
  }
  return vector<float>{millis / main_runs};
}

bool DAGNet::RunAt(int chain_id, const std::vector<int>& chain) {
  const auto& net_name = name_.c_str();
  for (const auto i : chain) {
    const auto& opdef = operator_nodes_[i].operator_->debug_def();
    const auto& op = operator_nodes_[i].operator_.get();

    const auto& op_name = opdef.name().c_str();
    const auto& op_type = opdef.type().c_str();
    CAFFE_SDT(operator_start, net_name, op_name, op_type, op);
    const auto success = operator_nodes_[i].operator_->Run();
    CAFFE_SDT(operator_done, net_name, op_name, op_type, op);
    if (!success) {
      return false;
    }
  }
  if (FLAGS_caffe2_dag_net_collect_stats) {
    auto device_option =
        operator_nodes_[chain_id].operator_->event().GetDeviceOption();
    CAFFE_EVENT(
        stats_[device_option.device_type()],
        task_time_to_succeeded_ms,
        task_timers_[chain_id]->MilliSeconds());
  }
  return true;
}

REGISTER_NET(dag, DAGNet);

} // namespace caffe2
