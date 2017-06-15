#include <chrono>
#include <future>
#include <random>
#include <thread>
#include <array>

#include "caffe2/proto/caffe2.pb.h"
#include "caffe2/core/context_gpu.h"
#include <gtest/gtest.h>

namespace caffe2 {

TEST(CUDAContextTest, TestAllocDealloc) {
  if (!HasCudaGPU()) return;
  CUDAContext context(0);
  context.SwitchToDevice();
  float* data = static_cast<float*>(CUDAContext::New(10 * sizeof(float)));
  EXPECT_NE(data, nullptr);
  CUDAContext::Delete(data);
}

TEST(CUDAContextTest, MemoryPoolAllocateDealloc) {
  if (!HasCudaGPU())
    return;
  if (GetCudaMemoryPoolType() == CudaMemoryPoolType::NONE) {
    LOG(ERROR) << "Choose a memory type that is not none to test memory pool.";
    return;
  }
  const int nbytes = 1048576;
  for (int i = 0; i < NumCudaDevices(); ++i) {
    LOG(INFO) << "Device " << i << " of " << NumCudaDevices();
    DeviceGuard guard(i);
    void* allocated = CUDAContext::New(nbytes);
    EXPECT_NE(allocated, nullptr);
    cudaPointerAttributes attr;
    CUDA_ENFORCE(cudaPointerGetAttributes(&attr, allocated));
    EXPECT_EQ(attr.memoryType, cudaMemoryTypeDevice);
    EXPECT_EQ(attr.device, i);
    CUDAContext::Delete(allocated);
    void* new_allocated = CUDAContext::New(nbytes);
    // With a pool, the above allocation should yield the same address.
    EXPECT_EQ(new_allocated, allocated);
    // But, if we are allocating something larger, we will have a different
    // chunk of memory.
    void* larger_allocated = CUDAContext::New(nbytes * 2);
    EXPECT_NE(larger_allocated, new_allocated);
    CUDAContext::Delete(new_allocated);
    CUDAContext::Delete(larger_allocated);
  }
}

cudaStream_t getStreamForHandle(cublasHandle_t handle) {
  cudaStream_t stream = nullptr;
  CUBLAS_ENFORCE(cublasGetStream(handle, &stream));
  CHECK_NOTNULL(stream);
  return stream;
}

TEST(CUDAContextTest, TestSameThreadSameObject) {
  if (!HasCudaGPU()) return;
  CUDAContext context_a(0);
  CUDAContext context_b(0);
  EXPECT_EQ(context_a.cuda_stream(), context_b.cuda_stream());
  EXPECT_EQ(context_a.cublas_handle(), context_b.cublas_handle());
  EXPECT_EQ(
      context_a.cuda_stream(), getStreamForHandle(context_b.cublas_handle()));
  // CuRAND generators are context-local.
  EXPECT_NE(context_a.curand_generator(), context_b.curand_generator());
}

TEST(CUDAContextTest, TestSameThreadDifferntObjectIfDifferentDevices) {
  if (NumCudaDevices() > 1) {
    CUDAContext context_a(0);
    CUDAContext context_b(1);
    EXPECT_NE(context_a.cuda_stream(), context_b.cuda_stream());
    EXPECT_NE(context_a.cublas_handle(), context_b.cublas_handle());
    EXPECT_NE(
        context_a.cuda_stream(), getStreamForHandle(context_b.cublas_handle()));
    EXPECT_NE(context_a.curand_generator(), context_b.curand_generator());
  }
}

namespace {
// A test function to return a stream address from a temp CUDA context. You
// should not use that stream though, because the actual stream is destroyed
// after thread exit.
void TEST_GetStreamAddress(cudaStream_t* ptr) {
  CUDAContext context(0);
  *ptr = context.cuda_stream();
  // Sleep for a while so we have concurrent thread executions
  std::this_thread::sleep_for(std::chrono::seconds(1));
}
}  // namespace

TEST(CUDAContextTest, TestDifferntThreadDifferentobject) {
  if (!HasCudaGPU()) return;
  std::array<cudaStream_t, 2> temp = {0};
  // Same thread
  TEST_GetStreamAddress(&temp[0]);
  TEST_GetStreamAddress(&temp[1]);
  EXPECT_TRUE(temp[0] != nullptr);
  EXPECT_TRUE(temp[1] != nullptr);
  EXPECT_EQ(temp[0], temp[1]);
  // Different threads
  std::thread thread_a(TEST_GetStreamAddress, &temp[0]);
  std::thread thread_b(TEST_GetStreamAddress, &temp[1]);
  thread_a.join();
  thread_b.join();
  EXPECT_TRUE(temp[0] != nullptr);
  EXPECT_TRUE(temp[1] != nullptr);
  EXPECT_NE(temp[0], temp[1]);
}

}  // namespace caffe2
