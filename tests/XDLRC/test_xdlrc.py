#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# This material is based upon work supported  by the Office of Naval Research
# under Contract No. N68335-20-C-0569. Any opinions, findings and conclusions
# or recommendations expressed in this material are those of the author(s) and
# do not necessarily reflect the views of the Office of Naval Research.
#
# Use of this source code is governed by a ISC-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/ISC
#
# SPDX-License-Identifier: ISC
"""
Functions for comparing two XDLRC files.
Used to test DeviceResources.generate_xdlrc().  Generates and checks for
correctness an XDLRC file for xc7a100tcsg-1 part. Only declarations
contained in XDLRC_KEY_WORD are currently supported (case-insensitive).
If an unknown declaration is encountered, the line is skipped and a
warning is printed.
Note: CFG is recognized as a declaration, but not supported in XDLRC
generation so these lines are skipped without warning or error.
Wires not found in both interchange and ISE are checked against a JSON
database of Vivado wires. The following encoding shows where a wire can
be found:
    100 ISE
    010 Interchange
    001 Vivado
Naturally, this leads to encodings like 101 indicating that the wire is
found in ISE and Vivado but not interchange.
To be ran in the tests directory of the python-fpga-interchange project
with the command:
    $python test_xdlrc.py -m interchange
Differences that are deemed "acceptable" (see XDLRC.py comments) are
tracked separately from errors and stored in the text file
XDLRC_Exceptions.txt.
"""

from collections import namedtuple, OrderedDict
import enum
import json
import sys
import time
import os
import sys
from fpga_interchange.XDLRC.XDLRC import XDLRC  # nopep8
from fpga_interchange.interchange_capnp import Interchange, read_capnp_file, CompressionFormat  # nopep8

global myDevice

############################## Convenient Constants ###########################
FAMILY = "artix7"
PKG = "csg324"
GRADE = "-1"
TEST_XDLRC = 'xc7a100t.xdlrc'
CORRECT_XDLRC = 'xc7a100t.xdlrc'
SCHEMA_DIR = "RapidWright/interchange/fpga-interchange-schema/interchange"  # noqa
DEVICE_FILE = "xc7a100t.device"

#  Filename of JSON dict:
#    {tile_name:{
#    "pips":pip_list,
#    "wires":wire_list,
#    "sites":site_list}
#    }
VIVADO_INFO = "xc7a100tcsg324_info.json"
# Namve of file containing output tcl array of possible nodeless wires.
TCL_FILE_OUT = "WireArray.tcl"
###############################################################################

KeyWords = namedtuple(
    'KeyWords',
    'comment tiles tile wire conn summary pip site pinwire prim_defs prim_def element cfg pin header tile_summary route_thru'
)  # noqa

# Dictionary contains XDLRC declarations as keys and expected token length
# as values
XDLRC_KEY_WORD = {
    '#': 0,
    'TILES': 3,
    'TILE': 6,
    'WIRE': 3,
    'CONN': 6,
    'TILE_SUMMARY': 6,
    'PIP': 4,
    'PRIMITIVE_SITE': 5,
    'PINWIRE': 4,
    'PRIMITIVE_DEFS': 2,
    'PRIMITIVE_DEF': 3,
    'ELEMENT': 3,
    'CFG': 0,
    'PIN': 4,
    'XDL_RESOURCE_REPORT': 0,
    'SUMMARY': 6,
    '_ROUTETHROUGH': 2
}

XDLRC_KEY_WORD_KEYS = KeyWords(
    comment='#',
    tiles='TILES',
    tile='TILE',
    wire='WIRE',
    conn='CONN',
    tile_summary='TILE_SUMMARY',
    pip='PIP',
    site='PRIMITIVE_SITE',
    pinwire='PINWIRE',
    prim_defs='PRIMITIVE_DEFS',
    prim_def='PRIMITIVE_DEF',
    element='ELEMENT',
    cfg='CFG',
    pin='PIN',
    header='XDL_RESOURCE_REPORT',
    summary='SUMMARY',
    route_thru="_ROUTETHROUGH")


class Vivado():
    """
    Handles Vivado data and tcl file output.

    Class Attributes:
    info            -   VIVADO_INFO loaded into memory.
    TCL_F           -   File handle for TCL_FILE_OUT.
    """

    info = {}
    TCL_F = None

    def tcl_print(self, tcl):
        """This is assigned in setup()"""
        pass

    def setup(self):
        """Load the files only once"""
        with open(VIVADO_INFO, "r") as f:
            Vivado.info = json.load(f)
        Vivado.TCL_F = open(TCL_FILE_OUT, "w")
        Vivado.TCL_F.write('set testWires {')
        Vivado.tcl_print = Vivado._tcl_print_first

    def _tcl_print_first(self, tcl):
        Vivado.TCL_F.write(tcl)
        Vivado.tcl_print = Vivado._tcl_print_next

    def _tcl_print_next(self, tcl):
        Vivado.TCL_F.write(f",{tcl}")

    def pip(self, tile, wire0, wire1):
        """Check if pip exists in Vivado"""
        return (f"{wire0} {wire1}" in Vivado.info[tile]["pips"])

    def wire(self, tile, wire):
        """Check if wire exists in Vivado"""
        return (f"{tile}/{wire}" in Vivado.info[tile]["wires"])

    def site(self, tile, site):
        """Check if site exists in Vivado"""
        return (f"{site}" in Vivado.info[tile]["sites"])

    def cleanup(self):
        Vivado.TCL_F.write("}\n")
        Vivado.TCL_F.close()


class ErrorHandle():
    """
    Handle error and exception prints.

    Class Attributes:
    errors              -   Current error count.
    unknowns            -   Record of encountered unkown key words.
    XDLRC_ERRORS        -   Name of file to store error messages.
    error_f             -   File Handle for XDLRC_Errors.
    XDLRC_EXCEPTIONS    -   Name of file to store exception messages.
    exception_f         -   File Handle for XDLRC_EXCEPTIONS.
    """

    _header = ""
    errors = 0
    unknowns = []
    XDLRC_Errors = "XDLRC_ERRORS.txt"
    error_f = None
    XDLRC_Exceptions = "XDLRC_Exceptions.txt"
    exception_f = None

    def setup(self):
        ErrorHandle.error_f = open(ErrorHandle.XDLRC_Errors, "w")
        ErrorHandle.exception_f = open(ErrorHandle.XDLRC_Exceptions, "w")
        ErrorHandle.exception_f.write(
            "Line numbers are expressed CORRECT_XDLRC:TEST_XDLRC\n" +
            "Some errors are not applicable to both files. These are " +
            "expressed with the appropriate side of the colon empty.\n" +
            "See XDLRC.py for further explanation of file contents\n\n\n")

    def err_print(self, str_in):
        ErrorHandle.errors += 1
        ErrorHandle.error_f.write(f"{ErrorHandle._header} {str_in}\n")

    def ex_print(self, exception, str_in):
        ErrorHandle.exception_f.write(
            f"{exception} {ErrorHandle._header} {str_in}\n")

    def cleanup(self):
        ErrorHandle.error_f.write(
            f"Done comparing XDLRC files. Errors: {ErrorHandle.errors}\n")
        ErrorHandle.error_f.close()
        ErrorHandle.exception_f.close()


def file_init(*argv):
    """
    Add line counting and get_line storage to file objects.
    Adds two members to file:
        line_num (int)  - Current line number
        line     (list) - Output of get_line()
    Note: get_line is called to initialize line.
    """

    for f in argv:
        f.line_num = 0
        f.line = []
    get_line(*argv)


def get_line(*argv):
    """
    Get the next eligible line in one or both XDLRC files.
    Strips beginning and end of line of '()\n\t ' characters.  Also
    checks the first word of each line to see if it is a supported XDLRC
    keyword. Uses two global variables - unknowns and lines. Lines is a
    dict that keeps track of line numbers for each file. Unknowns is a
    list of unrecognized XDLRC key words.
    Updates f.line_num to contain current line number.
    Updates f.line to contain the result
    Parameters:
        Any number of (XDLRC) file objects.
    """
    err = ErrorHandle()
    ErrorHandle._header = ""
    for f in argv:
        line = []
        while True:
            line = f.readline()
            if not line:
                # EOF is reached in this file. end of parse
                print(f"file reached EOF\n\n")
                if ErrorHandle.unknowns:
                    print(ErrorHandle.unknowns)
                break

            # keep track of line numbers
            f.line_num += 1

            line = line.strip("()\n\t ")
            if not line:
                continue
            line = line.upper().split()
            key_word = line[0]
            if (key_word not in XDLRC_KEY_WORD_KEYS
                    and key_word[0] != XDLRC_KEY_WORD_KEYS.comment):
                if line[0] not in ErrorHandle.unknowns:
                    print(f"Warning: Unknown Key word {line[0]}. Ignoring line"
                          + f" {f.line_num}")
                    print(line)
                    ErrorHandle.unknowns.append(line[0])
                continue

            elif (key_word[0] != XDLRC_KEY_WORD_KEYS.comment
                  and key_word != XDLRC_KEY_WORD_KEYS.header):

                # Make sure token is appropriate length
                expected_len = XDLRC_KEY_WORD[line[0]]
                actual_len = len(line)
                if actual_len < expected_len:
                    line += ['BLANK'] * (expected_len - actual_len)
                break

        # f.line is updated specifically in this way (NOT with =) to
        # support shallow copies of f.line correctly being updated
        f.line.clear()
        f.line.extend(line)


def assert_equal(obj1, obj2):
    """
    Run assert for equality on two objects.
    Catches AssertionError and prints it. Returns a bool of (obj1 == obj2)
    """

    try:
        assert obj1 == obj2
    except AssertionError as e:
        err = ErrorHandle()
        ErrorHandle._header = ""
        err.err_print(
            f"AssertionError caught.\nObj1:\n{obj1}\nObj2:\n{obj2}\n\n")
        return False
    return True


class Direction(enum.Enum):
    """ Enumeration for direction values. """
    Input = 0
    Output = 1
    Inout = 2

    def convert(input_str):
        input_str = input_str.upper()
        if input_str == 'INPUT':
            return Direction.Input
        elif input_str == 'OUTPUT':
            return Direction.Output
        elif input_str == 'INOUT':
            return Direction.Inout
        else:
            return None


class PinWire(namedtuple('PinWire', 'name direction wire')):
    """
    Lightweight class for holding XDLRC pinwire information.
    __eq__() has been overridden for accurate comparisons.
    __hash__() is overridden so PinWire can be in a set.
    Members:
        name  (str)       - Name of the pin.
        direction (Direction) - Direction of the pin.
        wire      (str)       - Name of the connecting wire.
    """

    def __hash__(self):
        return hash(tuple([self.name, self.direction, self.wire]))

    def __eq__(self, other):
        if type(other) != type(self):
            return False
        return ((self.name == other.name)
                and (self.direction == other.direction)
                and (self.wire == other.wire))


class Pip(namedtuple('Pip', 'wire0, wire1, dir')):
    """
    Lightweight class for holding XDLRC pip information.
    __eq__() is overridden for wire comparison.
    __hash__() is overridden so Pip can be in a set.
    Members:
        wire0 (str)                 - First pip wire
        wire1 (str)                 - Second pip wire
        dir   (str)                 - Verbatim pip direction (->, -=)
    """

    def __hash__(self):
        tmp = [self.wire0, self.wire1, self.dir]
        return hash(tmp.extend(self.route_thru))

    def __eq__(self, other):
        if type(self) != type(other):
            return False
        return (self.wire0 == other.wire0) and (self.wire1 == other.wire1)


class TileStruct(
        namedtuple('TileStruct', 'name type wires pips sites route_thru')):
    """
    Lightweight class for holding XDLRC tile information.
    __eq__() is overridden for accurate comparison.  It is important to
    note that it assumes that "other" is correct.
    Members:
        name  (str)         - Tile name
        type  (str)         - Tile type
        wires (dict)        - Key: Wire Name (str)
                              Value: Associated conns (list of tuples)
        pips  (list)        - list of type Pip
        sites (OrderedDict) - Key: "SiteName SiteType Bonding" (str)
                              Value: PinWires (list of PinWire)
        route_thru (set)    - set of routethrough tuples found in extra
                              pips.
    """

    def __eq__(self, other):
        """
        Check two objects for equality.
        Assumes other is always correct.
        Fails immediately upon type mismatch.
        Fails immediately if tile names differ, otherwise does NOT fail
        immediately upon equality violation.  Will check all elements
        and print out all errors found.  Increments global _error count.
        """

        tmp_err = ErrorHandle.errors
        vivado = Vivado()
        err = ErrorHandle()
        ErrorHandle._header = f"Tile: {self.name} Type: {self.type}"

        if type(other) != type(self):
            return False

        if self.name != other.name:
            err.err_print(
                "Fatal Error: Tile names do not match. Abort compare.")
            err.err_print(f"Name1: {self.name} Name2: {other.name}\n\n")
            return False

        # compare wires
        keys = (set(self.wires.keys()), set(other.wires.keys()))
        common_wires = keys[0].intersection(keys[1])
        uncommon_wires = keys[0].symmetric_difference(keys[1])

        for wire in uncommon_wires:

            if wire in keys[0]:
                if vivado.wire(self.name, wire):
                    note = ""
                    try:
                        global myDevice
                        node_idx = myDevice.node(self.name, wire).node_index
                        myNode = myDevice.device_resource_capnp.nodes[node_idx]
                        note = f"Node found with {len(myNode.wires)} wires"
                    except AssertionError as e:
                        note = "No node associated"
                    err.ex_print("EXTRA_WIRE_EXCEPTION 011",
                                 f"Wire: {wire} {note}")
                else:
                    # Wire is not in Vivado or ISE
                    err.err_print(f"Extra wire 010 {wire}")
            else:
                if vivado.wire(self.name, wire):
                    # Wire is in Vivado, ISE, and interchange but Vivado and
                    # interchange do not document any nodes, so the conns
                    # cannot be properly generated.
                    # TCL script was used to verify that all wires here fall
                    # under this category
                    err.ex_print("NODELESS_WIRE_EXCEPTION 101", f"Wire {wire}")
                    vivado.tcl_print(f"{self.name}/{wire}")
                else:
                    # Wire is only in ISE
                    # This happens zero times. Make it an error?
                    err.ex_print("MISSING_WIRE_EXCEPTION 100", f"Wire {wire}")

        for wire in common_wires:
            conns = self.wires[wire]
            other_conns = other.wires[wire]

            all_conns = (set(conns), set(other_conns))
            uncommon = all_conns[0].symmetric_difference(all_conns[1])

            for conn in uncommon:
                if vivado.wire(conn[0], conn[1]):
                    if conn in all_conns[0]:
                        err.ex_print("EXTRA_WIRE_CONN_EXCEPTION (Conn 011)",
                                     f"Wire: {wire} Conn: {conn}")
                    elif vivado.wire(conn[0], conn[1]):
                        err.ex_print("NODELESS_WIRE_EXCEPTION 101",
                                     f"Wire {conn}")
                        vivado.tcl_print(f"{conn[0]}/{conn[1]}")
                    else:
                        err.err_print(f"Missing conn {conn} for " +
                                      f"wire {wire} 101")
                else:
                    if conn in all_conns[0]:
                        err.err_print(f"Extra conn {conn} for wire {wire} 010")
                    else:
                        err.err_print(
                            f"Missing conn {conn} for wire {wire} 100")

        keys = (set(self.pips.keys()), set(other.pips.keys()))
        common_pips = keys[0].intersection(keys[1])
        uncommon_pips = keys[0].symmetric_difference(keys[1])

        for wire0 in uncommon_pips:
            if wire0 in keys[0]:
                for wire1, data in self.pips[wire0].items():
                    pip_dir = data[0]
                    rt = data[1]
                    if (vivado.pip(self.name, wire0, wire1)
                            or vivado.pip(self.name, wire1, wire0)):
                        err.ex_print(
                            "EXTRA_PIP_EXCEPTION 011",
                            f"Pip {wire0} {pip_dir} " +
                            f"{wire1} ROUTETHRU: {rt}")
                        if rt is not None:
                            self.route_thru.add(rt)
                    else:
                        err.err_print(f"Extra Pip {wire0} {wire1}")
            else:
                for wire1, data in self.pips[wire0].items():
                    pip = f"{wire0} {data[0]} {wire1} ROUTETHRU: {data[1]}"
                    if vivado.pip(self.name, wire0, wire1):
                        err.err_print(f"Missing Pip 101 {pip}")
                    else:
                        err.err_print(f"Missing Pip 100 {pip}")

        for wire0 in common_pips:
            conns = (set(self.pips[wire0].keys()),
                     set(other.pips[wire0].keys()))
            common_conns = conns[0].intersection(conns[1])
            uncommon_conns = conns[0].symmetric_difference(conns[1])

            for wire1 in common_conns:
                data0 = self.pips[wire0][wire1]
                data1 = other.pips[wire0][wire1]
                if data0[0] != data1[0]:
                    pip0 = f"{wire0} {data0[0]} {wire1} ROUTETHRU: {data0[1]}"
                    pip1 = f"{wire0} {data1[0]} {wire1} ROUTETHRU: {data1[1]}"
                    err.err_print(
                        f"Pip direction mismatch Pip0: {pip0} Pip1: {pip1}")
                if data0[1] != data1[1]:
                    pip0 = f"{wire0} {data0[0]} {wire1} ROUTETHRU: {data0[1]}"
                    pip1 = f"{wire0} {data1[0]} {wire1} ROUTETHRU: {data1[1]}"
                    if data0[1] and not data1[1]:
                        err.err_print(f"Extra Routethrough Pip0: {pip0}")
                    elif not data0[1] and data1[1]:
                        err.err_print(f"Missing Routethrough Pip1: {pip0}")
                    else:
                        err.err_print(
                            f"Routethrough mismatch pip0: {pip0} pip1: {pip1}")

            for wire1 in uncommon_conns:
                if wire1 in conns[0]:
                    data = self.pips[wire0][wire1]
                    pip_dir = data[0]
                    rt = data[1]
                    if (vivado.pip(self.name, wire0, wire1)
                            or vivado.pip(self.name, wire1, wire0)):
                        err.ex_print(
                            "EXTRA_PIP_EXCEPTION 011",
                            f"Pip {wire0} {pip_dir} " +
                            f"{wire1} ROUTETHRU: {rt}")
                        if rt is not None:
                            self.route_thru.add(rt)
                    else:
                        err.err_print(f"Extra Pip {wire0} {wire1}")
                else:
                    data = other.pips[wire0][wire1]
                    pip = f"{wire0} {data[0]} {wire1} ROUTETHRU: {data[1]}"
                    if vivado.pip(self.name, wire0, wire1):
                        err.err_print(f"Missing Pip 101 {pip}")
                    else:
                        err.err_print(f"Missing Pip 100 {pip}")

        # compare primitive sites
        common_sites = set()
        if len(self.sites.keys()) != len(other.sites.keys()):
            keys = [set(self.sites.keys()), set(other.sites.keys())]
            common_sites = keys[0].intersection(keys[1])
            uncommon_sites = keys[0].symmetric_difference(keys[1])

            for site in uncommon_sites:
                if site in keys[0]:
                    err.err_print(f"Extra Site {site}")
                else:
                    err.err_print(f"Missing Site {site}")
        else:
            other_sites = OrderedDict()
            for site0, site1 in zip(self.sites.items(), other.sites.items()):
                if site0[0] == site1[0]:
                    common_sites.add(site0[0])
                    other_sites[site1[0]] = site1[1]
                elif vivado.site(self.name, site0[0].split()[0]):
                    err.ex_print(
                        f"SITE_NAME_EXCEPTION",
                        f"Considering {site0[0]} to equal {site1[0]}")
                    other_sites[site0[0]] = site1[1]
                    common_sites.add(site0[0])
            other.sites.update(other_sites)

        for site in common_sites:
            pinwires = set(self.sites[site])
            other_pinwires = set(other.sites[site])

            for pw in pinwires.symmetric_difference(other_pinwires):
                err.err_print(f"PinWire mismatch for {pw}")

        return tmp_err == ErrorHandle.errors


def build_tile_db(f, tileName, typeStr):
    """
    Build a TileStruct of a tile by scanning XDLRC f.
    Breaks on tile_summary or on EOF.
    Parameters:
        f (file object) - file to scan for tile information
    Returns:
        tile - TileStruct representing the tile
    """

    tile = TileStruct(tileName, typeStr, {}, {}, OrderedDict(), set())
    err = ErrorHandle()
    get_line(f)

    while f.line and f.line[0] != XDLRC_KEY_WORD_KEYS.tile_summary:
        if f.line[0] == XDLRC_KEY_WORD_KEYS.wire:

            wire = f.line[1]
            tile.wires[wire] = []
            conns = tile.wires[wire]

            get_line(f)
            while f.line and (f.line[0] == XDLRC_KEY_WORD_KEYS.conn):
                conns.append(tuple([f.line[1], f.line[2]]))
                get_line(f)

        elif f.line[0] == XDLRC_KEY_WORD_KEYS.pip:
            if f.line[2] not in tile.pips:
                tile.pips[f.line[2]] = {}
            if len(f.line) > 5:
                rt = f.line[5][1:].split('-')
                if rt[0] != XDLRC_KEY_WORD_KEYS.route_thru:
                    err.ex_print(f"Unknown pip declaration {f.line}")
                    tile.pips[f.line[2]][f.line[4]] = (f.line[3], None)
                else:
                    tile.pips[f.line[2]][f.line[4]] = (f.line[3],
                                                       (rt[1], rt[2],
                                                        f.line[6]))
            else:
                tile.pips[f.line[2]][f.line[4]] = (f.line[3], None)
            get_line(f)

        elif f.line[0] == XDLRC_KEY_WORD_KEYS.site:
            sites_key = f.line[1] + ' ' + f.line[2] + ' ' + f.line[3]
            tile.sites[sites_key] = []
            pin_wires = tile.sites[sites_key]

            get_line(f)
            while (f.line and (f.line[0] == XDLRC_KEY_WORD_KEYS.pinwire)):

                direction = Direction.convert(f.line[2])
                pin_wires.append(PinWire(f.line[1], direction, f.line[3]))
                get_line(f)
        else:
            error_str = ("Error: build_tile_db() hit default branch\n" +
                         "This should not happen if XDLRC files are equal\n" +
                         f"Line {f.line_num}: {f.line}\n")
            err.err_print(error_str)
            sys.exit()

    return tile


class Conn(namedtuple('Conn', 'bel1 belpin1 bel2 belpin2')):
    """
    Lightweight class for holding XDLRC conn information.
    __eq__() is overridden for accruate comparison
    Members:
        bel1    (str) - Name of the INPUT Bel
        belpin1 (str) - Name of the INPUT Bel pin
        bel2    (str) - Name of the OUTPUT Bel
        belpin2 (str) - Name of the OUTPUT Bel pin
    """

    def __hash__(self):
        return hash(tuple([self.bel1, self.belpin1, self.bel2, self.belpin2]))

    def __eq__(self, other):
        if type(self) != type(other):
            return False

        return ((self.bel1 == other.bel1) and (self.bel2 == other.bel2)
                and (self.belpin1 == other.belpin1)
                and (self.belpin2 == other.belpin2))


class Element(namedtuple('Element', 'name pins conns cfg')):
    """
    Lightweight class for holding XDLRC element information.
    __eq__() is overridden for accruate comparison
    Members:
        name  (str)  - Element name
        pins  (list) - List of Element pins (PinWire)
        conns (list) - List of Element conns (Conn)
        cfg   (list) - List of Element CFG strings
    """

    def __eq__(self, other):
        err = ErrorHandle()
        tmp_err = ErrorHandle.errors

        if type(self) != type(other):
            return False
        if self.name != other.name:
            err.err_print(f"Element name mismatch {self.name} != {other.name}")
            return False

        pin_sets = (set(self.pins), set(other.pins))
        assert len(self.pins) == len(pin_sets[0])
        assert len(other.pins) == len(pin_sets[1])
        conn_sets = (set(self.conns), set(other.conns))
        assert len(self.conns) == len(conn_sets[0])
        assert len(other.conns) == len(conn_sets[1])

        for pin in pin_sets[0].symmetric_difference(pin_sets[1]):
            if pin in pin_sets[0]:
                if "CARRY4_" in pin.name:
                    err.ex_print("CARRY4_EXCEPTION",
                                 f"Element: {self.name} Pinwire {pin}")
                elif self.name == "CIN" or self.name == "PRECYINIT":
                    err.ex_print("CIN_PRECYINIT_EXCEPTION",
                                 f"Extra Pinwire {pin} Element: {self.name}")
                else:
                    err.err_print(f"Extra Element Pinwire {pin}")
            else:
                err.err_print(f"Missing Element Pinwire {pin}")

        for conn in conn_sets[0].symmetric_difference(conn_sets[1]):
            if len(conn_sets[0]) == len(conn_sets[1]):
                err.err_print(
                    f"Element Conn mismatch {conn} Element: {self.name}")
            elif len(conn_sets[0]) > len(conn_sets[1]):
                if "CARRY4_" in conn.bel1 or "CARRY4_" in conn.bel2:
                    err.ex_print("CARRY4_EXCEPTION",
                                 f"Conn to extra CARRY4 element Conn: {conn}")
                elif self.name == "CIN" or self.name == "PRECYINIT":
                    err.ex_print("CIN_PRECYINIT_EXCEPTION",
                                 f"Conn {conn} Element: {self.name}")
                else:
                    err.err_print(
                        f"Extra Element Conn {conn} Element: {self.name}")
            else:
                err.err_print(
                    f"Missing Element Conn {conn} Element: {self.name}")

        if set(self.cfg) != set(other.cfg):
            if len(self.cfg) == 0:
                err.ex_print("CFG_ELEMENT_EXCEPTION", f"Element: {self.name}")
            elif self.name == "CIN" or self.name == "PRECYINIT":
                err.ex_print("CIN_PRECYINIT_EXCEPTION",
                             f"Element: {self.name} CFG: {self.cfg}")
            else:
                c4 = False
                for i in self.cfg:
                    if "CARRY4_" in i:
                        c4 = True
                        break
                if c4:
                    err.ex_print("CARRY4_EXCEPTION",
                                 f"Element: {self.name} CFG: {self.cfg}")
                else:
                    err.err_print(
                        f"CFG mismatch Element: {self.name} {self.cfg} {other.cfg}"
                    )

        return tmp_err == ErrorHandle.errors


class PrimDef(namedtuple('PrimDef', 'name pins elements')):
    """
    Lightweight class for holding XDLRC primitive def information.
    __eq__() is overridden for accurate comparison.  It is important to
    note that it is assumed that the "other" operand is correct.
    Members:
        name     (str)  - Name of Primitive Def
        pins     (dict) - Key: PinWire name (str)
                          Value: PinWire
        elements (dict) - Key: Element name (str)
                          Value: Element details (Element)
    """

    def __eq__(self, other):
        """
        Check two objects for equality.
        Assumes other is always correct.
        Fails immediately upon type mismatch.
        Fails immediately if PrimDef names differ, otherwise does NOT
        fail immediately upon equality violation.  Will check all
        elements and print out all errors found.  Increments global
        _error count.
        """

        if type(self) != type(other):
            return False

        err = ErrorHandle()
        ErrorHandle._header = f"Prim_Def {self.name}"
        if self.name != other.name:
            err.err_print("Fatal Error: Primitive Def name mismatch\n" +
                          f"Name1: {self.name} Name2: {other.name}")
            return False

        tmp_err = ErrorHandle.errors

        # Check pins
        pins = set(self.pins.keys())
        other_pins = set(other.pins.keys())

        for pin in pins.symmetric_difference(other_pins):
            if pin not in pins:
                err.err_print(f"Extra Pin {self.pins[pin]}")
            else:
                err.err_print(f"Missing Pin {other.pins[pin]}")

        for pin in pins.intersection(other_pins):
            if self.pins[pin] != other.pins[pin]:
                err.err_print(
                    f"Pin Mismatch\n\t{self.pins[pin]}\n\t{other.pins[pin]}")
        # Check elements
        keys = set(self.elements.keys())
        other_keys = set(other.elements.keys())

        for key in keys.symmetric_difference(other_keys):
            if key in self.elements:
                if "CARRY4" in key:
                    err.ex_print(f"CARRY4_EXCEPTION", f"Extra Element: {key}")
                elif "_ROUTETHROUGH" in key:
                    rt = key.split('-')[1:]
                    rt.append(self.name)
                    if tuple(rt) in self.extra_rt:
                        err.ex_print(f"EXTRA_ROUTETHROUGH_EXCEPTION",
                                     f"Extra RT Element: {key}")
                    else:
                        err.err_print(f"Extra Element {key}")
                else:
                    err.err_print(f"Extra Element {key}")
            else:
                err.err_print(f"Missing Element {key}")

        for key in keys.intersection(other_keys):
            self.elements[key] == other.elements[key]

        return tmp_err == ErrorHandle.errors


def build_prim_def_db(f, name):
    """
    Build a PrimDef by scanning f.
    Breaks on EOF or new Primitive_Def declaration.
    Parameters:
        f (file object) - file to scan for tile information
    Returns:
        prim_def - PrimDef object representing the primitive_def.
    """
    prim_def = PrimDef(name, {}, {})
    get_line(f)
    err = ErrorHandle()
    ErrorHandle._header = ""

    while (f.line and (f.line[0] != XDLRC_KEY_WORD_KEYS.prim_def)
           and f.line[0] != XDLRC_KEY_WORD_KEYS.summary):
        if f.line[0] == XDLRC_KEY_WORD_KEYS.pin:
            pin_wire = PinWire(f.line[1], Direction.convert(f.line[2]),
                               f.line[3])
            prim_def.pins[f.line[1]] = pin_wire
            get_line(f)
        elif f.line[0] == XDLRC_KEY_WORD_KEYS.element:
            if f.line[2] != '0':  # make sure there is more than just cfg
                element = Element(f.line[1], [], [], [])
                prim_def.elements[f.line[1]] = element
                element = prim_def.elements[f.line[1]]
                get_line(f)

                while f.line:
                    if f.line[0] == XDLRC_KEY_WORD_KEYS.pin:
                        element.pins.append(
                            PinWire(f.line[1], Direction.convert(f.line[2]),
                                    ''))
                        get_line(f)
                    elif f.line[0] == XDLRC_KEY_WORD_KEYS.conn:
                        if f.line[3] == '==>':
                            element.conns.append(
                                Conn(f.line[1], f.line[2], f.line[4],
                                     f.line[5]))
                        else:
                            element.conns.append(
                                Conn(f.line[4], f.line[5], f.line[1],
                                     f.line[2]))
                        get_line(f)
                    elif f.line[0] == XDLRC_KEY_WORD_KEYS.cfg:
                        element.cfg.extend(f.line[1:])
                        get_line(f)
                    else:
                        break
            else:
                err.ex_print("CFG_ELEMENT_EXCEPTION",
                             f"caught on line {f.line_num}")
                get_line(f)
        elif f.line[0] == XDLRC_KEY_WORD_KEYS.cfg:
            get_line(f)
        else:
            err.err_print(f"Error: build_prim_def_db hit default branch\n" +
                          f"Check syntax on line {f.line_num}")
            get_line(f)

    return prim_def


def compare_tile(f1, f2):
    """
    Parse and compare a single tile.
    Assumes file_init() has been executed for each file parameter.
    """

    # Check Tile Header
    assert_equal(f1.line, f2.line)

    tile1 = build_tile_db(f1, f1.line[3], f1.line[4])
    tile2 = build_tile_db(f2, f2.line[3], f2.line[4])

    # Check Tile contents
    # __eq__ is overridden so this line actually does stuff
    tile1 == tile2

    # Check Tile summary
    # This first check accounts for EXTRA_WIRE_EXCEPTION making the summay
    # wire count be off
    err = ErrorHandle()
    ErrorHandle._header = f"Tile: {tile1.name}"
    if f1.line[4] != f2.line[4]:
        err.ex_print(
            "SUMMARY_WIRE_EXCEPTION", f"line {f2.line_num}:" +
            f"{f1.line_num} summary wire count mismatch")
    elif f1.line[5] != f2.line[5]:
        err.ex_print(
            "EXTRA_PIP_EXCEPTION", f"line {f2.line_num}:{f1.line_num} " +
            f"summary pip count mismatch")
    else:
        assert_equal(f1.line, f2.line)

    get_line(f1, f2)
    return tile1.route_thru


def compare_prim_defs(f1, f2, extra_rt=set()):
    """
    Compare the primitive_defs.
    Assumes file_init() has been executed for each file parameter.
    """

    err = ErrorHandle()
    ErrorHandle._header = ""

    # Check primitive_defs declaration
    if f1.line != f2.line:
        err.ex_print(
            "PRIM_DEF_GENERAL_EXCEPTION",
            f"line {f2.line_num}:{f1.line_num} PRIMITIVE_DEFS count mismatch")
    get_line(f1, f2)

    # Primitive_def checks
    while f1.line and f2.line and f1.line[0] != XDLRC_KEY_WORD_KEYS.summary:
        while f2.line[1] != f1.line[1]:  # Not all ISE prim defs represented
            err.ex_print(
                "PRIM_DEF_GENERAL_EXCEPTION",
                f"caught on line {f2.line_num}. PRIMITIVE_DEF {f2.line[1]} missing."
            )
            get_line(f2)
            while f2.line[0] != XDLRC_KEY_WORD_KEYS.prim_def:
                get_line(f2)

        # Elements w/ only CFG bits are not supported, so comparing
        # element count will likely fail. So element cnt is dropped.
        if f2.line[3] != f1.line[3]:
            err.ex_print("CFG_PRIM_DEF_EXCEPTION",
                         f"caught on line {f2.line_num}")
        f2.line = f2.line[:3]
        f1.line = f1.line[:3]

        assert_equal(f1.line, f2.line)

        prim_def1 = build_prim_def_db(f1, f1.line[1])
        prim_def2 = build_prim_def_db(f2, f2.line[1])

        prim_def1.extra_rt = extra_rt
        # __eq__ is overridden so this actually does stuff
        prim_def1 == prim_def2


def compare_xdlrc(f1, f2):
    """
    Compare two xdlrc files for equality.
    Tiles must be listed in the same order. Primitive Def headers must
    be in the same order.  Everything else can be out of order.
    Assumes that file2 has been generated correctly and file1 is being
    checked against it for correctness.
    Assumes file_init() has been executed for each file parameter.
    """

    # check Tiles row_num col_num declaration
    assert_equal(f1.line, f2.line)

    # Tile chekcs
    extra_rt = set()
    get_line(f1, f2)
    while (f1.line and f2.line
           and (f1.line[0] != XDLRC_KEY_WORD_KEYS.prim_defs)):
        tmp_rt = compare_tile(f1, f2)
        extra_rt.update(tmp_rt)

    while (f2.line[0] != XDLRC_KEY_WORD_KEYS.prim_defs):
        get_line(f2)
    compare_prim_defs(f1, f2, extra_rt)

    # This will fail due to PRIM_DEF_GENERAL_EXCEPTION
    err = ErrorHandle()
    err.ex_print("PRIM_DEF_GENERAL_EXCEPTION",
                 f"Summary line mismatch:\n\t{f1.line}\n\t{f2.line}")


def init(fileName='', device_file=DEVICE_FILE, schema_dir=SCHEMA_DIR):
    """
    Set up the environment for __main__.
    Also useful to run after an import for debugging/testing
    Parameters:
        fileName (str) - Name of file to pass to XDLRC constructor
    """

    device_schema = Interchange(SCHEMA_DIR).device_resources_schema.Device
    return XDLRC(
        read_capnp_file(device_schema, open(device_file, "rb"),
                        CompressionFormat.GZIP, False), fileName, FAMILY, PKG,
        GRADE)


def argparse_setup():
    """Setup argparse and return parsed arguements."""
    import argparse
    parser = argparse.ArgumentParser(
        description="Generate XLDRC file and check for accuracy. The most " +
        "accurate comparison is a full comparison of the two files since the "
        + "test code uses information gathered from previous XDLRC sections " +
        "to accurately assess differences encountered in later sections. ")
    parser.add_argument(
        "TEST_XDLRC",
        help="XDLRC file to test for accuracy",
        nargs='?',
        default=TEST_XDLRC)
    parser.add_argument(
        "CORRECT_XDLRC",
        help="Correct XDLRC file to compare against",
        nargs='?',
        default=CORRECT_XDLRC)
    parser.add_argument(
        "DEVICE",
        help="DeviceResources capnp file to use for XDLRC generation",
        nargs='?',
        default=DEVICE_FILE)
    parser.add_argument(
        "SCHEMAS",
        help="Location of interchange capnp schemas for XDLRC generation",
        nargs='?',
        default=SCHEMA_DIR)
    parser.add_argument(
        "DIR", help="Directory where files are located", nargs='?', default='')
    parser.add_argument("--ex", help="Name of known exception file")
    parser.add_argument("-e", help="Name of error output file")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "-t", "--tile", help="Parse files as single tile", action="store_true")
    group.add_argument(
        "-p",
        "--prim-defs",
        help="Parse files as primitive_defs only",
        action="store_true")
    group.add_argument(
        "--no-gen", help="Do not generate XDLRC file", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = argparse_setup()

    if not args.no_gen and not (args.tile or args.prim_defs):
        myDevice = init(args.DIR + args.TEST_XDLRC, args.DEVICE, args.SCHEMAS)
        start = time.time()
        myDevice.generate_XDLRC()
        finish = time.time() - start
        print(f"XDLRC {args.DIR+args.TEST_XDLRC} generated in {finish} sec ")
    else:
        myDevice = init(None, args.DEVICE, args.SCHEMAS)

    if args.ex:
        ErrorHandle.XDLRC_Exceptions = args.ex
    if args.e:
        ErrorHandle.XDLRC_Errors = args.e

    err = ErrorHandle()
    err.setup()
    f1 = open(args.DIR + args.TEST_XDLRC, "r")
    f2 = open(args.DIR + args.CORRECT_XDLRC, "r")

    file_init(f1, f2)
    vivado = Vivado()
    vivado.setup()

    start = time.time()

    if args.tile:
        compare_tile(f1, f2)
    elif args.prim_defs:
        compare_prim_defs(f1, f2)
    else:
        compare_xdlrc(f1, f2)

    finish = time.time() - start
    print(f"XDLRC compared in {finish} seconds")
    vivado.cleanup()

    err.cleanup()
    print(f"Done comparing XDLRC files. Errors: {ErrorHandle.errors}")
