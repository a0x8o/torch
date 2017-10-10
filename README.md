[![Join the chat at https://gitter.im/torch/torch7](https://badges.gitter.im/Join%20Chat.svg)](https://gitter.im/torch/torch7?utm_source=badge&utm_medium=badge&utm_campaign=pr-badge&utm_content=badge)
[![Build Status](https://travis-ci.org/torch/torch7.svg)](https://travis-ci.org/torch/torch7)

## Need help? ##

* Questions, Support, Install issues: [Google groups](https://groups.google.com/forum/#!forum/torch7)
* Reporting bugs: [torch7](https://github.com/torch/torch7/issues) [nn](https://github.com/torch/nn/issues) [cutorch](https://github.com/torch/cutorch/issues) [cunn](https://github.com/torch/cutorch/issues) [optim](https://github.com/torch/optim/issues) [threads](https://github.com/torch/threads/issues)
* Hanging out with other developers and users (strictly no install issues, no large blobs of text): [Gitter Chat](https://gitter.im/torch/torch7)

<a name="torch.reference.dok"></a>
# Torch Package Reference Manual #

__Torch__ is the main package in [Torch7](http://torch.ch) where data
structures for multi-dimensional tensors and mathematical operations
over these are defined. Additionally, it provides many utilities for
accessing files, serializing objects of arbitrary types and other
useful utilities.

<a name="torch.overview.dok"></a>
## Torch Packages ##

  * Tensor Library
    * [Tensor](doc/tensor.md) defines the _all powerful_ tensor object that provides multi-dimensional numerical arrays with type templating.
    * [Mathematical operations](doc/maths.md) that are defined for the tensor object types.
    * [Storage](doc/storage.md) defines a simple storage interface that controls the underlying storage for any tensor object.
  * File I/O Interface Library
    * [File](doc/file.md) is an abstract interface for common file operations.
    * [Disk File](doc/diskfile.md) defines operations on files stored on disk.
    * [Memory File](doc/memoryfile.md) defines operations on stored in RAM.
    * [Pipe File](doc/pipefile.md) defines operations for using piped commands.
    * [High-Level File operations](doc/serialization.md) defines higher-level serialization functions.
  * Useful Utilities
    * [Timer](doc/timer.md) provides functionality for _measuring time_.
    * [Tester](doc/tester.md) is a generic tester framework.
    * [CmdLine](doc/cmdline.md) is a command line argument parsing utility.
    * [Random](doc/random.md) defines a random number generator package with various distributions.
    * Finally useful [utility](doc/utility.md) functions are provided for easy handling of torch tensor types and class inheritance.

<a name="torch.links.dok"></a>
## Useful Links ##

  * [Community packages](https://github.com/torch/torch7/wiki/Cheatsheet)
  * [Torch Blog](http://torch.ch/blog/)
  * [Torch Slides](https://github.com/soumith/cvpr2015/blob/master/cvpr-torch.pdf)

<<<<<<< HEAD
=======
[![TravisCI Build Status](https://travis-ci.org/caffe2/caffe2.svg?branch=master)](https://travis-ci.org/caffe2/caffe2)
[![Appveyor Build Status](https://ci.appveyor.com/api/projects/status/github/caffe2/caffe2?svg=true)](https://ci.appveyor.com/project/Yangqing/caffe2)

Caffe2 is a lightweight, modular, and scalable deep learning framework. Building on the original [Caffe](http://caffe.berkeleyvision.org), Caffe2 is designed with expression, speed, and modularity in mind.

## News and Events

[Caffe2 research award competition request for proposals](https://research.fb.com/programs/research-awards/proposals/caffe2-rfp/)

## Questions and Feedback

Please use Github issues (https://github.com/caffe2/caffe2/issues) to ask questions, report bugs, and request new features.

Please participate in our survey (https://www.surveymonkey.com/r/caffe2). We will send you information about new releases and special developer events/webinars.


## License and Citation

Caffe2 is released under the [BSD 2-Clause license](https://github.com/Yangqing/caffe2/blob/master/LICENSE).

### Further Resources on [Caffe2.ai](http://caffe2.ai)

* [Installation](http://caffe2.ai/docs/getting-started.html)
* [Learn More](http://caffe2.ai/docs/learn-more.html)
* [Upgrading to Caffe2](http://caffe2.ai/docs/caffe-migration.html)
* [Datasets](http://caffe2.ai/docs/datasets.html)
* [Model Zoo](http://caffe2.ai/docs/zoo.html)
* [Tutorials](http://caffe2.ai/docs/tutorials.html)
* [Operators Catalogue](http://caffe2.ai/docs/operators-catalogue.html)
* [C++ API](http://caffe2.ai/doxygen-c/html/classes.html)
* [Python API](http://caffe2.ai/doxygen-python/html/namespaces.html)
>>>>>>> 8a1a898c090e6c2a8daa9bc59d6ac2dc5ec09a05
