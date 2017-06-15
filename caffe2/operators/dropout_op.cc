#include "caffe2/operators/dropout_op.h"

namespace caffe2 {

template <>
bool DropoutOp<float, CPUContext>::RunOnDevice() {
  auto& X = Input(0);
  auto* Y = Output(0);
  auto* mask = Output(1);
  Y->Resize(X.dims());
  mask->Resize(X.dims());
  if (is_test_) {
    if (Y != &X) {
      context_.Copy<float, CPUContext, CPUContext>(
          X.size(), X.data<float>(), Y->mutable_data<float>());
    }
    return true;
  } else {
    float scale = 1. / (1. - ratio_);
    // mask=true means keep, and mask=false means not keep, so we will
    // generate probability depending on 1-ratio.
    std::bernoulli_distribution dist(1. - ratio_);
    const float* Xdata = X.data<float>();
    float* Ydata = Y->mutable_data<float>();
    bool* mask_data = mask->mutable_data<bool>();
    auto& gen = context_.RandGenerator();
    for (int i = 0; i < X.size(); ++i) {
      mask_data[i] = dist(gen);
      Ydata[i] = Xdata[i] * scale * mask_data[i];
    }
    return true;
  }
}

template <>
bool DropoutGradientOp<float, CPUContext>::RunOnDevice() {
  auto& dY = Input(0);
  auto& mask = Input(1);
  auto* dX = Output(0);
  DCHECK_EQ(dY.size(), mask.size());
  dX->Resize(dY.dims());
  if (is_test_) {
    if (dX != &dY) {
      context_.Copy<float, CPUContext, CPUContext>(
          dY.size(), dY.data<float>(), dX->mutable_data<float>());
    }
    return true;
  } else {
    const float* dYdata = dY.data<float>();
    const bool* mask_data = mask.data<bool>();
    float* dXdata = dX->mutable_data<float>();
    float scale = 1. / (1. - ratio_);
    for (int i = 0; i < dY.size(); ++i) {
      dXdata[i] = dYdata[i] * mask_data[i] * scale;
    }
    return true;
  }
}


namespace {
REGISTER_CPU_OPERATOR(Dropout, DropoutOp<float, CPUContext>);
REGISTER_CPU_OPERATOR(DropoutGrad, DropoutGradientOp<float, CPUContext>);

OPERATOR_SCHEMA(Dropout)
    .NumInputs(1)
    .NumOutputs(2)
    .AllowInplace({{0, 0}})
    .SetDoc(R"DOC(
Dropout takes one input data (Tensor<float>) and produces two Tensor outputs,
output (Tensor<float>) and mask (Tensor<bool>). Depending on whether it is in
test mode or not, the output Y will either be a random dropout, or a simple
copy of the input. Note that our implementation of Dropout does scaling in
the training phase, so during testing nothing needs to be done.
)DOC")
    .Arg("ratio", "(float, default 0.5) the ratio of random dropout")
    .Arg("is_test", "(int, default 0) if nonzero, run dropout in test mode where "
                    "the output is simply Y = X.")
    .Input(0, "data", "The input data as Tensor.")
    .Output(0, "output", "The output.")
    .Output(1, "mask",
            "The output mask. If is_test is nonzero, this output is not filled.");

OPERATOR_SCHEMA(DropoutGrad)
    .NumInputs(2).NumOutputs(1).AllowInplace({{0, 0}});

class GetDropoutGradient : public GradientMakerBase {
  using GradientMakerBase::GradientMakerBase;
  vector<OperatorDef> GetGradientDefs() override {
    return SingleGradientDef(
        "DropoutGrad", "",
        vector<string>{GO(0), O(1)},
        vector<string>{GI(0)});
  }
};
REGISTER_GRADIENT(Dropout, GetDropoutGradient);
}  // namespace
}  // namespace caffe2
