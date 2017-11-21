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

#include "caffe2/operators/transpose_op.h"
#include "caffe2/operators/transpose_op_gpu.h"
#include <limits>

#include "caffe2/core/context_gpu.h"

namespace caffe2 {

// Cuda memory is precious so let's do a lower ndim limit.
#define COMPILE_TIME_CUDA_MAX_TRANSPOSE_DIMS 6

namespace {
// TODO(jiayq): one possible optimization is to copy the buffer into a shared
// memory location to speed up access.
template <typename Dtype>
__global__ void transpose_gpu(const int nthreads, const Dtype* from_data,
  Dtype* to_data, const int* buffer, const int num_axes) {
  int from_inds[COMPILE_TIME_CUDA_MAX_TRANSPOSE_DIMS];
  const int* from_counts = buffer;
  const int* to_counts = buffer + num_axes;
  const int* axes = buffer + num_axes * 2;
  CUDA_1D_KERNEL_LOOP(index, nthreads) {
    int from_index = index, to_index = 0;
    for (int i = num_axes - 1; i >= 0; --i) {
      from_inds[i] = from_index % from_counts[i];
      from_index = from_index / from_counts[i];
    }
    for (int i = 0; i < num_axes - 1; i++) {
      to_index = (to_index + from_inds[axes[i]]) * to_counts[i + 1];
    }
    to_index += from_inds[axes[num_axes - 1]];
    to_data[to_index] = from_data[index];
  }
}
}  // namespace

template <>
template <typename T>
bool TransposeOp<CUDAContext>::DoRunWithType() {
  const auto& input = Input(0);
  auto* output = Output(0);
  return TransposeCUDA<T>(axes_, context_, input, output, buffer_cpu_, buffer_);
}

template <typename T>
bool TransposeCUDA(
    vector<int>& axes,
    CUDAContext& context,
    const Tensor<CUDAContext>& input,
    Tensor<CUDAContext>* output,
    TensorCPU& buffer_cpu,
    Tensor<CUDAContext>& buffer) {
  int count = input.size();
  int ndim = input.ndim();
  CAFFE_ENFORCE(
      count < std::numeric_limits<int>::max(),
      "Transpose op on GPU only supports int32");
  CAFFE_ENFORCE(
      ndim <= COMPILE_TIME_CUDA_MAX_TRANSPOSE_DIMS,
      "Input ndim exceeds compile time max.");

  // Buffer contains the following data:
  // (1) the dimenions of the inputs
  // (2) the dimension of the outputs
  // (3) the axis mapping from inputs to outputs
  buffer_cpu.Resize(3 * ndim);
  int* buffer_data = buffer_cpu.mutable_data<int>();
  for (int i = 0; i < ndim; ++i) {
    *(buffer_data++) = input.dim32(i);
  }
  for (int i = 0; i < ndim; ++i) {
    *(buffer_data++) = output->dim32(i);
  }
  for (int i = 0; i < ndim; ++i) {
    *(buffer_data++) = axes[i];
  }
  // Copy the dimension information to GPU.
  buffer.CopyFrom(buffer_cpu, &context);
  transpose_gpu<T>
      <<<CAFFE_GET_BLOCKS(count),
         CAFFE_CUDA_NUM_THREADS,
         0,
         context.cuda_stream()>>>(
          count,
          input.template data<T>(),
          output->template mutable_data<T>(),
          buffer.data<int>(),
          ndim);
  return true;
}

REGISTER_CUDA_OPERATOR(Transpose, TransposeOp<CUDAContext>);
} // namespace caffe2
