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

#include "caffe2/operators/top_k.h"

#include <thrust/sort.h>
#include <thrust/system/cuda/execution_policy.h>
#include "caffe2/core/context.h"
#include "caffe2/core/context_gpu.h"
#include "caffe2/operators/top_k_heap_selection.cuh"
#include "caffe2/operators/top_k_radix_selection.cuh"

namespace caffe2 {

// Converts a matrix of size [outerSize, k] containing
// row-wise indices into global (linearized) indices from an original
// matrix of [outerSize, innerSize]
template <typename Index>
__global__ void linearizeRowIndices(
    Index* in,
    Index* out,
    int outerSize,
    int innerSize,
    int k) {
  if (blockIdx.x < outerSize) {
    in += (Index)blockIdx.x * k;
    out += (Index)blockIdx.x * k;

    auto indexOffset = (Index)blockIdx.x * innerSize;
    for (int i = threadIdx.x; i < k; i += blockDim.x) {
      out[i] = in[i] + indexOffset;
    }
  }
}

template <>
class TopKOp<float, CUDAContext> : public Operator<CUDAContext> {
 public:
  TopKOp(const OperatorDef& operator_def, Workspace* ws)
      : Operator<CUDAContext>(operator_def, ws),
        OP_SINGLE_ARG(int, "k", k_, -1) {}

  bool RunOnDevice() override;

 private:
  int k_;
};

bool TopKOp<float, CUDAContext>::RunOnDevice() {
  auto& input = Input(0);
  auto* values = Output(0);
  auto* indices = Output(1);
  auto* flatten_indices = OutputSize() > 2 ? Output(2) : nullptr;

  vector<TIndex> in_dims = input.dims();
  CAFFE_ENFORCE(
      in_dims.back() >= k_, "k argment should not be greater than last dim");

  vector<TIndex> out_dims = in_dims;
  out_dims.back() = k_;

  // Get the batch size
  size_t outerSize = 1;
  for (int i = 0; i < in_dims.size() - 1; ++i) {
    outerSize *= in_dims[i];
  }

  values->Resize(out_dims);
  indices->Resize(out_dims);
  if (flatten_indices) {
    flatten_indices->Resize(outerSize * k_);
  }

  // Right now, the top-k operator only supports max-k
  constexpr bool kDir = true;

  if (k_ <= 512) {
    // heap selection is possible
    constexpr int kBlockSize = 256;
    int numWarps = kBlockSize / kWarpSize;

    auto grid = outerSize;
    auto block = kBlockSize;

#define RUN_HEAP(HEAP_SIZE)                                               \
  do {                                                                    \
    int smem = numWarps * HEAP_SIZE * (sizeof(float) + sizeof(TIndex));   \
                                                                          \
    selectRowsViaHeap<float, TIndex, TIndex, kBlockSize, HEAP_SIZE, kDir> \
        <<<grid, block, smem, context_.cuda_stream()>>>(                  \
            input.data<float>(),                                          \
            values->mutable_data<float>(),                                \
            indices->mutable_data<TIndex>(),                              \
            kDir ? -std::numeric_limits<float>::infinity()                \
                 : std::numeric_limits<float>::infinity(),                \
            kDir ? -std::numeric_limits<TIndex>::max()                    \
                 : std::numeric_limits<float>::max(),                     \
            outerSize,                                                    \
            in_dims.back(),                                               \
            k_);                                                          \
  } while (false)

    if (k_ <= 32) {
      RUN_HEAP(32);
    } else if (k_ <= 128) {
      RUN_HEAP(128);
    } else {
      RUN_HEAP(512);
    }

#undef RUN_HEAP

  } else {
    // k is too large, use radix selection instead
    auto grid = outerSize;
    auto block = std::min(
        math::roundUp((int)in_dims.back(), kWarpSize), CAFFE_CUDA_NUM_THREADS);

    // Radix selection required
    gatherTopK<float, kDir, TIndex><<<grid, block, 0, context_.cuda_stream()>>>(
        input.data<float>(),
        in_dims.back(),
        k_,
        outerSize,
        values->mutable_data<float>(),
        indices->mutable_data<TIndex>());

    // Unfortunately the output is not currently sorted, and there is
    // no batch sorting utility available. Iterate over all of the
    // slices and sort them in-place using Thrust.
    for (int slice = 0; slice < outerSize; ++slice) {
      thrust::sort_by_key(
          thrust::cuda::par.on(context_.cuda_stream()),
          values->mutable_data<float>() + slice * k_,
          values->mutable_data<float>() + slice * k_ + k_,
          indices->mutable_data<TIndex>() + slice * k_,
          thrust::greater<float>());
    }
  }

  // Now that we've completed writing the indices, linearize the
  // indices if we need it
  if (flatten_indices) {
    linearizeRowIndices<TIndex>
        <<<outerSize, CAFFE_CUDA_NUM_THREADS, 0, context_.cuda_stream()>>>(
            indices->mutable_data<TIndex>(),
            flatten_indices->mutable_data<TIndex>(),
            outerSize,
            in_dims.back(),
            k_);
  }

  return true;
}

REGISTER_CUDA_OPERATOR(TopK, TopKOp<float, CUDAContext>);

__global__ void fillValuesWithIndicesKernel(
    const float* values,
    const TIndex* indices,
    const TIndex k,
    const TIndex orignal_last_dim,
    const TIndex length,
    float* output) {
  CUDA_1D_KERNEL_LOOP(i, length) {
    int first_dim = i / k;
    int idx = orignal_last_dim * first_dim + indices[i];
    output[idx] = values[i];
  }
}

template <>
bool TopKGradientOp<float, CUDAContext>::RunOnDevice() {
  auto& values = Input(0);
  auto& indices = Input(1);
  auto& original_input = Input(2);

  vector<TIndex> in_dims = values.dims();

  // Linearize input tensor except for last dimension
  // e.g. [3, 4, 5] -> [12, 5]
  // [5] -> [5]
  TIndex flatten_shape[] = {size_to_dim_(in_dims.size() - 1, in_dims),
                            in_dims[in_dims.size() - 1]};

  vector<TIndex> original_dims = original_input.dims();
  auto* output = Output(0);
  output->Resize(original_dims);

  float* output_data = output->mutable_data<float>();
  math::Set<float, CUDAContext>(
      output->size(), float(0), output_data, &context_);

  int length = flatten_shape[0] * flatten_shape[1];
  if (length == 0) { // for empty batch
    return true;
  }

  int num_threads = std::min(CAFFE_CUDA_NUM_THREADS, length);
  int blocks = math::divUp(length, num_threads);

  fillValuesWithIndicesKernel<<<
      blocks,
      num_threads,
      0,
      context_.cuda_stream()>>>(
      values.data<float>(),
      indices.data<TIndex>(),
      flatten_shape[1],
      original_dims.back(),
      length,
      output_data);

  return true;
}

REGISTER_CUDA_OPERATOR(TopKGradient, TopKGradientOp<float, CUDAContext>);
} // namespace caffe2
