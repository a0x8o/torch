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

#ifndef CAFFE2_OPERATORS_CLIP_OP_H_
#define CAFFE2_OPERATORS_CLIP_OP_H_

#include <limits>

#include "caffe2/core/context.h"
#include "caffe2/core/logging.h"
#include "caffe2/core/operator.h"
#include "caffe2/utils/math.h"

namespace caffe2 {

template <typename T, class Context>
class ClipOp final : public Operator<Context> {
 public:
  USE_OPERATOR_CONTEXT_FUNCTIONS;
  ClipOp(const OperatorDef& operator_def, Workspace* ws)
      : Operator<Context>(operator_def, ws),
        min_(std::numeric_limits<T>::lowest()),
        max_(std::numeric_limits<T>::max()) {
    if (HasArgument("min")) {
      min_ = static_cast<T>(OperatorBase::GetSingleArgument<float>("min", 0));
    }
    if (HasArgument("max")) {
      max_ = static_cast<T>(OperatorBase::GetSingleArgument<float>("max", 0));
    }
  }

  bool RunOnDevice() override;

 protected:
  T min_;
  T max_;
};

template <typename T, class Context>
class ClipGradientOp final : public Operator<Context> {
 public:
  USE_OPERATOR_CONTEXT_FUNCTIONS;
  ClipGradientOp(const OperatorDef& operator_def, Workspace* ws)
      : Operator<Context>(operator_def, ws),
        min_(std::numeric_limits<T>::lowest()),
        max_(std::numeric_limits<T>::max()) {
    if (HasArgument("min")) {
      min_ = static_cast<T>(OperatorBase::GetSingleArgument<float>("min", 0));
    }
    if (HasArgument("max")) {
      max_ = static_cast<T>(OperatorBase::GetSingleArgument<float>("max", 0));
    }
  }

  bool RunOnDevice() override;

 protected:
  T min_;
  T max_;
  // Input: Y, dY; Output: dX
};

} // namespace caffe2

#endif // CAFFE2_OPERATORS_CLIP_OP_H_
