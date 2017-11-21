# Finds Google Protocol Buffers library and compilers and extends
# the standard cmake script with version and python generation support
function(custom_protobuf_find)
  set(CAFFE2_USE_CUSTOM_PROTOBUF ON PARENT_SCOPE)
  message(STATUS "Use custom protobuf build.")
  # For a custom protobuf build, we will always use static protobuf.
  option(protobuf_BUILD_SHARED_LIBS "" OFF)
  option(protobuf_BUILD_TESTS "" OFF)
  option(protobuf_BUILD_EXAMPLES "" OFF)
  # MSVC protobuf built with static library explicitly uses /MT and /MTd which
  # makes things a bit tricky, so we set it off.
  #option(protobuf_MSVC_STATIC_RUNTIME "" OFF)
  if (APPLE)
    # Protobuf generated files triggers a deprecated atomic operation warning
    # so we turn it off here.
    set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -Wno-deprecated-declarations" PARENT_SCOPE)
  endif()
  add_subdirectory(${PROJECT_SOURCE_DIR}/third_party/protobuf/cmake)
  # To build shared Caffe2 libraries that link to a static protobuf,
  # we need those static libraries to be compiled as PIC.
  set_property(TARGET libprotobuf PROPERTY POSITION_INDEPENDENT_CODE ON)
  set(PROTOBUF_LIBRARIES libprotobuf PARENT_SCOPE)
  set(PROTOBUF_INCLUDE_DIR ${PROJECT_SOURCE_DIR}/third_party/protobuf/src PARENT_SCOPE)
  set(Caffe2_DEPENDENCY_LIBS ${Caffe2_DEPENDENCY_LIBS} PARENT_SCOPE)
  # Figure out which protoc to use.
  # If CAFFE2_CUSTOM_PROTOC_EXECUTABLE is set, we assume the user knows
  # what they're doing and we blindly use the specified protoc. This
  # is typically the case when cross-compiling where protoc must be
  # compiled for the host architecture and libprotobuf must be
  # compiled for the target architecture.
  # If CAFFE2_CUSTOM_PROTOC_EXECUTABLE is NOT set, we use the protoc
  # target that is built as part of including the protobuf project.
  if(EXISTS "${CAFFE2_CUSTOM_PROTOC_EXECUTABLE}")
    set(PROTOBUF_PROTOC_EXECUTABLE ${CAFFE2_CUSTOM_PROTOC_EXECUTABLE} PARENT_SCOPE)
  else()
    set(PROTOBUF_PROTOC_EXECUTABLE $<TARGET_FILE:protoc> PARENT_SCOPE)
  endif()
  set(Protobuf_FOUND TRUE PARENT_SCOPE)
endfunction()

if (WIN32)
  find_package(Protobuf NO_MODULE)
elseif (ANDROID OR IOS)
  custom_protobuf_find()
  # Unfortunately, new protobuf does not support libprotoc and protoc
  # cross-compilation so we will need to exclude it.
  # The problem of using EXCLUDE_FROM_ALL is that one is not going to be able
  # to run cmake install. A proper solution has to be implemented by protobuf
  # since we derive our cmake files from there.
  # TODO(jiayq): change this once https://github.com/google/protobuf/pull/3878
  # merges.
  set_target_properties(
      libprotoc protoc PROPERTIES
      EXCLUDE_FROM_ALL 1 EXCLUDE_FROM_DEFAULT_BUILD 1)
else()
  # Always use libprotobuf from tree if a custom PROTOBUF
  if(EXISTS "${CAFFE2_CUSTOM_PROTOC_EXECUTABLE}")
    custom_protobuf_find()
  else()
    find_package(Protobuf)
    if(Protobuf_FOUND OR PROTOBUF_FOUND)
      # Add PROTOBUF_LIBRARY for backwards compatibility.
      # Newer versions use *PROTOBUF_LIBRARIES*.
      list(APPEND Caffe2_DEPENDENCY_LIBS ${PROTOBUF_LIBRARY})
    endif()
  endif()
endif()

# If Protobuf is not found, do custom protobuf find.
if(NOT(Protobuf_FOUND OR PROTOBUF_FOUND))
  custom_protobuf_find()
endif()

if(NOT(Protobuf_FOUND OR PROTOBUF_FOUND))
  message(FATAL_ERROR "Could not find protobuf or compile local version")
endif()

message(STATUS "Using protobuf compiler ${PROTOBUF_PROTOC_EXECUTABLE}")
caffe2_include_directories(${PROTOBUF_INCLUDE_DIR})
list(APPEND Caffe2_DEPENDENCY_LIBS ${PROTOBUF_LIBRARIES})

################################################################################################
# Modification of standard 'protobuf_generate_cpp()' with output dir parameter and python support
# Usage:
#   caffe2_protobuf_generate_cpp_py(<srcs_var> <hdrs_var> <python_var> <proto_files>)
function(caffe2_protobuf_generate_cpp_py srcs_var hdrs_var python_var)
  if(NOT ARGN)
    message(SEND_ERROR "Error: caffe_protobuf_generate_cpp_py() called without any proto files")
    return()
  endif()

  set(${srcs_var})
  set(${hdrs_var})
  set(${python_var})
  foreach(fil ${ARGN})
    get_filename_component(abs_fil ${fil} ABSOLUTE)
    get_filename_component(fil_we ${fil} NAME_WE)

    list(APPEND ${srcs_var} "${CMAKE_CURRENT_BINARY_DIR}/${fil_we}.pb.cc")
    list(APPEND ${hdrs_var} "${CMAKE_CURRENT_BINARY_DIR}/${fil_we}.pb.h")
    list(APPEND ${python_var} "${CMAKE_CURRENT_BINARY_DIR}/${fil_we}_pb2.py")

    # Note: the following depends on PROTOBUF_PROTOC_EXECUTABLE. This
    # is done to make sure protoc is built before attempting to
    # generate sources if we're using protoc from the third_party
    # directory and are building it as part of the Caffe2 build. If
    # points to an existing path, it is a no-op.
    add_custom_command(
      OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/${fil_we}.pb.cc"
             "${CMAKE_CURRENT_BINARY_DIR}/${fil_we}.pb.h"
             "${CMAKE_CURRENT_BINARY_DIR}/${fil_we}_pb2.py"
      WORKING_DIRECTORY ${PROJECT_SOURCE_DIR}
      COMMAND ${CMAKE_COMMAND} -E make_directory "${CMAKE_CURRENT_BINARY_DIR}"
      COMMAND ${PROTOBUF_PROTOC_EXECUTABLE} -I${PROJECT_SOURCE_DIR} --cpp_out    "${PROJECT_BINARY_DIR}" ${abs_fil}
      COMMAND ${PROTOBUF_PROTOC_EXECUTABLE} -I${PROJECT_SOURCE_DIR} --python_out "${PROJECT_BINARY_DIR}" ${abs_fil}
      DEPENDS ${PROTOBUF_PROTOC_EXECUTABLE} ${abs_fil}
      COMMENT "Running C++/Python protocol buffer compiler on ${fil}" VERBATIM )
  endforeach()

  set_source_files_properties(${${srcs_var}} ${${hdrs_var}} ${${python_var}} PROPERTIES GENERATED TRUE)
  set(${srcs_var} ${${srcs_var}} PARENT_SCOPE)
  set(${hdrs_var} ${${hdrs_var}} PARENT_SCOPE)
  set(${python_var} ${${python_var}} PARENT_SCOPE)
endfunction()
