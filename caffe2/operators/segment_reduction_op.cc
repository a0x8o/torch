#include "caffe2/operators/segment_reduction_op.h"

namespace caffe2 {
namespace {

template <typename Def>
string FormatDoc() {
  string doc = Def::doc;
  ReplaceAll(doc, "{op}", Def::OpDef::name);
  ReplaceAll(doc, "{op_doc}", Def::OpDef::doc);
  return doc;
}

#define REGISTER_SEGMENT_DEF(...)                                              \
  REGISTER_CPU_OPERATOR_STR(                                                   \
      string(__VA_ARGS__::basename) + (__VA_ARGS__::OpDef::name),              \
      __VA_ARGS__::ForwardOp);                                                 \
  OPERATOR_SCHEMA_STR(                                                         \
      string(__VA_ARGS__::basename) + (__VA_ARGS__::OpDef::name))              \
      .NumInputs(__VA_ARGS__::ForwardOp::kNumInputs)                           \
      .NumOutputs(1)                                                           \
      .SetDoc(FormatDoc<__VA_ARGS__>())                                        \
      .Output(0, "OUTPUT", "Aggregated tensor")                                \
      .FillUsing(__VA_ARGS__::PopulateSchema);                                 \
  REGISTER_CPU_OPERATOR_STR(                                                   \
      string(__VA_ARGS__::basename) + (__VA_ARGS__::OpDef::name) + "Gradient", \
      __VA_ARGS__::BackwardOp);                                                \
  OPERATOR_SCHEMA_STR(                                                         \
      string(__VA_ARGS__::basename) + (__VA_ARGS__::OpDef::name) + "Gradient") \
      .NumInputs(__VA_ARGS__::BackwardOp::kNumInputs)                          \
      .NumOutputs(1);                                                          \
  REGISTER_GRADIENT_STR(                                                       \
      string(__VA_ARGS__::basename) + (__VA_ARGS__::OpDef::name),              \
      __VA_ARGS__::GetGradient)

REGISTER_SEGMENT_DEF(
    AbstractSortedSegmentRangeDef<float, int, CPUContext, SumRangeReducerDef>);
REGISTER_SEGMENT_DEF(AbstractSortedSegmentRangeDef<
                     float,
                     int,
                     CPUContext,
                     LogSumExpRangeReducerDef>);
REGISTER_SEGMENT_DEF(AbstractSortedSegmentRangeDef<
                     float,
                     int,
                     CPUContext,
                     LogMeanExpRangeReducerDef>);
REGISTER_SEGMENT_DEF(
    AbstractSortedSegmentRangeDef<float, int, CPUContext, MeanRangeReducerDef>);
REGISTER_SEGMENT_DEF(
    AbstractSortedSegmentRangeDef<float, int, CPUContext, MaxRangeReducerDef>);

#define REGISTER_REDUCER_WITH_ALL_OPS(reducer_def)                           \
  REGISTER_SEGMENT_DEF(                                                      \
      AbstractReduceFrontDef<float, CPUContext, reducer_def>);               \
  REGISTER_SEGMENT_DEF(                                                      \
      AbstractSortedSegmentDef<float, int, CPUContext, reducer_def>);        \
  REGISTER_SEGMENT_DEF(                                                      \
      AbstractSparseSortedSegmentDef<float, int, CPUContext, reducer_def>);  \
  REGISTER_SEGMENT_DEF(                                                      \
      AbstractUnsortedSegmentDef<float, int, CPUContext, reducer_def>);      \
  REGISTER_SEGMENT_DEF(                                                      \
      AbstractSparseUnsortedSegmentDef<float, int, CPUContext, reducer_def>) \
  REGISTER_SEGMENT_DEF(                                                      \
      AbstractLengthsDef<float, int, CPUContext, reducer_def>)               \
  REGISTER_SEGMENT_DEF(                                                      \
      AbstractSparseLengthsDef<float, int, CPUContext, reducer_def>)

REGISTER_REDUCER_WITH_ALL_OPS(SumReducerDef);
REGISTER_REDUCER_WITH_ALL_OPS(WeightedSumReducerDef);
REGISTER_REDUCER_WITH_ALL_OPS(MeanReducerDef);

REGISTER_SEGMENT_DEF(AbstractReduceBackDef<float, CPUContext, SumReducerDef>);
REGISTER_SEGMENT_DEF(AbstractReduceBackDef<float, CPUContext, MeanReducerDef>);

// Auxiliary output gradients are currently implemented only for Lengths version
#define REGISTER_GRADIENT_WITH_MAIN_INPUT(...)                     \
  REGISTER_CPU_OPERATOR_STR(                                       \
      string(__VA_ARGS__::basename) + (__VA_ARGS__::OpDef::name) + \
          "WithMainInputGradient",                                 \
      __VA_ARGS__::WithMainInputBackwardOp);                       \
  OPERATOR_SCHEMA_STR(                                             \
      string(__VA_ARGS__::basename) + (__VA_ARGS__::OpDef::name) + \
      "WithMainInputGradient")                                     \
      .NumInputs(__VA_ARGS__::WithMainInputBackwardOp::kNumInputs) \
      .NumOutputs(1, INT_MAX)
REGISTER_GRADIENT_WITH_MAIN_INPUT(
    AbstractLengthsDef<float, int, CPUContext, WeightedSumReducerDef>);
REGISTER_GRADIENT_WITH_MAIN_INPUT(
    AbstractSparseLengthsDef<float, int, CPUContext, WeightedSumReducerDef>);
}
}
