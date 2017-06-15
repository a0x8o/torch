#!/usr/bin/env sh
# This script simply starts the ipython notebook and allows all network machines
# to access it.

# Use the following command for very verbose prints.
# GLOG_logtostderr=1 GLOG_v=1 PYTHONPATH=../../../gen:$PYTHONPATH ipython notebook --ip='*'

# Use the following command for a normal run.
PYTHONPATH=../../../gen:$PYTHONPATH ipython notebook --ip='*'
