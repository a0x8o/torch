#include "caffe2/core/context_gpu.h"
#include "caffe2/operators/zero_gradient_op.h"

namespace caffe2 {
<<<<<<< HEAD
REGISTER_CUDA_OPERATOR(ZeroGradient, ZeroGradientOp<CUDAContext>);
}
=======
namespace {
REGISTER_CUDA_OPERATOR(ZeroGradient, ZeroGradientOp<CUDAContext>);
}
}
>>>>>>> 3d8433f8b359d59d9f0db8e916b3a049262b55f3
