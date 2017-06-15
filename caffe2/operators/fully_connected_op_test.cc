#include <iostream>

#include "caffe2/operators/fully_connected_op.h"
#include "caffe2/core/flags.h"
#include <gtest/gtest.h>

CAFFE2_DECLARE_string(caffe_test_root);

namespace caffe2 {

static void AddConstInput(const vector<TIndex>& shape, const float value,
                          const string& name, Workspace* ws) {
  DeviceOption option;
  CPUContext context(option);
  Blob* blob = ws->CreateBlob(name);
  auto* tensor = blob->GetMutable<TensorCPU>();
  tensor->Resize(shape);
  math::Set<float, CPUContext>(tensor->size(), value,
                               tensor->mutable_data<float>(),
                               &context);
  return;
}

TEST(FullyConnectedTest, Test) {
  Workspace ws;
  OperatorDef def;
  def.set_name("test");
  def.set_type("FC");
  def.add_input("X");
  def.add_input("W");
  def.add_input("B");
  def.add_output("Y");
  AddConstInput(vector<TIndex>{5, 10}, 1., "X", &ws);
  AddConstInput(vector<TIndex>{6, 10}, 1., "W", &ws);
  AddConstInput(vector<TIndex>{6}, 0.1, "B", &ws);
  unique_ptr<OperatorBase> op(CreateOperator(def, &ws));
  EXPECT_NE(nullptr, op.get());
  EXPECT_TRUE(op->Run());
  Blob* Yblob = ws.GetBlob("Y");
  EXPECT_NE(nullptr, Yblob);
  auto& Y = Yblob->Get<TensorCPU>();
  EXPECT_EQ(Y.size(), 5 * 6);
  for (int i = 0; i < Y.size(); ++i) {
    CHECK_LT(Y.data<float>()[i], 10.11);
    CHECK_GT(Y.data<float>()[i], 10.09);
  }
}

}  // namespace caffe2
