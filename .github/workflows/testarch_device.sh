#!/bin/bash

export INTERCHANGE_SCHEMA_PATH="$GITHUB_WORKSPACE/env/fpga-interchange-schema/interchange"
export CAPNP_PATH="$GITHUB_WORKSPACE/env/capnproto-java/compiler/src/main/schema/"

python3 fpga_interchange/testarch_generators/generate_testarch.py --schema_dir $INTERCHANGE_SCHEMA_PATH
