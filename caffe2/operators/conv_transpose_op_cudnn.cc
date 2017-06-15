#include "caffe2/core/common_cudnn.h"
#include "caffe2/core/context_gpu.h"
#include "caffe2/operators/conv_op_cache_cudnn.h"
#include "caffe2/operators/conv_transpose_op.h"

namespace caffe2 {

// Earlier in the days Caffe sets the default cudnn workspace to 8MB. We bump
// it up to 64MB in Caffe2, as this enables the use of Winograd in many cases,
// something very beneficial to more recent CNN models.
static constexpr size_t kCONV_CUDNN_WORKSPACE_LIMIT_BYTES = 64 * 1024 * 1024;

// Manually specified number of algorithms implemented in CuDNN.
// This does not have any performance implications, as we will always find the
// fastest algorithm; setting them to the right number of algorithms will enable
// us to best report the statistics when doing an exhaustive search, though.
static constexpr size_t kNUM_CUDNN_FWD_ALGS = 7;
static constexpr size_t kNUM_CUDNN_BWD_FILTER_ALGS = 4;
static constexpr size_t kNUM_CUDNN_BWD_DATA_ALGS = 5;

namespace {
template <typename ArrayOfcudnnConvolutionAlgoPerf_t>
inline void LogCuDNNPerfStats(
    const ArrayOfcudnnConvolutionAlgoPerf_t& perf_stat,
    int returned_algo_count) {
  LOG(INFO) << "Perf result: (algo: stat, time, memory)";
  for (int i = 0; i < returned_algo_count; ++i) {
    const auto& stat = perf_stat[i];
    LOG(INFO) << stat.algo << ": " << stat.status << " " << stat.time << " "
              << stat.memory;
  }
}
} // namespace

class CudnnConvTransposeOpBase : public ConvTransposeUnpoolBase<CUDAContext> {
 public:
  CudnnConvTransposeOpBase(const OperatorDef& operator_def, Workspace* ws)
      : ConvTransposeUnpoolBase<CUDAContext>(operator_def, ws),
        cudnn_wrapper_(&context_),
        cudnn_ws_nbytes_limit_(OperatorBase::GetSingleArgument<size_t>(
            "ws_nbytes_limit",
            kCONV_CUDNN_WORKSPACE_LIMIT_BYTES)),
        exhaustive_search_(
            OperatorBase::GetSingleArgument<int>("exhaustive_search", 0)),
        deterministic_(
            OperatorBase::GetSingleArgument<int>("deterministic", 0)),
        cudnn_state_(OperatorBase::GetSingleArgument<int>("cudnn_state", 0)) {
    CAFFE_ENFORCE(!deterministic_ || !exhaustive_search_);
    CUDNN_ENFORCE(cudnnCreateTensorDescriptor(&bottom_desc_));
    CUDNN_ENFORCE(cudnnCreateFilterDescriptor(&filter_desc_));
    CUDNN_ENFORCE(cudnnCreateTensorDescriptor(&bias_desc_));
    CUDNN_ENFORCE(cudnnCreateTensorDescriptor(&top_desc_));
    CUDNN_ENFORCE(cudnnCreateConvolutionDescriptor(&conv_desc_));
  }

  ~CudnnConvTransposeOpBase() {
    CUDNN_ENFORCE(cudnnDestroyTensorDescriptor(bottom_desc_));
    CUDNN_ENFORCE(cudnnDestroyFilterDescriptor(filter_desc_));
    CUDNN_ENFORCE(cudnnDestroyTensorDescriptor(bias_desc_));
    CUDNN_ENFORCE(cudnnDestroyTensorDescriptor(top_desc_));
    CUDNN_ENFORCE(cudnnDestroyConvolutionDescriptor(conv_desc_));
  }

 protected:
  vector<TIndex> cudnn_input_dims_;
  vector<TIndex> cudnn_filter_dims_;

  CuDNNWrapper cudnn_wrapper_;
  cudnnTensorDescriptor_t bottom_desc_;
  cudnnFilterDescriptor_t filter_desc_;
  cudnnTensorDescriptor_t bias_desc_;
  cudnnTensorDescriptor_t top_desc_;
  cudnnConvolutionDescriptor_t conv_desc_;
  const size_t cudnn_ws_nbytes_limit_;
  size_t cudnn_ws_nbytes_;
  bool exhaustive_search_;
  bool deterministic_;
  size_t cudnn_state_;
};

template <typename T>
class CudnnConvTransposeOp final : public CudnnConvTransposeOpBase {
 public:
  CudnnConvTransposeOp(const OperatorDef& operator_def, Workspace* ws)
      : CudnnConvTransposeOpBase(operator_def, ws) {}

  ~CudnnConvTransposeOp() {}

  bool RunOnDevice() override;

 private:
  AlgorithmsCache<cudnnConvolutionBwdDataAlgo_t> data_algo_cache_;
  cudnnConvolutionBwdDataAlgo_t bwd_data_algo_;
  // Input: X, W, b
  // Output: Y
  INPUT_TAGS(INPUT, FILTER, BIAS);
};

template <typename T>
class CudnnConvTransposeGradientOp final : public CudnnConvTransposeOpBase {
 public:
  CudnnConvTransposeGradientOp(const OperatorDef& operator_def, Workspace* ws)
      : CudnnConvTransposeOpBase(operator_def, ws) {}

  ~CudnnConvTransposeGradientOp() {}

  bool RunOnDevice() override;

 private:
  cudnnConvolutionFwdAlgo_t algo_;
  cudnnConvolutionBwdFilterAlgo_t bwd_filter_algo_;
  AlgorithmsCache<cudnnConvolutionFwdAlgo_t> forward_algo_cache_;
  AlgorithmsCache<cudnnConvolutionBwdFilterAlgo_t> filter_algo_cache_;
  // input: X, W, dY
  // output: dW, db, and optionally dX
  INPUT_TAGS(INPUT, FILTER, OUTPUT_GRAD);
  OUTPUT_TAGS(FILTER_GRAD, BIAS_GRAD, INPUT_GRAD);
};

////////////////////////////////////////////////////////////////////////////////
// Implementations
////////////////////////////////////////////////////////////////////////////////

template <typename T>
bool CudnnConvTransposeOp<T>::RunOnDevice() {
  auto& X = Input(INPUT);
  auto& filter = Input(FILTER);
  auto& bias = Input(BIAS);
  auto* Y = Output(0);
  int C = 0;
  switch (order_) {
    case StorageOrder::NHWC:
      C = filter.dim32(3);
      break;
    case StorageOrder::NCHW:
      C = filter.dim32(1);
      break;
    default:
      LOG(FATAL) << "Unknown storage order: " << order_;
  }
  ConvTransposeUnpoolBase<CUDAContext>::SetOutputSize(X, Y, C);

  int N = 0, M = 0, H = 0, W = 0, H_out = 0, W_out = 0;
  switch (order_) {
    case StorageOrder::NHWC:
      N = X.dim32(0);
      H = X.dim32(1);
      W = X.dim32(2);
      M = X.dim32(3);
      H_out = Y->dim32(1);
      W_out = Y->dim32(2);
      DCHECK_EQ(filter.dim32(1), kernel_h_);
      DCHECK_EQ(filter.dim32(1), kernel_h_);
      DCHECK_EQ(filter.dim32(2), kernel_w_);
      DCHECK_EQ(filter.dim32(3), C);
      break;
    case StorageOrder::NCHW:
      N = X.dim32(0);
      M = X.dim32(1);
      H = X.dim32(2);
      W = X.dim32(3);
      H_out = Y->dim32(2);
      W_out = Y->dim32(3);
      DCHECK_EQ(filter.dim32(1), C);
      DCHECK_EQ(filter.dim32(2), kernel_h_);
      DCHECK_EQ(filter.dim32(3), kernel_w_);
      break;
    default:
      LOG(FATAL) << "Unknown storage order: " << order_;
  }

  DCHECK_EQ(bias.ndim(), 1);
  DCHECK_EQ(bias.dim32(0), C);

  // Set up the cudnn algorithms & workspace if necessary
  bool input_changed = (X.dims() != cudnn_input_dims_);
  bool filter_changed = (filter.dims() != cudnn_filter_dims_);

  if (input_changed || filter_changed) {
    VLOG(1) << "Changing the cudnn descriptor configurations.";
    if (input_changed) {
      cudnn_input_dims_ = X.dims();
      CUDNN_ENFORCE(cudnnSetTensor4dDescriptor(
          bottom_desc_,
          GetCudnnTensorFormat(order_),
          cudnnTypeWrapper<T>::type,
          N,
          M,
          H,
          W));
    }
    if (filter_changed) {
      cudnn_filter_dims_ = filter.dims();
      CUDNN_ENFORCE(cudnnSetFilter4dDescriptor(
          filter_desc_,
          cudnnTypeWrapper<T>::type,
          GetCudnnTensorFormat(order_),
          M,
          C,
          kernel_h_,
          kernel_w_));
      CUDNN_ENFORCE(cudnnSetTensor4dDescriptor(
          bias_desc_,
          GetCudnnTensorFormat(order_),
          cudnnTypeWrapper<T>::type,
          1,
          C,
          1,
          1));
    }
    // Set the output
    CUDNN_ENFORCE(cudnnSetTensor4dDescriptor(
        top_desc_,
        GetCudnnTensorFormat(order_),
        cudnnTypeWrapper<T>::type,
        N,
        C,
        H_out,
        W_out));
    // Set the convolution descriptor
    CAFFE_ENFORCE_EQ(
        pad_t_,
        pad_b_,
        "The current padding scheme leads to unequal padding on the top and "
        "bottom, which is not supported by cudnn.");
    CAFFE_ENFORCE_EQ(
        pad_l_,
        pad_r_,
        "The current padding scheme leads to unequal padding on the left "
        "and right, which is not supported by cudnn.");
#if CUDNN_VERSION_MIN(6,0,0)
    CUDNN_ENFORCE(cudnnSetConvolution2dDescriptor(
        conv_desc_,
        pad_t_,
        pad_l_,
        stride_h_,
        stride_w_,
        1,
        1,
        CUDNN_CROSS_CORRELATION,
        cudnnTypeWrapper<T>::type));
#else
    CUDNN_ENFORCE(cudnnSetConvolution2dDescriptor(
        conv_desc_,
        pad_t_,
        pad_l_,
        stride_h_,
        stride_w_,
        1,
        1,
        CUDNN_CROSS_CORRELATION));
#endif
    if (deterministic_) {
      bwd_data_algo_ = CUDNN_CONVOLUTION_BWD_DATA_ALGO_1;
    } else if (exhaustive_search_) {
      bwd_data_algo_ =
          data_algo_cache_.getAlgorithm(X.dims(), filter.dims(), [&]() {
            int returned_algo_count;
            std::array<
                cudnnConvolutionBwdDataAlgoPerf_t,
                kNUM_CUDNN_BWD_DATA_ALGS>
                data_perf_stat;
            cudnn_wrapper_.with_cudnn_state(
                cudnn_state_, [&](CuDNNState* state) {
                  state->workspace().reset();
                  CUDNN_ENFORCE(cudnnFindConvolutionBackwardDataAlgorithm(
                      state->cudnn_handle(),
                      filter_desc_,
                      bottom_desc_,
                      conv_desc_,
                      top_desc_,
                      kNUM_CUDNN_BWD_DATA_ALGS,
                      &returned_algo_count,
                      data_perf_stat.data()));
                });

            LogCuDNNPerfStats(data_perf_stat, returned_algo_count);
            return data_perf_stat[0].algo;
          });
    } else {
      CUDNN_ENFORCE(cudnnGetConvolutionBackwardDataAlgorithm(
          cudnn_wrapper_.inline_cudnn_handle(),
          filter_desc_,
          bottom_desc_,
          conv_desc_,
          top_desc_,
          CUDNN_CONVOLUTION_BWD_DATA_SPECIFY_WORKSPACE_LIMIT,
          cudnn_ws_nbytes_limit_,
          &bwd_data_algo_));
    }

    size_t bwd_data_ws_size;
    CUDNN_ENFORCE(cudnnGetConvolutionBackwardDataWorkspaceSize(
        cudnn_wrapper_.inline_cudnn_handle(),
        filter_desc_,
        bottom_desc_,
        conv_desc_,
        top_desc_,
        bwd_data_algo_,
        &bwd_data_ws_size));
    cudnn_ws_nbytes_ = bwd_data_ws_size;
    VLOG(1) << "CuDNN algorithm: " << bwd_data_algo_;
    VLOG(1) << "CuDNN workspace size: " << bwd_data_ws_size;
  }

  // Now, actually run the computation.
  // Filter
  cudnn_wrapper_.with_cudnn_state(cudnn_state_, [&](CuDNNState* state) {
    CUDNN_ENFORCE(cudnnConvolutionBackwardData(
        state->cudnn_handle(),
        cudnnTypeWrapper<T>::kOne(),
        filter_desc_,
        filter.template data<T>(),
        bottom_desc_,
        X.template data<T>(),
        conv_desc_,
        bwd_data_algo_,
        state->workspace().get(cudnn_ws_nbytes_),
        cudnn_ws_nbytes_,
        cudnnTypeWrapper<T>::kZero(),
        top_desc_,
        Y->template mutable_data<T>()));
  });
  // Bias
  CUDNN_ENFORCE(cudnnAddTensor(
      cudnn_wrapper_.inline_cudnn_handle(),
      cudnnTypeWrapper<T>::kOne(),
      bias_desc_,
      bias.template data<T>(),
      cudnnTypeWrapper<T>::kOne(),
      top_desc_,
      Y->template mutable_data<T>()));
  // Done.
  return true;
}

// TODO(Yangqing): a lot of the function contents are very similar. Consider
// consolidating them.
template <typename T>
bool CudnnConvTransposeGradientOp<T>::RunOnDevice() {
  auto& X = Input(INPUT);
  auto& filter = Input(FILTER);
  auto& dY = Input(OUTPUT_GRAD);
  auto* dfilter = Output(FILTER_GRAD);
  auto* dbias = Output(BIAS_GRAD);
  DCHECK_EQ(X.ndim(), 4);
  DCHECK_EQ(filter.ndim(), 4);
  auto* Y = Output(0);
  int C = 0;
  switch (order_) {
    case StorageOrder::NHWC:
      C = filter.dim32(3);
      break;
    case StorageOrder::NCHW:
      C = filter.dim32(1);
      break;
    default:
      LOG(FATAL) << "Unknown storage order: " << order_;
  }
  ConvTransposeUnpoolBase<CUDAContext>::SetOutputSize(X, Y, C);

  int N = 0, M = 0, H = 0, W = 0, H_out = 0, W_out = 0;
  switch (order_) {
    case StorageOrder::NHWC:
      N = X.dim32(0);
      H = X.dim32(1);
      W = X.dim32(2);
      M = X.dim32(3);
      H_out = dY.dim32(1);
      W_out = dY.dim32(2);
      DCHECK_EQ(filter.dim32(1), kernel_h_);
      DCHECK_EQ(filter.dim32(1), kernel_h_);
      DCHECK_EQ(filter.dim32(2), kernel_w_);
      DCHECK_EQ(filter.dim32(3), C);
      break;
    case StorageOrder::NCHW:
      N = X.dim32(0);
      M = X.dim32(1);
      H = X.dim32(2);
      W = X.dim32(3);
      H_out = dY.dim32(2);
      W_out = dY.dim32(3);
      DCHECK_EQ(filter.dim32(1), C);
      DCHECK_EQ(filter.dim32(2), kernel_h_);
      DCHECK_EQ(filter.dim32(3), kernel_w_);
      break;
    default:
      LOG(FATAL) << "Unknown storage order: " << order_;
  }
  // Since we only handle LegacyPadding::NOTSET, we don't need to
  // compute padding.
  dfilter->ResizeLike(filter);
  dbias->Resize(C);

  // Set up the cudnn algorithms & workspace if necessary
  bool input_changed = (X.dims() != cudnn_input_dims_);
  bool filter_changed = (filter.dims() != cudnn_filter_dims_);
  if (input_changed || filter_changed) {
    VLOG(1) << "Changing the cudnn descriptor configurations.";
    if (input_changed) {
      cudnn_input_dims_ = X.dims();
      CUDNN_ENFORCE(cudnnSetTensor4dDescriptor(
          bottom_desc_,
          GetCudnnTensorFormat(order_),
          cudnnTypeWrapper<T>::type,
          N,
          M,
          H,
          W));
    }
    if (filter_changed) {
      cudnn_filter_dims_ = filter.dims();
      CUDNN_ENFORCE(cudnnSetFilter4dDescriptor(
          filter_desc_,
          cudnnTypeWrapper<T>::type,
          GetCudnnTensorFormat(order_),
          M,
          C,
          kernel_h_,
          kernel_w_));
      CUDNN_ENFORCE(cudnnSetTensor4dDescriptor(
          bias_desc_,
          GetCudnnTensorFormat(order_),
          cudnnTypeWrapper<T>::type,
          1,
          C,
          1,
          1));
    }
    // Set the output
    CUDNN_ENFORCE(cudnnSetTensor4dDescriptor(
        top_desc_,
        GetCudnnTensorFormat(order_),
        cudnnTypeWrapper<T>::type,
        N,
        C,
        H_out,
        W_out));
    // Set the convolution descriptor
    CAFFE_ENFORCE_EQ(
        pad_t_,
        pad_b_,
        "The current padding scheme leads to unequal padding on the top and "
        "bottom, which is not supported by cudnn.");
    CAFFE_ENFORCE_EQ(
        pad_l_,
        pad_r_,
        "The current padding scheme leads to unequal padding on the left "
        "and right, which is not supported by cudnn.");
#if CUDNN_VERSION_MIN(6,0,0)
    CUDNN_ENFORCE(cudnnSetConvolution2dDescriptor(
        conv_desc_,
        pad_t_,
        pad_l_,
        stride_h_,
        stride_w_,
        1,
        1,
        CUDNN_CROSS_CORRELATION,
        cudnnTypeWrapper<T>::type));
#else
    CUDNN_ENFORCE(cudnnSetConvolution2dDescriptor(
        conv_desc_,
        pad_t_,
        pad_l_,
        stride_h_,
        stride_w_,
        1,
        1,
        CUDNN_CROSS_CORRELATION));
#endif
    // Set the workspace

    size_t bwd_filter_ws_size, fwd_ws_size;

    if (deterministic_) {
      algo_ = CUDNN_CONVOLUTION_FWD_ALGO_IMPLICIT_PRECOMP_GEMM;
      bwd_filter_algo_ = CUDNN_CONVOLUTION_BWD_FILTER_ALGO_1;
    } else if (exhaustive_search_) {
      bwd_filter_algo_ =
          filter_algo_cache_.getAlgorithm(X.dims(), filter.dims(), [&]() {

            LOG(INFO) << "CUDNN Convolution bwd: doing exhaustive search.";
            // When we do an exhaustive search, we will ignore the workspace
            // size
            // limit and simply go for the fastest algorithm. If you happen to
            // run
            // out of memory later, you will be on your own...
            int returned_algo_count;
            // We clean up the current workspace memory so that the forward
            // algorithm
            // is free to allocate memory.
            // Actually run the search.
            std::array<
                cudnnConvolutionBwdFilterAlgoPerf_t,
                kNUM_CUDNN_BWD_FILTER_ALGS>
                filter_perf_stat;

            cudnn_wrapper_.with_cudnn_state(
                cudnn_state_, [&](CuDNNState* state) {
                  state->workspace().reset();
                  CUDNN_ENFORCE(cudnnFindConvolutionBackwardFilterAlgorithm(
                      state->cudnn_handle(),
                      top_desc_,
                      bottom_desc_,
                      conv_desc_,
                      filter_desc_,
                      kNUM_CUDNN_BWD_FILTER_ALGS,
                      &returned_algo_count,
                      filter_perf_stat.data()));
                });
            LogCuDNNPerfStats(filter_perf_stat, returned_algo_count);
            return filter_perf_stat[0].algo;
          });

      algo_ = forward_algo_cache_.getAlgorithm(X.dims(), filter.dims(), [&]() {
        int returned_algo_count;
        std::array<cudnnConvolutionFwdAlgoPerf_t, kNUM_CUDNN_FWD_ALGS>
            fwd_perf_stat;
        cudnn_wrapper_.with_cudnn_state(cudnn_state_, [&](CuDNNState* state) {
          state->workspace().reset();
          CUDNN_ENFORCE(cudnnFindConvolutionForwardAlgorithm(
              state->cudnn_handle(),
              top_desc_,
              filter_desc_,
              conv_desc_,
              bottom_desc_,
              kNUM_CUDNN_BWD_DATA_ALGS,
              &returned_algo_count,
              fwd_perf_stat.data()));
        });

        LogCuDNNPerfStats(fwd_perf_stat, returned_algo_count);
        return fwd_perf_stat[0].algo;
      });
    } else {
      // choose backward algorithm for filter
      CUDNN_ENFORCE(cudnnGetConvolutionBackwardFilterAlgorithm(
          cudnn_wrapper_.inline_cudnn_handle(),
          top_desc_,
          bottom_desc_,
          conv_desc_,
          filter_desc_,
          CUDNN_CONVOLUTION_BWD_FILTER_SPECIFY_WORKSPACE_LIMIT,
          cudnn_ws_nbytes_limit_,
          &bwd_filter_algo_));
      // choose backward algo for data
      CUDNN_ENFORCE(cudnnGetConvolutionForwardAlgorithm(
          cudnn_wrapper_.inline_cudnn_handle(),
          top_desc_,
          filter_desc_,
          conv_desc_,
          bottom_desc_,
          CUDNN_CONVOLUTION_FWD_SPECIFY_WORKSPACE_LIMIT,
          cudnn_ws_nbytes_limit_,
          &algo_));
    }
    // get workspace for backwards filter algorithm
    CUDNN_ENFORCE(cudnnGetConvolutionBackwardFilterWorkspaceSize(
        cudnn_wrapper_.inline_cudnn_handle(),
        top_desc_,
        bottom_desc_,
        conv_desc_,
        filter_desc_,
        bwd_filter_algo_,
        &bwd_filter_ws_size));
    // get workspace for backwards data algorithm
    CUDNN_ENFORCE(cudnnGetConvolutionForwardWorkspaceSize(
        cudnn_wrapper_.inline_cudnn_handle(),
        top_desc_,
        filter_desc_,
        conv_desc_,
        bottom_desc_,
        algo_,
        &fwd_ws_size));
    cudnn_ws_nbytes_ = std::max(bwd_filter_ws_size, fwd_ws_size);

    VLOG(1) << "CuDNN bwd algorithm: " << bwd_filter_algo_ << ", " << algo_;
    VLOG(1) << "CuDNN workspace size: " << cudnn_ws_nbytes_;
  }

  // Now, actually run the computation.
  CUDNN_ENFORCE(cudnnConvolutionBackwardBias(
      cudnn_wrapper_.inline_cudnn_handle(),
      cudnnTypeWrapper<T>::kOne(),
      top_desc_,
      dY.template data<T>(),
      cudnnTypeWrapper<T>::kZero(),
      bias_desc_,
      dbias->template mutable_data<T>()));

  cudnn_wrapper_.with_cudnn_state(cudnn_state_, [&](CuDNNState* state) {
    CUDNN_ENFORCE(cudnnConvolutionBackwardFilter(
        state->cudnn_handle(),
        cudnnTypeWrapper<T>::kOne(),
        top_desc_,
        dY.template data<T>(),
        bottom_desc_,
        X.template data<T>(),
        conv_desc_,
        bwd_filter_algo_,
        state->workspace().get(cudnn_ws_nbytes_),
        cudnn_ws_nbytes_,
        cudnnTypeWrapper<T>::kZero(),
        filter_desc_,
        dfilter->template mutable_data<T>()));
    if (OutputSize() == 3) {
      // Compute the gradient w.r.t. the input.
      auto* dX = Output(INPUT_GRAD);
      dX->ResizeLike(X);
      CUDNN_ENFORCE(cudnnConvolutionForward(
          state->cudnn_handle(),
          cudnnTypeWrapper<T>::kOne(),
          top_desc_,
          dY.template data<T>(),
          filter_desc_,
          filter.template data<T>(),
          conv_desc_,
          algo_,
          state->workspace().get(cudnn_ws_nbytes_),
          cudnn_ws_nbytes_,
          cudnnTypeWrapper<T>::kZero(),
          bottom_desc_,
          dX->template mutable_data<T>()));
    }
  });
  return true;
}

REGISTER_CUDNN_OPERATOR(ConvTranspose, CudnnConvTransposeOp<float>);
REGISTER_CUDNN_OPERATOR(
    ConvTransposeGradient,
    CudnnConvTransposeGradientOp<float>);

} // namespace caffe2
