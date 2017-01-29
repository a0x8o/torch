#ifndef TH_GENERIC_FILE
#error "You must define TH_GENERIC_FILE before including THGenerateHalfType.h"
#endif

#include "THHalf.h"
#define real THHalf
#define accreal float
#define TH_CONVERT_REAL_TO_ACCREAL(_val) TH_half2float(_val)
#define TH_CONVERT_ACCREAL_TO_REAL(_val) TH_float2half(_val)
#define Real Half
#define THInf TH_HALF_MAX
#define TH_REAL_IS_HALF
#line 1 TH_GENERIC_FILE
#include TH_GENERIC_FILE
#undef real
#undef accreal
#undef Real
#undef THInf
#undef TH_REAL_IS_HALF
#undef TH_CONVERT_REAL_TO_ACCREAL
#undef TH_CONVERT_ACCREAL_TO_REAL

#ifndef THGenerateAllTypes
#undef TH_GENERIC_FILE
#endif
