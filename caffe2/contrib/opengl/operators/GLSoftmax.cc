// Copyright 2004-present Facebook. All Rights Reserved.

#include "../core/GLFilter.h"
#include "../core/GLImage.h"

#include "caffe2/core/timer.h"
#include <iostream>
#include <vector>

class GLSoftmaxReduce : public GLFilter {
 public:
  binding* inputSize;
  binding* outputSize;
  binding* tileSize;
  binding* inputData;
  binding* maxData;
  binding* sumData;

  const std::vector<binding*> input_bindings() {
    std::vector<binding*> bindings({BINDING(inputSize),
                                    BINDING(outputSize),
                                    BINDING(tileSize),
                                    BINDING(inputData),
                                    BINDING(maxData),
                                    BINDING(sumData)});
    return bindings;
  }

  GLSoftmaxReduce(bool compute_sum_ = false)
      : GLFilter("GLSoftmaxReduce",
                 vertex_shader,
                 fragment_shader,
                 input_bindings(),
                 {/* no uniform_blocks_bindings */},
                 {/* no attributes */},
                 {{"COMPUTE_SUM", caffe2::to_string((int)compute_sum_)}}) {}

  template <typename T>
  void reduce(const GLImage<T>* input_image,
              const GLImage<T>* output_image,
              int tile_size_x,
              int tile_size_y);

  static const char* fragment_shader;
};

// MARK: GLSL

const char* GLSoftmaxReduce::fragment_shader = R"GLSL(#version 300 es

#define COMPUTE_SUM $(COMPUTE_SUM)

precision highp float;
precision mediump int;
precision mediump sampler2D;

in highp vec2 v_texCoord;

uniform ivec2 inputSize;
uniform ivec2 outputSize;
uniform ivec2 tileSize;

uniform sampler2D inputData;
layout(location = 0) out mediump vec4 outputData;

void main() {
  ivec2 outputCoord = ivec2(v_texCoord * vec2(outputSize));
  ivec2 texelCoord = outputCoord * tileSize;
  ivec2 sumArea = min(tileSize, inputSize - texelCoord);
  vec4 result = vec4(0.0);

  for (int y = 0; y < sumArea.y; y++) {
    for (int x = 0; x < sumArea.x; x++) {
      ivec2 idx = texelCoord + ivec2(x, y);
      vec4 val = texelFetch(inputData, idx, 0);
#if COMPUTE_SUM
      result += val;
#else
      result = max(result, val);
#endif
    }
  }

  outputData = result;
}

)GLSL";

template <typename T>
void GLSoftmaxReduce::reduce(const GLImage<T>* input_image,
                             const GLImage<T>* output_image,
                             int tile_size_x,
                             int tile_size_y) {
  int input_slices = input_image->slices;
  int output_slices = output_image->slices;

  for (int is = 0; is < input_slices; is++) {
    std::vector<texture_attachment> input_attachments({{input_image->textures[is], inputData}});

    run(input_attachments,
        {output_image->textures.begin() + is, output_image->textures.begin() + is + 1},
        [&]() {
          glUniform2i(inputSize->location, input_image->width, input_image->height);
          glUniform2i(outputSize->location, output_image->width, output_image->height);
          glUniform2i(tileSize->location, tile_size_x, tile_size_y);
        },
        output_image->width,
        output_image->height);
  }
}

class GLSoftmaxScale : public GLFilter {
 public:
  binding* outputSize;
  binding* inputData;
  binding* maxData;
  binding* sumData;

  const std::vector<binding*> input_bindings() {
    std::vector<binding*> bindings(
        {BINDING(outputSize), BINDING(inputData), BINDING(maxData), BINDING(sumData)});
    return bindings;
  }

  GLSoftmaxScale(bool _compute_exp = false)
      : GLFilter("GLSoftmaxScale",
                 vertex_shader,
                 fragment_shader,
                 input_bindings(),
                 {/* no uniform blocks */},
                 {/* no attributes */},
                 {{"COMPUTE_EXP", caffe2::to_string((int)_compute_exp)}}) {}

  template <typename T>
  void scale(const GLImage<T>* input_image,
             const GLImage<T>* max_image,
             const GLImage<T>* sum_image,
             const GLImage<T>* output_image);

  static const char* fragment_shader;
};

template <typename T>
void GLSoftmaxScale::scale(const GLImage<T>* input_image,
                           const GLImage<T>* max_image,
                           const GLImage<T>* sum_image,
                           const GLImage<T>* output_image) {
  int input_slices = input_image->slices;
  int output_slices = output_image->slices;

  for (int is = 0; is < input_slices; is++) {
    std::vector<texture_attachment> input_attachments({{input_image->textures[is], inputData},
                                                       {max_image->textures[is], maxData},
                                                       {sum_image->textures[is], sumData}});
    run(input_attachments,
        {output_image->textures.begin() + is, output_image->textures.begin() + is + 1},
        [&]() { glUniform2i(outputSize->location, output_image->width, output_image->height); },
        output_image->width,
        output_image->height);
  }
}

// MARK: GLSL

const char* GLSoftmaxScale::fragment_shader = R"GLSL(#version 300 es

#define COMPUTE_EXP $(COMPUTE_EXP)

precision highp float;
precision mediump int;
precision mediump sampler2D;

in highp vec2 v_texCoord;
uniform ivec2 outputSize;

uniform sampler2D inputData;
uniform sampler2D maxData;
uniform sampler2D sumData;
layout(location = 0) out mediump vec4 outputData;

void main() {
  ivec2 texelCoord = ivec2(v_texCoord * vec2(outputSize));

  highp vec4 val = texelFetch(inputData, texelCoord, 0);
#if COMPUTE_EXP
  highp vec4 maxVal = texelFetch(maxData, ivec2(0), 0);
  outputData = exp(val - maxVal);
#else
  highp vec4 sumVal = texelFetch(sumData, ivec2(0), 0);
  outputData = val / sumVal;
#endif
}

)GLSL";

#include "../core/ImageAllocator.h"
#include "caffe2/core/operator.h"

#ifndef CAFFE2_MOBILE
#error "Caffe2 mobile state not defined"
#endif

#if CAFFE2_MOBILE

namespace caffe2 {
template <class T>
class OpenGLSoftmax final : public Operator<CPUContext>, ImageAllocator<T> {
 public:
  OpenGLSoftmax(const OperatorDef& operator_def, Workspace* ws)
      : Operator<CPUContext>(operator_def, ws),
        order_(StringToStorageOrder(OperatorBase::GetSingleArgument<string>("order", "NCHW"))) {
    OPERATOR_NEEDS_FEATURE(this->order_ == StorageOrder::NCHW, "OpenGL only supports NCHW order.");
  }

  bool RunOnDevice() override {
    const GLImageVector<T>& input = Inputs()[INPUT]->template Get<GLImageVector<T>>();
    const int num_images = input.size();
    const int input_channels = input.channels();
    const int input_width = input.width();
    const int input_height = input.height();

    const int output_channels = input_channels;
    const int output_width = input_width;
    const int output_height = input_height;

    int is_last = OperatorBase::GetSingleArgument<int>("is_last", 0);

    const int tile_size_x = 16;
    const int tile_size_y = 16;

    int max_buf_width = input_width;
    int max_buf_height = input_height;
    vector<GLImageVector<T>*> reduce_buf;
    while (reduce_buf.size() == 0 || (max_buf_height > tile_size_y)) {
      max_buf_width = (max_buf_width + tile_size_x - 1) / tile_size_x;
      max_buf_height = (max_buf_height + tile_size_y - 1) / tile_size_y;
      reduce_buf.push_back(
          ImageAllocator<T>::newImage(1, max_buf_width, max_buf_height, output_channels));
    }

    GLImageVector<T>* max = ImageAllocator<T>::newImage(num_images, 1, 1, output_channels);
    GLImageVector<T>* sum = ImageAllocator<T>::newImage(num_images, 1, 1, output_channels);
    GLImageVector<T>* after_exp =
        ImageAllocator<T>::newImage(num_images, output_width, output_height, output_channels);
    GLImageVector<T>* output_images = ImageAllocator<T>::newImage(
        num_images, output_width, output_height, output_channels, is_last);

    if (!f_max) {
      f_max.reset(new GLSoftmaxReduce());
      f_exp.reset(new GLSoftmaxScale(true));
      f_sum.reset(new GLSoftmaxReduce(true));
      f_scale.reset(new GLSoftmaxScale());
    }

    for (int i = 0; i < num_images; i++) {
      auto input_image = input[i];
      auto max_image = (*max)[i];
      auto sum_image = (*sum)[i];
      auto after_exp_image = (*after_exp)[i];
      auto output_image = (*output_images)[i];
      // Get Max
      for (int ir = 0; ir < reduce_buf.size() + 1; ir++) {
        const GLImage<T>* in = ir == 0 ? input_image : (*reduce_buf[ir - 1])[0];
        GLImage<T>* out = ir == reduce_buf.size() ? max_image : (*reduce_buf[ir])[0];

        const int running_tile_size_x = ir < reduce_buf.size() ? in->width : in->width;
        const int running_tile_size_y = ir < reduce_buf.size() ? in->height : in->height;
        f_max->reduce(in, out, running_tile_size_x, running_tile_size_y);
      }
      // scale vals by exp(x - max)
      f_exp->scale(input_image, max_image, sum_image, after_exp_image);

      // Get sum of the exp
      for (int ir = 0; ir < reduce_buf.size() + 1; ir++) {
        const GLImage<T>* in = ir == 0 ? after_exp_image : (*reduce_buf[ir - 1])[0];
        GLImage<T>* out = ir == reduce_buf.size() ? sum_image : (*reduce_buf[ir])[0];
        const int running_tile_size_x = ir < reduce_buf.size() ? tile_size_x : in->width;
        const int running_tile_size_y = ir < reduce_buf.size() ? tile_size_y : in->height;
        f_sum->reduce(in, out, running_tile_size_x, running_tile_size_y);
      }

      // Scale(softmax)
      f_scale->scale(after_exp_image, max_image, sum_image, output_image);
    }

    Outputs()[OUTPUT]->Reset(output_images);

    delete sum;
    delete max;
    delete after_exp;
    for (auto&& rb : reduce_buf) {
      delete rb;
    }
    return true;
  }

 private:
  StorageOrder order_;
  std::unique_ptr<GLSoftmaxReduce> f_max;
  std::unique_ptr<GLSoftmaxScale> f_exp;
  std::unique_ptr<GLSoftmaxReduce> f_sum;
  std::unique_ptr<GLSoftmaxScale> f_scale;

  INPUT_TAGS(INPUT, FILTER, BIAS);
  OUTPUT_TAGS(OUTPUT);
};

REGISTER_CPU_OPERATOR(OpenGLSoftmax, OpenGLSoftmax<float16_t>);
OPERATOR_SCHEMA(OpenGLSoftmax).NumInputs(1).NumOutputs(1).AllowInplace({{0, 0}});
} // namespace caffe2
#endif // CAFFE2_MOBILE
