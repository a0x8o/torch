#pragma once

#include "ATen/Generator.h"
#include "ATen/Scalar.h"
#include "ATen/ScalarType.h"
#include "ATen/TensorAccessor.h"
#include "ATen/TensorImpl.h"
#include "ATen/TensorBase.h"
#include "ATen/Storage.h"
#include "ATen/SparseTensorRef.h"
#include "ATen/Utils.h"

namespace at {
struct Type;

// Tensor is a "generic" object holding a pointer to the underlying TensorImpl object, which
// has an embedded reference count. In this way, Tensor is similar to boost::intrusive_ptr.
//
// For example:
//
// void func(Tensor a) {
//   Tensor b = a;
//   ...
// }
//
// In this example, when we say Tensor b = a, we are creating a new object that points to the
// same underlying TensorImpl, and bumps its reference count. When b goes out of scope, the
// destructor decrements the reference count by calling release() on the TensorImpl it points to.
// The existing constructors, operator overloads, etc. take care to implement the correct semantics.
//
// Note that Tensor can also be NULL, i.e. it is not associated with any underlying TensorImpl, and
// special care must be taken to handle this.
struct Tensor : public detail::TensorBase {
  Tensor() : TensorBase() {}
  Tensor(TensorImpl * self, bool retain) : TensorBase(self, retain) {}
  Tensor(const TensorBase & rhs) : TensorBase(rhs) {}
  Tensor(const Tensor & rhs) = default;
  Tensor(Tensor && rhs) noexcept = default;

  Tensor & operator=(Tensor && rhs) & {
    rhs.swap(*this);
    return *this;
  }
  Tensor & operator=(Tensor const & rhs) & {
      //Tensor ctor retains original rhs.pImpl
      //then rhs.pImpl is swapped with this->pImpl
      //finally Tensor dtor releases rhs.pImpl, which was originally this->pImpl
      Tensor(rhs).swap(*this);
      return *this;
  }
  Tensor & operator=(Tensor const & rhs) && {
    return assign_(rhs);
  }
  Tensor & operator=(Scalar v) &&;
  Tensor & assign_(Scalar v);
  void reset() {
    Tensor().swap(*this);
  }
  void reset(TensorImpl * rhs) {
    Tensor(rhs, true).swap(*this);
  }
  void reset(TensorImpl * rhs, bool retain) {
    Tensor(rhs, retain).swap(*this );
  }
  TensorImpl * get() const {
    return pImpl;
  }
  TensorImpl * detach() {
    TensorImpl * ret = pImpl;
    pImpl = nullptr;
    return ret;
  }
  bool defined() const {
    return pImpl != nullptr;
  }
  void swap(Tensor & rhs) {
    TensorImpl * tmp = pImpl;
    pImpl = rhs.pImpl;
    rhs.pImpl = tmp;
  }
  const char * toString() const {
    return pImpl->toString();
  }
  IntList sizes() const {
    return pImpl->sizes();
  }
  IntList strides() const {
    return pImpl->strides();
  }
  int64_t ndimension() const {
    return dim();
  }
  Type & type() const {
    return pImpl->type();
  }
  inline Tensor toType(const Type & t) const;
  inline Tensor & copy_(const Tensor & src);
  inline Tensor toType(ScalarType t) const;
  inline Tensor toBackend(Backend b) const;

  template<typename T>
  T * data() const;

  void * unsafeGetTH(bool retain) const {
    return pImpl->unsafeGetTH(retain);
  }

  //toLongData(), toFloatData() etc.
  #define TO_TYPE_DATA(T,name,_) \
  T * to##name##Data() const;
  AT_FORALL_SCALAR_TYPES(TO_TYPE_DATA)
  #undef TO_TYPE_DATA

  template<typename T, size_t N>
  TensorAccessor<T,N> accessor() {
    static_assert(N > 0, "accessor is used for indexing tensor, for scalars use *data<T>()");
    AT_ASSERT(dim() == N, "expected %d dims but tensor has %d",N,dim());
    return TensorAccessor<T,N>(data<T>(),sizes().data(),strides().data());
  }

  Tensor operator-() const;
  Tensor& operator+=(const Tensor & other);
  Tensor& operator+=(Scalar other);
  Tensor& operator-=(const Tensor & other);
  Tensor& operator-=(Scalar other);
  Tensor& operator*=(const Tensor & other);
  Tensor& operator*=(Scalar other);
  Tensor& operator/=(const Tensor & other);
  Tensor& operator/=(Scalar other);
  Tensor operator[](int64_t idx) const;

  //example
  //Tensor * add(Tensor & b);
  ${tensor_method_declarations}
};

} //namespace at
