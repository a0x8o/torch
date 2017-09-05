#pragma once

namespace caffe2 {

/**
 *  Use this to implement a Observer using the Observer Pattern template.
 */

template <class T>
class ObserverBase {
 public:
<<<<<<< HEAD
  explicit ObserverBase(T* subject) : subject_(subject) {}
=======
  explicit ObserverBase(T* subject) : subject_(subject) {
    subject_->SetObserver(this);
  }
>>>>>>> 3d8433f8b359d59d9f0db8e916b3a049262b55f3

  virtual bool Start() {
    return false;
  }
  virtual bool Stop() {
    return false;
  }

  virtual ~ObserverBase() noexcept {};

  T* subject() const {
    return subject_;
  }

 protected:
  T* subject_;
};

} // namespace caffe2
