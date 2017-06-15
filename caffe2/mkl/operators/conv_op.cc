#include "caffe2/core/context.h"
#include "caffe2/core/operator.h"
#include "caffe2/operators/conv_pool_op_base.h"
#include "caffe2/utils/mkl_utils.h"

#ifdef CAFFE2_HAS_MKL_DNN

namespace caffe2 {
namespace mkl {

template <typename T>
class MKLConvOp final : public ConvPoolOpBase<MKLContext> {
 public:
  USE_CONV_POOL_BASE_FUNCTIONS(MKLContext);
  MKLConvOp(const OperatorDef& operator_def, Workspace* ws)
      : ConvPoolOpBase<MKLContext>(operator_def, ws) {
    OPERATOR_NEEDS_FEATURE(
        dilation_h() == 1 && dilation_w() == 1, "Dilation not supported.");
    OPERATOR_NEEDS_FEATURE(
        pad_l() == pad_r() && pad_t() == pad_b(),
        "Uneven padding not supported.");
    OPERATOR_NEEDS_FEATURE(
        order_ == StorageOrder::NCHW, "Only NCHW order supported.");
    OPERATOR_NEEDS_FEATURE(
        group_ == 1, "Group convolution not supported yet.");
  }
  ~MKLConvOp() {}

  // TODO(jiayq): support double if needed.
  bool RunOnDeviceWithOrderNCHW() override {
    auto& X = OperatorBase::Input<MKLMemory<float>>(INPUT);
    auto& filter = OperatorBase::Input<MKLMemory<float>>(FILTER);
    auto& bias = OperatorBase::Input<MKLMemory<float>>(BIAS);
    MKLMemory<float>* Y = OperatorBase::Output<MKLMemory<float>>(0);
    CAFFE_ENFORCE(4 == X.ndim());
    const int N = X.dim32(0), C = X.dim32(1), H = X.dim32(2), W = X.dim32(3);
    CAFFE_ENFORCE(4 == filter.ndim());
    const int M = filter.dim32(0);

    bool dims_changed;
    CHECK_INPUT_FILTER_DIMS(dims_changed);
    if (dims_changed) {
      CAFFE_ENFORCE(
          C == filter.dim32(1),
          "Convolution op: # of input channels ",
          C,
          " is not equal to kernel channels:",
          filter.dim32(1));
      CAFFE_ENFORCE(filter.dim32(2) == kernel_h());
      CAFFE_ENFORCE(filter.dim32(3) == kernel_w());
      CAFFE_ENFORCE(bias.ndim() == 1);
      CAFFE_ENFORCE(bias.dim32(0) == M);

      size_t dimension = 4;
      size_t bdata_sizes[4] = {W, H, C, N};
      // We will utilize the SetOutputSize() function int he base class
      // with dummy TensorCPU input and output to calculate the sizes.
      TensorCPU dummy_input(X.dims());
      TensorCPU dummy_output;
      ConvPoolOpBase<MKLContext>::SetOutputSize(
          dummy_input, &dummy_output, M);
      size_t tdata_sizes[4] = {
          dummy_output.dim(3), dummy_output.dim(2),
          dummy_output.dim(1), dummy_output.dim(0)};
      size_t fdata_sizes[4] = {kernel_w(), kernel_h(), C, M};
      size_t strides[2] = {stride_w(), stride_h()};
      int pads[2] = {-pad_l(), -pad_t()};

      primitive_.Reset(
          dnnConvolutionCreateForwardBias<float>,
          nullptr,
          dnnAlgorithmConvolutionDirect,
          dimension,
          bdata_sizes,
          tdata_sizes,
          fdata_sizes,
          strides,
          pads,
          dnnBorderZeros);
      Y->Reset(dummy_output.dims(), primitive_, dnnResourceDst);
      buffer_.Reset(dummy_output.dims(), primitive_, dnnResourceDst, true);

      input_layout_.Reset(primitive_, dnnResourceSrc);
      filter_layout_.Reset(primitive_, dnnResourceFilter);
      bias_layout_.Reset(primitive_, dnnResourceBias);
    }

    // Try to share from the output: this allows us to avoid unnecessary copy
    // operations, if the output is already allocated and is having the same
    // layout as the buffer has.
    buffer_.ShareFrom(*Y);
    std::shared_ptr<void> X_view = X.View(
        input_layout_, primitive_, dnnResourceSrc);
    std::shared_ptr<void> filter_view = filter.View(
        filter_layout_, primitive_, dnnResourceFilter);
    std::shared_ptr<void> bias_view = bias.View(
        bias_layout_, primitive_, dnnResourceBias);
    resources_[dnnResourceSrc] = X_view.get();
    resources_[dnnResourceFilter] = filter_view.get();
    resources_[dnnResourceBias] = bias_view.get();
    resources_[dnnResourceDst] = buffer_.buffer();

    MKLDNN_SAFE_CALL(mkl::dnnExecute<T>(primitive_, resources_));
    buffer_.CopyTo(Y, primitive_, dnnResourceDst);
    return true;
  }

  bool RunOnDeviceWithOrderNHWC() override {
    CAFFE_NOT_IMPLEMENTED;
  }

 private:
  // Input: X, W, b
  // Output: Y
  vector<TIndex> cached_input_dims_;
  vector<TIndex> cached_filter_dims_;
  PrimitiveWrapper<T> primitive_;
  LayoutWrapper<T> input_layout_;
  LayoutWrapper<T> filter_layout_;
  LayoutWrapper<T> bias_layout_;
  MKLMemory<T> buffer_;
  void* resources_[dnnResourceNumber] = {0};
  INPUT_TAGS(INPUT, FILTER, BIAS);
};

} // namespace mkl


REGISTER_MKL_OPERATOR(Conv, mkl::MKLConvOp<float>);

}  // namespace caffe2

#endif // CAFFE2_HAS_MKL_DNN
