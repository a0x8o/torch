#include "caffe2/perfkernels/embedding_lookup.h"

#include "caffe2/core/types.h"
#include "caffe2/perfkernels/common.h"
#include "caffe2/perfkernels/typed_axpy.h"
#include "caffe2/utils/cpuid.h"
#include "caffe2/utils/math.h"

namespace caffe2 {

// Base implementation does runtime dispatch for each segment of reduction
template <typename IndexType, typename InType, typename OutType>
static void EmbeddingLookupGenericSlow(
    const TIndex block_size,
    const TIndex output_size,
    const TIndex index_size,
    const TIndex data_size,
    const InType* input,
    const IndexType* indices,
    const int* lengths,
    const float* weights, // optional, can be null for sum reducer
    bool normalize_by_lengths,
    OutType* out) {
  TIndex current = 0;
  for (int m = 0; m < output_size; ++m) {
    memset(out, 0, sizeof(OutType) * block_size);
    for (int i = 0; i < lengths[m]; ++i) {
      CAFFE_ENFORCE_LT(current, index_size);
      TIndex idx = indices[current];
      CAFFE_ENFORCE(
          0 <= idx && idx < data_size,
          "Index ",
          current,
          " is out of bounds: ",
          idx,
          ", range 0 to ",
          data_size);
      CAFFE_ENFORCE_LT(idx, data_size);
#ifdef __GNUC__
      if (current + 1 < index_size) {
        __builtin_prefetch(input + block_size * indices[current + 1], 0, 1);
      }
#endif // __GNUC__
      TypedAxpy<InType, OutType>(
          block_size,
          weights ? weights[current] : 1.0,
          input + block_size * indices[current],
          out);
      ++current;
    }
    if (normalize_by_lengths && lengths[m]) {
      // hack: context is not really used
      math::Scale<OutType, CPUContext>(
          block_size, 1.f / lengths[m], out, out, nullptr);
    }
    out += block_size;
  }
  CAFFE_ENFORCE_EQ(
      current,
      index_size,
      "Your input seems to be incorrect: the sum of lengths values should be "
      "the size of the indices tensor, but it appears not.");
}

// Proxy back to generic implementation
#define EMBEDDING_SPECIALIZATION(IndexType, InType, OutType)       \
  void EmbeddingLookup_##IndexType##_##InType##_##OutType##__base( \
      const TIndex block_size,                                     \
      const TIndex output_size,                                    \
      const TIndex index_size,                                     \
      const TIndex data_size,                                      \
      const InType* input,                                         \
      const IndexType* indices,                                    \
      const int* lengths,                                          \
      const float* weights,                                        \
      bool normalize_by_lengths,                                   \
      OutType* out) {                                              \
    EmbeddingLookupGenericSlow<IndexType, InType, OutType>(        \
        block_size,                                                \
        output_size,                                               \
        index_size,                                                \
        data_size,                                                 \
        input,                                                     \
        indices,                                                   \
        lengths,                                                   \
        weights,                                                   \
        normalize_by_lengths,                                      \
        out);                                                      \
  }                                                                \
  template <>                                                      \
  void EmbeddingLookup(                                            \
      const TIndex block_size,                                     \
      const TIndex output_size,                                    \
      const TIndex index_size,                                     \
      const TIndex data_size,                                      \
      const InType* input,                                         \
      const IndexType* indices,                                    \
      const int* lengths,                                          \
      const float* weights,                                        \
      bool normalize_by_lengths,                                   \
      OutType* out) {                                              \
    AVX2_FMA_DO(                                                   \
        EmbeddingLookup_##IndexType##_##InType##_##OutType,        \
        block_size,                                                \
        output_size,                                               \
        index_size,                                                \
        data_size,                                                 \
        input,                                                     \
        indices,                                                   \
        lengths,                                                   \
        weights,                                                   \
        normalize_by_lengths,                                      \
        out);                                                      \
    BASE_DO(                                                       \
        EmbeddingLookup_##IndexType##_##InType##_##OutType,        \
        block_size,                                                \
        output_size,                                               \
        index_size,                                                \
        data_size,                                                 \
        input,                                                     \
        indices,                                                   \
        lengths,                                                   \
        weights,                                                   \
        normalize_by_lengths,                                      \
        out);                                                      \
  }

EMBEDDING_SPECIALIZATION(int32_t, float, float);
EMBEDDING_SPECIALIZATION(int64_t, float, float);
EMBEDDING_SPECIALIZATION(int32_t, float16, float);
EMBEDDING_SPECIALIZATION(int64_t, float16, float);

#undef EMBEDDING_SPECIALIZATION

} // namespace caffe2
