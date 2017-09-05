#ifndef CAFFE2_CORE_ALLOCATOR_H_
#define CAFFE2_CORE_ALLOCATOR_H_

#include <unordered_map>

#include "caffe2/core/logging.h"

CAFFE2_DECLARE_bool(caffe2_report_cpu_memory_usage);

namespace caffe2 {

// Use 32-byte alignment should be enough for computation up to AVX512.
constexpr size_t gCaffe2Alignment = 32;

using MemoryDeleter = std::function<void(void* ptr)>;

// A virtual allocator class to do memory allocation and deallocation.
struct CPUAllocator {
  CPUAllocator() {}
  virtual ~CPUAllocator() noexcept {}
  virtual std::pair<void*, MemoryDeleter> New(size_t nbytes) = 0;
};

// A virtual struct that is used to report Caffe2's memory allocation and
// deallocation status
class MemoryAllocationReporter {
 public:
  MemoryAllocationReporter() : allocated_(0) {}
  void New(void* ptr, size_t nbytes);
  void Delete(void* ptr);

 private:
  std::mutex mutex_;
  std::unordered_map<void*, size_t> size_table_;
  size_t allocated_;
};

struct DefaultCPUAllocator final : CPUAllocator {
  DefaultCPUAllocator() {}
  ~DefaultCPUAllocator() override {}
  std::pair<void*, MemoryDeleter> New(size_t nbytes) override {
    void* data = nullptr;
#ifdef __ANDROID__
    data = memalign(gCaffe2Alignment, nbytes);
#elif defined(_MSC_VER)
    data = _aligned_malloc(nbytes, gCaffe2Alignment);
#else
    CAFFE_ENFORCE_EQ(posix_memalign(&data, gCaffe2Alignment, nbytes), 0);
#endif
    CAFFE_ENFORCE(data);
    memset(data, 0, nbytes);
    return {data, Delete};
  }
#ifdef _MSC_VER
  static void Delete(void* data) {
    _aligned_free(data);
  }
#else
  static void Delete(void* data) {
    free(data);
  }
#endif
};

// Get the CPU Alloctor.
CPUAllocator* GetCPUAllocator();
// Sets the CPU allocator to the given allocator: the caller gives away the
// ownership of the pointer.
void SetCPUAllocator(CPUAllocator* alloc);

} // namespace caffe2

#endif // CAFFE2_CORE_ALLOCATOR_H_
