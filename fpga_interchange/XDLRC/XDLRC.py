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
Code to generate XDLRC files based on the information provided in
RapidWright interchange DeviceResources capnp device representations.
Contains class XDLRC, which extends the DeviceResources class found in
this repository's device_resource.py.  This class uses the Python
DeviceResources object in conjunction with the Python capnproto object
to generate the information found in an ISE XDLRC file of a device.
The XDLRC generator will print out the tile and primitive_def
declarations in the same order as ISE; however the internal declarations
for these data types are not the same order.
There are some differences between the ISE file and XDLRC file produced
by this code.  They are outlined in the README.
"""

from fpga_interchange.interchange_capnp import Interchange, read_capnp_file
from fpga_interchange.device_resources import DeviceResources, convert_direction
import sys


class DummyFile():
    """Fake file"""

    def write(*args, **kwargs):
        return

    def close(*args, **kwargs):
        return


class XDLRC(DeviceResources):
    """
    Class for generating XDLRC files from Interchange device resources.
    This class contains the main/helper routines associated with
    generating a XDLRC file.  Creating an instance of the class
    automatically will generate the XDLRC file.

    Members:
    tiles   (list)      - Tiles sorted by X,Y coordinates.
    family  (str)       - Device family, if provided.
    pkg                 - Pointer to device_resource_capnp.package,
                          if provided, otherwise None.
    site_alias (dict)   - Uses pkg to find alias names for IOBs.
                          Key: site_name    Value: pin_name
    grade    (str)      - Device grade, if provided.
    xdlrc    (file)     - xdlrc file to write to.
    route_thru  (dict)  - Key: site_type    Value: tuple(pin0, pin1)
    skip_route_thru     - Skip routethroughs in primitive_defs.

    Functions:
    generate_tile       - Generate single tile.
    generate_prim_defs  - Generate Primitive Defs.
    generate_XDLRC      - Generate full XDLRC
    generate_XDLRC_PLUS - Generate XDLRC with extra information
    """

    def __sort_tile_cols__(tile):
        """
        Helper function for sort.

        NOT designed for use outside of being a key function for sort().
        Helps sort() sort the tiles based on col number

        NOTE: self is purposely not included as the first arguement.
        """
        return tile.col

    def __init__(self,
                 device_resource,
                 fileName='',
                 family="",
                 pkg="",
                 grade=""):
        """
        Initialize the XDLRC object.
        Parameters:
            device_resource - Object to obtain device information from.
                              Can be instance of DeviceResources or
                              interchange_capnp.read_capnp_file() output
            fileName (str)  - Name of file to create/write to.  Can be
                              none for no file operations.
            family (str)    - Name of Device Family
            pkg (str)       - Device package
            grade (str)     - Device grade
        """

        if type(device_resource) is DeviceResources:
            # TODO test this feature
            self.__dict__ = device_resource.__dict__.copy()
        else:
            super().__init__(device_resource)

        self.tiles = []
        tiles_by_row = [[]]
        for tile in self.device_resource_capnp.tileList:
            # Create a list of lists of tiles by row
            if len(tiles_by_row) <= tile.row:
                for i in range(tile.row - len(tiles_by_row)):
                    tiles_by_row.append([])
                tiles_by_row.append([tile])
            else:
                tiles_by_row[tile.row].append(tile)

        # sort each row list by column and then attach to master tile list
        for tile_row in tiles_by_row:
            tile_row.sort(key=XDLRC.__sort_tile_cols__)
            self.tiles += tile_row

        # set up file to write to
        if fileName is not None:
            if fileName == '':
                fileName = self.device_resource_capnp.name + ".xdlrc"
            self.xdlrc = open(fileName, "w+")
        else:
            self.xdlrc = DummyFile()

        self.family = family
        self.site_alias = {}
        self.grade = ""
        if pkg:
            self.pkg = None
            for p in self.device_resource_capnp.packages:
                if self.strs[p.name] == pkg:
                    self.pkg = p
                    for pin in p.packagePins:
                        if pin.site.which == "site":
                            site_name = self.strs[pin.site.site]
                            pin_name = self.strs[pin.packagePin]
                            self.site_alias[site_name] = pin_name
                    break
                if self.pkg is None:
                    pkg_options = []
                    for p in self.device_resource_capnp.packages:
                        pkg_options.append(self.strs[p.name])
                    print(f"Warning: Invalid pkg: {pkg}\n Pkg options:" +
                          f" {pkg_options}\n")
                    sys.exit()
            if grade:
                for g in self.pkg.grades:
                    if self.strs[g.name] == grade:
                        self.grade = grade
                        break
                if not self.grade:
                    grade_options = []
                    for g in self.pkg.grades:
                        grade_options.append(self.strs[g.name])
                    print(f"Warning: Invalid grade: {grade}\nGrade options: " +
                          f"{grade_options}\n")
                    sys.exit()
        else:
            self.pkg = None

        self.route_throughs = {}
        self.rt_pips = []
        self.skip_routethru = False

        # counters for xdlrc summary
        self.num_sites = 0
        self.num_pips = 0
        self.num_pins = 0

    def close_file(self):
        self.xdlrc.close()

    def generate_alt_site_types(self):
        for site in self.device_resource_capnp.siteTypeList:
            if len(site.altSiteTypes) != 0:
                self.xdlrc.write(
                    f"(alternate_site_types {self.strs[site.name]}")
                for alt in site.altSiteTypes:
                    name = self.device_resource_capnp.siteTypeList[alt].name
                    self.xdlrc.write(f" {self.strs[name]}")
                self.xdlrc.write(f")\n")

    def _generate_sites(self, sites, tile_type_r, site_pins, pin_tile_wires):
        """Returns number of pinwires"""
        # Some pointers for abbreviated reference
        raw_repr = self.device_resource_capnp
        xdlrc = self.xdlrc

        num_pinwires = 0
        # PRIMITIVE_SITE declaration
        for site in sites:
            site_name = self.strs[site.name]

            site_type_in_tile_type = tile_type_r.siteTypes[site.type]
            site_type_r_idx = site_type_in_tile_type.primaryType
            site_type_r = raw_repr.siteTypeList[site_type_r_idx]
            site_t_name = self.strs[site_type_r.name]

            site = self.site_name_to_site[site_name][site_t_name]

            site_t = self.get_site_type(site.site_type_index)

            if site_name in self.site_alias:
                site_name = self.site_alias[site_name]
                bond = "bonded "
            elif "IOB" in site_t_name or "PAD" in site_t_name:
                if self.site_alias:
                    bond = "unbonded "
                else:
                    bond = "unknown "
            else:
                bond = "internal "
            xdlrc.write(f"\t\t(primitive_site {site_name} {site_t_name} " +
                        f"{bond}{len(site_t.site_pins.keys())}\n")

            site_pins[site_t_name] = []

            # PINWIRE declaration
            # site_pin to tile_wire list
            site_to_tile = site_type_in_tile_type.primaryPinsToTileWires
            site_to_tile = site_type_in_tile_type.primaryPinsToTileWires
            for idx, pin in enumerate(site_type_r.pins):
                pin_name = self.strs[pin.name]
                tile_wire = self.strs[site_to_tile[idx]]
                pin = site_t.site_pins[pin_name]
                direction = pin[3].name.lower()
                num_pinwires += 1
                pin_tile_wires.add(tile_wire)
                xdlrc.write(f"\t\t\t(pinwire {pin_name} {direction} " +
                            f"{tile_wire})\n")

                site_pins[site_t_name].append(pin_name)

            xdlrc.write(f"\t\t)\n")
        return num_pinwires

    def _generate_wires(self, tile_name, tile_wires, pin_tile_wires, idx_list):
        """Return number of wires"""
        # Some pointers for abbreviated reference
        raw_repr = self.device_resource_capnp
        xdlrc = self.xdlrc

        num_wires = 0
        # WIRE declaration
        for idx in idx_list:
            wire_name = self.strs[idx]
            try:
                node_idx = self.node(tile_name, wire_name).node_index
            except AssertionError as e:
                continue
            myNode = raw_repr.nodes[node_idx]

            num_wires += 1
            tile_wires.add(wire_name)
            xdlrc.write(f"\t\t(wire {wire_name} {len(myNode.wires) -1}")

            if len(myNode.wires) == 1:  # no CONNs
                xdlrc.write(')\n')
                continue
            else:
                xdlrc.write('\n')

            # CONN declaration
            for w in myNode.wires:
                wire = raw_repr.wires[w]
                conn_tile = self.strs[wire.tile]
                conn_wire = self.strs[wire.wire]

                if (conn_wire != wire_name) or (conn_tile != tile_name):
                    xdlrc.write(f"\t\t\t(conn {conn_tile} {conn_wire})\n")

            xdlrc.write(f"\t\t)\n")

        for wire in (pin_tile_wires - tile_wires):
            num_wires += 1
            xdlrc.write(f"\t\t(wire {wire} {0})\n")

        return num_wires

    def _generate_pips(self, tile_name, tile, tile_type, tile_type_r, pips,
                       site_pins):
        """Generate Pips with routethroughs"""

        # Pointer for abbreviated reference
        xdlrc = self.xdlrc
        raw_repr = self.device_resource_capnp

        for p in pips:
            wire0 = self.strs[tile_type.wires[p.wire0]]
            wire1 = self.strs[tile_type.wires[p.wire1]]
            rt = ""  # _ROUTE_THROUGH
            full_pip = tile_type.wire_id_to_pip[p.wire0, p.wire1]
            if full_pip.buffered21 and full_pip.which() == 'pseudoCells':
                for site in tile.sites:
                    site_name = self.strs[site.name]
                    site_info = tile_type_r.siteTypes[site.type]
                    conns = site_info.primaryPinsToTileWires
                    pins = raw_repr.siteTypeList[site_info.primaryType].pins
                    site_t = self.strs[raw_repr.siteTypeList[site_info.
                                                             primaryType].name]
                    pin0 = ""
                    pin1 = ""
                    for idx, conn in enumerate(conns):
                        if self.strs[conn] == wire0:
                            pin0 = self.strs[pins[idx].name]
                        elif self.strs[conn] == wire1:
                            pin1 = self.strs[pins[idx].name]
                    if pin0 and pin1:
                        break
                    else:
                        pin0 = ""
                        pin1 = ""
                rt = f" (_ROUTETHROUGH-{pin0}-{pin1} {site_t})"
                if site_t not in self.route_throughs:
                    self.route_throughs[site_t] = set()
                self.route_throughs[site_t].add((pin0, pin1))

            if p.directional:
                xdlrc.write(f"\t\t(pip {tile_name} {wire0} -> {wire1}{rt})\n")
            else:
                xdlrc.write(f"\t\t(pip {tile_name} {wire0} =- {wire1}{rt})\n")
                xdlrc.write(f"\t\t(pip {tile_name} {wire1} =- {wire0}{rt})\n")

    def _generate_tile(self, tile):
        """
        The heavy lifting for generating xdlrc for a tile.

        Returns a tuple(num_sites, num_pips)
        """

        # Some pointers for abbreviated reference
        raw_repr = self.device_resource_capnp
        xdlrc = self.xdlrc

        tile_name = self.strs[tile.name]

        tile_type = self.get_tile_type(tile.type)
        tile_type_r = raw_repr.tileTypeList[tile_type.tile_type_index]

        tile_type.name = self.strs[tile_type_r.name]
        tile_type.wires = tile_type_r.wires
        pips = tile_type.pips
        num_sites = len(tile.sites)
        xdlrc.write(f"\t(tile {tile.row} {tile.col} {tile_name} " +
                    f"{tile_type.name} {num_sites}\n")

        pin_tile_wires = set()
        # This is used for ROUTETHROUGH analysis
        site_pins = {}  # "site_t_name", [pins]

        # PRIMITIVE_SITE declarations
        num_pinwires = self._generate_sites(tile.sites, tile_type_r, site_pins,
                                            pin_tile_wires)

        # WIRE declarations
        tile_wires = set()
        num_wires = self._generate_wires(
            tile_name, tile_wires, pin_tile_wires,
            tile_type.string_index_to_wire_id_in_tile_type.keys())

        # PIP declaration
        self._generate_pips(tile_name, tile, tile_type, tile_type_r, pips,
                            site_pins)

        # TILE_SUMMARY declaration
        xdlrc.write(f"\t\t(tile_summary {tile_name} {tile_type.name} ")
        xdlrc.write(f"{num_pinwires} {num_wires} {len(pips)})\n")
        xdlrc.write(f"\t)\n")
        return (num_sites, len(pips), num_pinwires)

    def generate_tile(self, tile_name):
        """
        Generate a single tile representation for tile_name (str).

        Returns a tuple(num_sites, num_pips)
        """
        for tile in self.tiles:
            name = self.strs[tile.name]
            if name == tile_name:
                return self._generate_tile(tile)

    def generate_tiles(self):
        """Generate all tiles"""
        # TILES declaration
        num_rows = self.tiles[-1].row + 1
        num_cols = self.tiles[-1].col + 1
        self.xdlrc.write(f"(tiles {num_rows} {num_cols}\n")

        # TILE declarations
        for tile in self.tiles:
            tmp_sites, tmp_pips, tmp_pins = self._generate_tile(tile)
            self.num_sites += tmp_sites
            self.num_pips += tmp_pips
            self.num_pins += tmp_pins

        self.xdlrc.write(")\n")

    def generate_prim_defs(self):
        """Generate the primitive_defs."""
        # some pointers for abbreviated reference
        raw_repr = self.device_resource_capnp
        xdlrc = self.xdlrc

        if self.skip_routethru:
            self.route_throughs = {}

        # PRIMITIVE_DEFS declaration
        xdlrc.write(f" (primitive_defs {len(raw_repr.siteTypeList)}\n")

        # PRIMITIVE_DEF declarations
        # Semantics to ensure primitive_defs are added alphabetically
        site_types = {}
        for idx in range(len(raw_repr.siteTypeList)):
            site_t = self.get_site_type(idx)
            site_types[site_t.site_type] = site_t

        site_type_names = list(site_types.keys())
        site_type_names.sort()

        for i in site_type_names:
            site_t = site_types[i]
            # TODO Symbiflow added this to the python SiteType class
            site_t_r = raw_repr.siteTypeList[site_t.site_type_index]
            site_wires = site_t_r.siteWires

            xdlrc.write(f"\t(primitive_def {site_t.site_type} " +
                        f"{len(site_t.site_pins)} {len(site_t.bels)}\n")
            # PIN declaration
            for pin_name, pin in site_t.site_pins.items():
                direction = pin[3].name.lower()
                xdlrc.write(f"\t\t(pin {pin_name} {pin_name} {direction})\n")

            # ELEMENT declaration
            for bel in site_t.bels:
                xdlrc.write(f"\t\t(element {bel.name} {len(bel.bel_pins)}\n")

                # 1 is the enum for routing
                add_cfg = [] if (bel.category == 1) else None

                # TODO Symbiflow adjusted bel pin representation in SiteType
                for bel_pin in bel.bel_pins:
                    # PIN declaration
                    bel_pin_index = site_t.bel_pin_index[bel_pin]
                    bel_pin_name = bel_pin_index[1]
                    bel_info = site_t.bel_pins[bel_pin_index]
                    direction = bel_info[2].name.lower()
                    if direction == 'inout' or direction == 'input':
                        xdlrc.write(f"\t\t\t(pin {bel_pin_name} input)\n")
                        if add_cfg is not None:
                            add_cfg.append(bel_pin_name)
                    else:
                        xdlrc.write(f"\t\t\t(pin {bel_pin_name} output)\n")

                    # CONN declaration
                    site_wire_index = bel_info[1]

                    if site_wire_index is None:
                        # sometimes an element pin has no conn statements
                        continue

                    if direction == 'input':
                        direction_str = '<=='
                    elif direction == 'inout':
                        direction = ''
                    else:
                        direction_str = '==>'

                    for pin_idx in site_wires[site_wire_index].pins:
                        bel_pin2_r = site_t_r.belPins[pin_idx]
                        bel2_name = self.strs[bel_pin2_r.bel]
                        if bel2_name != bel.name:
                            bel_pin2_name = self.strs[bel_pin2_r.name]
                            direction2 = convert_direction(
                                bel_pin2_r.dir).name.lower()
                            if not direction:
                                if direction2 == 'input':
                                    xdlrc.write(f"\t\t\t(conn {bel.name} " +
                                                f"{bel_pin_name} ==> " +
                                                f"{bel2_name} " +
                                                f"{bel_pin2_name})\n")
                                elif direction2 == 'inout':
                                    xdlrc.write(f"\t\t\t(conn {bel.name} " +
                                                f"{bel_pin_name} <== " +
                                                f"{bel2_name} " +
                                                f"{bel_pin2_name})\n")
                                    xdlrc.write(f"\t\t\t(conn {bel.name} " +
                                                f"{bel_pin_name} ==> " +
                                                f"{bel2_name} " +
                                                f"{bel_pin2_name})\n")
                                else:
                                    xdlrc.write(f"\t\t\t(conn {bel.name} " +
                                                f"{bel_pin_name} <== " +
                                                f"{bel2_name} " +
                                                f"{bel_pin2_name})\n")
                            elif direction2 != direction:
                                xdlrc.write(f"\t\t\t(conn {bel.name} " +
                                            f"{bel_pin_name} " +
                                            f"{direction_str} {bel2_name}" +
                                            f" {bel_pin2_name})\n")
                if add_cfg is not None:
                    xdlrc.write(
                        f"\t\t\t(cfg {' '.join(e for e in add_cfg)})\n")
                xdlrc.write(f"\t\t)\n")

            if i in self.route_throughs:
                pins = self.route_throughs[i]
                for p in pins:
                    rt = f"_ROUTETHROUGH-{p[0]}-{p[1]}"
                    xdlrc.write(f"\t\t(element {rt} 2\n")
                    xdlrc.write(f"\t\t\t(pin {p[0]} input)\n")
                    xdlrc.write(f"\t\t\t(pin {p[1]} output)\n")
                    xdlrc.write(f"\t\t)\n")
            xdlrc.write(f"\t)\n")
        xdlrc.write(f")\n")

    def generate_header(self):
        pkg = self.strs[self.pkg.name] if self.pkg.name else ""
        name = self.device_resource_capnp.name + pkg + self.grade
        self.xdlrc.write(f"(xdl_resource_report v0.2 {name} {self.family}\n")

    def generate_footer(self):
        # SUMMARY
        self.xdlrc.write(
            f"(summary tiles={len(self.tiles)} sites={self.num_sites} " +
            f"sitedefs={len(self.site_types)} " +
            f"numpins={self.num_pins} numpips={self.num_pips})\n)")


def argparse_setup():
    """Setup argparse and return parsed arguements."""
    import argparse
    parser = argparse.ArgumentParser(
        description="Generate XLDRC file and check for accuracy")
    parser.add_argument("SCHEMA", help="Location of CapnProto Device Schema")
    parser.add_argument(
        "DEVICE", help="Interchange-CapnProto device representation")
    parser.add_argument(
        "FAMILY", help="The family of the part", default="artix7")
    parser.add_argument("PKG", help="Name of part package", default="csg324")
    parser.add_argument("GRADE", help="Speed grade of part", default="-1")
    parser.add_argument(
        "FILE", help="Name of output XDLRC file", nargs='?', default="")
    parser.add_argument(
        "-x", "--extra", help="Generate XDLRC+ file", action="store_true")
    parser.add_argument("--no-rt", help="Exclude Routethroughs from Elements")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-t", "--tile", help="Generate XDLRC for a single tile")
    group.add_argument(
        "-p",
        "--prim-defs",
        help="Generate XDLRC for Primitive_Defs only",
        action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = argparse_setup()

    device_schema = Interchange(args.SCHEMA).device_resources_schema.Device
    device = XDLRC(
        read_capnp_file(device_schema, open(args.DEVICE, "rb")), args.FILE,
        args.FAMILY, args.PKG, args.GRADE)

    if args.no_rt:
        device.skip_routethru = True

    if args.tile:
        device.generate_tile(args.tile)
        device.close_file()
    elif args.prim_defs:
        device.generate_prim_defs()
        device.close_file()
    else:
        device.generate_header()
        if args.extra:
            device.generate_alt_site_types()
        device.generate_tiles()
        device.generate_prim_defs()
        device.generate_footer()
        device.close_file()
