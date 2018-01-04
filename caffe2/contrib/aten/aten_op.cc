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

#include "aten_op.h"
#include "caffe2/utils/math.h"

namespace caffe2 {

REGISTER_CPU_OPERATOR(ATen, ATenOp<CPUContext>);
template<>
at::Backend ATenOp<CPUContext>::backend() const {
  return at::kCPU;
}

OPERATOR_SCHEMA(ATen);
CAFFE_KNOWN_TYPE(at::Half);

namespace math {
template<>
void Set<at::Half,CPUContext>(const size_t N, const at::Half h, at::Half* v, CPUContext * c) {
  Set(0, h.x, (uint16_t*) v, c);
}
}

}
