#include "caffe2/operators/conv_op.h"
#include "caffe2/operators/conv_op_impl.h"
#include "caffe2/core/context_gpu.h"

namespace caffe2 {
namespace {
REGISTER_CUDA_OPERATOR(Conv, ConvOp<float, CUDAContext>);
REGISTER_CUDA_OPERATOR(ConvGradient, ConvGradientOp<float, CUDAContext>);
}  // namespace
}  // namespace caffe2
