This material is based upon work supported  by the Office of Naval Research under Contract No. N68335-20-C-0569. Any opinions, findings and conclusions or recommendations expressed in this material are those of the author(s) and do not necessarily reflect the views of the Office of Naval Research.
<br><br><br>
# How to run Tests:

## Generate Vivado Tile Database
In Vivado tcl shell:
```
source tileInfo.tcl
```
This should output a file called xc7a100tcsg324_info.json. This file is about 1.2 GB so generation takes a few minutes.<br>
A dictionary, `{"TileName":{"pips":[], "sites":[], "wires":[]}}`, is
extracted from Vivado. This information is used to produce a 2 out of 3
majority vote when determining if a discrepancy between ISE and
interchange is expected or not.

## Run main test
Python3.9 is required for this test code.<br><br>
General Usage:
```
python test_xdlrc.py [-h] [--ex EX] [-e E] [-t | -p | --no-gen] [TEST_XDLRC] [CORRECT_XDLRC] [DEVICE] [SCHEMAS] [DIR]

Generate XLDRC file and check for accuracy. The most accurate comparison is a full comparison of the two files since the test code uses information gathered from previous XDLRC sections to accurately assess differences encountered in later sections.

positional arguments:
  TEST_XDLRC       XDLRC file to test for accuracy.
  CORRECT_XDLRC    Correct XDLRC file to compare against.
  DEVICE           DeviceResources capnp file to use for XDLRC generation.
  SCHEMAS          Location of interchange capnp schemas for XDLRC generation.
  DIR              Directory where files are located.

optional arguments:
  -h, --help       show this help message and exit.
  --ex EX          Name of known exception file.
  -e E             Name of error output file.
  --no-gen         Do not generate XDLRC file.
  -t, --tile       Parse files as single tile. --no-gen is implied.
  -p, --prim-defs  Parse files as primitive_defs only. --no-gen is implied.
```
This will produce a few files:
    XDLRC_ERRORS.txt - If the test is successful this file should show zero errors.
    XDLRC_EXCEPTIONS.txt - This contains a list of expected differences encountered between the two XDLRC files.
    WireArray.tcl - This contains a list of wires that are presumed to be nodeless.

## Sanity Check Nodeless Wires:
It turns out that querying Vivado for the nodes for a wire is resource intensive. So, instead of adding all nodeless wires to the Vivado database to compare against, a tcl array is created so that the wire-node lookup is limited to the wires in question. To make sure that the given wires do not have nodes run the following in Vivado tcl shell:
```
source check_wires.tcl
```
If this check is successful, it should produce an empty file called: wire_check_results.txt<br>If this file is not empty, then the erroneous wires are listed.