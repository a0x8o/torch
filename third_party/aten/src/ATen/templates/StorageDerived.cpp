#include "ATen/${Storage}.h"
#include "ATen/Half.h"

namespace at {

${Storage}::${Storage}(Context* context):
    storage(${THStorage}_new(${state})), context(context) {}

${Storage}::${Storage}(Context* context, ${THStorage}* storage):
    storage(storage), context(context) {}

${Storage}::${Storage}(Context* context, std::size_t storage_size)
  : storage(${THStorage}_newWithSize(${state,} storage_size)), context(context) {}

#if ${isCUDA}
static cudaError_t call_deleter(void * ctx, void * data) {
  auto fnptr = (std::function<void(void*)>*) ctx;
  (*fnptr)(data);
  delete fnptr;
  return cudaSuccess;
}
static THCDeviceAllocator storage_deleter = {
  nullptr,
  nullptr,
  call_deleter,
  nullptr,
  nullptr,
};
#else
static void call_deleter(void * ctx, void * data) {
  auto fnptr = (std::function<void(void*)>*) ctx;
  (*fnptr)(data);
  delete fnptr;
}
static THAllocator storage_deleter = {
  nullptr,
  nullptr,
  call_deleter,
};
#endif

${Storage}::${Storage}(Context* context,
  void * data, std::size_t size, const std::function<void(void*)> & deleter)
  : storage(${THStorage}_newWithDataAndAllocator(${state,}
     static_cast<${THScalarType}*>(data), size,
     &storage_deleter,
     new std::function<void(void*)>(deleter)
    )),
    context(context) {
    ${THStorage}_clearFlag(${state,} storage, TH_STORAGE_RESIZABLE);
}

${Storage}::~${Storage}() {
  ${THStorage}_free(${state,} storage);
}

std::size_t ${Storage}::elementSize() const {
  return sizeof(${ScalarType});
}

std::size_t ${Storage}::size() const {
  return storage->size;
}

void* ${Storage}::data() {
  return storage->data;
}

const void* ${Storage}::data() const {
  return storage->data;
}

auto ${Storage}::retain() -> ${Storage}& {
  ${THStorage}_retain(${state,} storage);
  return *this;
}

auto ${Storage}::free() -> ${Storage}& {
  ${THStorage}_free(${state,} storage);
  return *this;
}

auto ${Storage}::resize(int64_t new_size) -> ${Storage}& {
  ${THStorage}_resize(${state,} storage, new_size);
  return *this;
}

auto ${Storage}::fill(Scalar value) -> ${Storage}& {
  ${THStorage}_fill(${state,} storage, ${to_th_type}(value.to${ScalarName}()));
  return *this;
}

auto ${Storage}::set(std::size_t ind, Scalar value) -> ${Storage}& {
  ${THStorage}_set(${state,} storage, ind, ${to_th_type}(value.to${ScalarName}()));
  return *this;
}

auto ${Storage}::fast_set(std::size_t ind, Scalar value) -> ${Storage}& {
  throw std::runtime_error("unsupported operation 'fast_set'");
}

auto ${Storage}::get(std::size_t ind) -> Scalar {
  // static cast to fix  long -> int64_t issues
  return static_cast<${ScalarType}>(${to_at_type}(${THStorage}_get(${state,} storage, ind)));
}

auto ${Storage}::fast_get(std::size_t ind) -> Scalar {
  if(${isCUDA})
    throw std::runtime_error("unsupported operation 'fast_get'");
  return static_cast<${ScalarType}>(${to_at_type}(storage->data[ind]));
}

int ${Storage}::getDevice() const {
  ${storage_device} //storage->device;
}

Type& ${Storage}::type() const {
  return context->getType(Backend::${Backend},ScalarType::${ScalarName});
}

const char * ${Storage}::toString() const {
  return "${Storage}";
}

const char * ${Storage}::typeString() {
  return "${Type}";
}

}
