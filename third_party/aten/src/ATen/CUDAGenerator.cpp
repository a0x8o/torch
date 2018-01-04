#ifdef AT_CUDA_ENABLED

#include "ATen/CUDAGenerator.h"
#include "ATen/Context.h"
#include <stdexcept>

#define const_generator_cast(generator) \
  dynamic_cast<const CUDAGenerator&>(generator)

namespace at {

CUDAGenerator::CUDAGenerator(Context * context_)
  : context(context_)
{
  int num_devices, current_device;
  cudaGetDeviceCount(&num_devices);
  cudaGetDevice(&current_device);
  THCRandom_init(context->thc_state, num_devices, current_device);
}

CUDAGenerator::~CUDAGenerator() {
  // no-op Generator state is global to the program
}

CUDAGenerator& CUDAGenerator::copy(const Generator& from) {
  throw std::runtime_error("CUDAGenerator::copy() not implemented");
}

CUDAGenerator& CUDAGenerator::free() {
  THCRandom_shutdown(context->thc_state);
  return *this;
}

uint64_t CUDAGenerator::seed() {
  return THCRandom_initialSeed(context->thc_state);
}

CUDAGenerator& CUDAGenerator::manualSeed(uint64_t seed) {
  THCRandom_manualSeed(context->thc_state, seed);
  return *this;
}

void * CUDAGenerator::unsafeGetTH() {
  throw std::runtime_error("CUDAGenerator::unsafeGetTH() not implemented");
}

} // namespace at
#endif //AT_CUDA_ENABLED
