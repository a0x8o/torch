// Copyright 2004-present Facebook. All Rights Reserved.

#pragma once
#include "GLTexture.h"
#include "caffe2/core/common.h"
#include <functional>

class GLContext {
 private:
  static GLContext* _glcontext;
  std::function<const GLTexture*(const int width, const int height)> foreignTextureAllocator =
      nullptr;

 public:
  virtual void set_context() = 0;
  virtual void reset_context() = 0;
  virtual void flush_context() = 0;
  virtual ~GLContext(){};

  static void initGLContext();
  static GLContext* getGLContext();
  static void deleteGLContext();

  static bool GL_EXT_texture_border_clamp_defined();

  void setTextureAllocator(
      std::function<const GLTexture*(const int width, const int height)> textureAllocator) {
    foreignTextureAllocator = textureAllocator;
  }

  std::function<const GLTexture*(const int width, const int height)> getTextureAllocator() {
    return foreignTextureAllocator;
  }
};

bool supportOpenGLES3();

bool isSupportedDevice();

#if CAFFE2_IOS
int iPhoneVersion();
#endif
