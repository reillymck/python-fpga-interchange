#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Copyright (C) 2020  The SymbiFlow Authors.
#
# Use of this source code is governed by a ISC-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/ISC
#
# SPDX-License-Identifier: ISC
"""
This file defines the Series-7 devices FASM generator class.

The FASM generator extends the generic one, with additional
functions and handlers for specific elements in the Series-7 devices.

The ultimate goal is to have most of the FASM annotations included in the
device resources, so that the size of this file can be reduced to handle
only very specific cases which are hard to encode in the device database.

Such special cases may include:
    - PLL and MMCM register configuration functions
    - Extra features corresponding to specific PIPs (such as BUFG rebuf)

"""
import re
from enum import Enum
from collections import namedtuple

from fpga_interchange.fasm_generators.generic import FasmGenerator
from fpga_interchange.route_stitching import flatten_segments
from fpga_interchange.physical_netlist import PhysicalPip
"""
This is a helper object that is used to find and emit extra features
that do depend on the usage of specific PIPs or Pseudo PIPs.

regex: is used to identify the correct PIPs.
features: list of extra features to be added.
callback: function to get the correct prefix for the feature, based on the
          regex match results.
"""
ExtraFeatures = namedtuple('ExtraFeatures', 'regex features callback')


class LutsEnum(Enum):
    LUT5 = 0
    LUT6 = 1

    @classmethod
    def from_str(cls, label):
        if label == "LUT5":
            return cls.LUT5
        elif label == "LUT6":
            return cls.LUT6
        else:
            raise NotImplementedError


class XC7FasmGenerator(FasmGenerator):
    def handle_ios(self):
        """
        This function is specialized to add FASM features for the IO buffers
        in the 7-Series database format.
        """

        # FIXME: Need to make this dynamic, and find a suitable way to add FASM annotations to the device resources.
        #        In addition, a reformat of the database might be required to have an easier handling of these
        #        features.
        allowed_io_types = {
            "OBUF": [
                "LVCMOS12_LVCMOS15_LVCMOS18_LVCMOS25_LVCMOS33_LVTTL_SSTL135_SSTL15.SLEW.SLOW",
                "LVCMOS33_LVTTL.DRIVE.I12_I16", "PULLTYPE.NONE"
            ],
            "IBUF": [
                "LVCMOS12_LVCMOS15_LVCMOS18_LVCMOS25_LVCMOS33_LVTTL.SLEW.FAST",
                "LVCMOS12_LVCMOS15_LVCMOS18_LVCMOS25_LVCMOS33_LVDS_25_LVTTL_SSTL135_SSTL15_TMDS_33.IN_ONLY",
                "LVCMOS25_LVCMOS33_LVTTL.IN", "PULLTYPE.NONE"
            ]
        }

        iob_sites = ["IOB_Y1", "IOB_Y0"]

        for cell_instance, cell_data in self.physical_cells_instances.items():
            if cell_data.cell_type not in allowed_io_types:
                continue

            tile_name = cell_data.tile_name

            iob_site_idx = cell_data.sites_in_tile.index(cell_data.site_name)

            iob_site = iob_sites[
                iob_site_idx] if "SING" not in tile_name else "IOB_Y0"

            for feature in allowed_io_types[cell_data.cell_type]:
                self.add_cell_feature((tile_name, iob_site, feature))

    @staticmethod
    def get_slice_prefix(site_name, tile_type, sites_in_tile):
        """
        Returns the slice prefix corresponding to the input site name.
        """

        slice_sites = {
            "CLBLL_L": ["SLICEL_X1", "SLICEL_X0"],
            "CLBLL_R": ["SLICEL_X1", "SLICEL_X0"],
            "CLBLM_L": ["SLICEL_X1", "SLICEM_X0"],
            "CLBLM_R": ["SLICEL_X1", "SLICEM_X0"],
        }

        slice_site_idx = sites_in_tile.index(site_name)
        return slice_sites[tile_type][slice_site_idx]

    def handle_luts(self):
        """
        This function handles LUTs FASM features generation
        """

        bel_re = re.compile("([ABCD])([56])LUT")

        luts = dict()

        for cell_instance, cell_data in self.physical_cells_instances.items():
            if not cell_data.cell_type.startswith("LUT"):
                continue

            site_name = cell_data.site_name
            site_type = cell_data.site_type

            tile_name = cell_data.tile_name
            tile_type = cell_data.tile_type
            sites_in_tile = cell_data.sites_in_tile
            slice_site = self.get_slice_prefix(site_name, tile_type,
                                               sites_in_tile)

            bel = cell_data.bel
            m = bel_re.match(bel)
            assert m, bel

            # A, B, C or D
            lut_loc = m.group(1)
            lut_name = "{}LUT".format(lut_loc)

            # LUT5 or LUT6
            lut_type = "LUT{}".format(m.group(2))

            init_param = self.device_resources.get_parameter_definition(
                cell_data.cell_type, "INIT")
            init_value = init_param.decode_integer(
                cell_data.attributes["INIT"])

            phys_lut_init = self.get_phys_lut_init(init_value, cell_data)

            key = (site_name, lut_loc)
            if key not in luts:
                luts[key] = {
                    "data": (tile_name, slice_site, lut_name),
                    LutsEnum.LUT5: None,
                    LutsEnum.LUT6: None,
                }

            luts[key][LutsEnum.from_str(lut_type)] = phys_lut_init

        for lut in luts.values():
            tile_name, slice_site, lut_name = lut["data"]

            lut5 = lut[LutsEnum.LUT5]
            lut6 = lut[LutsEnum.LUT6]

            if lut5 is not None and lut6 is not None:
                lut_init = "{}{}".format(lut6[0:32], lut5[32:64])
            elif lut5 is not None:
                lut_init = lut5[32:64].zfill(32)
            elif lut6 is not None:
                lut_init = lut6
            else:
                assert False

            init_feature = "INIT[{}:0]={}'b{}".format(
                len(lut_init) - 1, len(lut_init), lut_init)

            self.add_cell_feature((tile_name, slice_site, lut_name,
                                   init_feature))

    def handle_slice_ff(self):
        """
        Handles slice FFs FASM feature emission.
        """

        allowed_cell_types = ["FDRE", "FDSE", "FDCE", "FDPE", "LDCE", "LDPE"]
        allowed_site_types = ["SLICEL", "SLICEM"]

        for cell_instance, cell_data in self.physical_cells_instances.items():
            cell_type = cell_data.cell_type
            if cell_type not in allowed_cell_types:
                continue

            site_name = cell_data.site_name
            site_type = cell_data.site_type

            if site_type not in allowed_site_types:
                continue

            tile_name = cell_data.tile_name
            tile_type = cell_data.tile_type
            sites_in_tile = cell_data.sites_in_tile
            slice_site = self.get_slice_prefix(site_name, tile_type,
                                               sites_in_tile)

            bel = cell_data.bel

            if cell_type in ["FDRE", "FDCE", "LDCE"]:
                self.add_cell_feature((tile_name, slice_site, bel, "ZRST"))

            if cell_type.startswith("LD"):
                self.add_cell_feature((tile_name, slice_site, "LATCH"))

            if cell_type in ["FDRE", "FDCE"]:
                self.add_cell_feature((tile_name, slice_site, "FFSYNC"))

            init_param = self.device_resources.get_parameter_definition(
                cell_data.cell_type, "INIT")
            init_value = init_param.decode_integer(
                cell_data.attributes["INIT"])

            if init_value == 0:
                self.add_cell_feature((tile_name, slice_site, bel, "ZINI"))

    def handle_clock_resources(self):
        for cell_instance, cell_data in self.physical_cells_instances.items():
            cell_type = cell_data.cell_type
            if cell_type not in ["BUFG", "BUFGCTRL"]:
                continue

            site_name = cell_data.site_name
            site_type = cell_data.site_type
            site_loc = cell_data.sites_in_tile.index(site_name)
            site_prefix = "BUFGCTRL.BUFGCTRL_X0Y{}".format(site_loc)

            tile_name = cell_data.tile_name

            self.add_cell_feature((tile_name, site_prefix, "IN_USE"))

            if cell_type == "BUFG":
                for feature in ["IS_IGNORE1_INVERTED", "ZINV_CE0", "ZINV_S0"]:
                    self.add_cell_feature((tile_name, site_prefix, feature))

    def handle_site_thru(self, site_thru_pips):
        """
        This function is currently specialized to add very specific features
        for pseudo PIPs which need to be enabled to get the correct HW behaviour
        """

        def get_feature_prefix(site_thru_feature, wire):
            regex = re.compile(site_thru_feature.regex)

            m = regex.match(wire)

            return site_thru_feature.callback(m) if m else None

        # FIXME: this information needs to be added as an annotation
        #        to the device resources
        site_thru_features = list()
        site_thru_features.append(
            ExtraFeatures(
                regex="IOI_OLOGIC([01])_D1",
                features=["OMUX.D1", "OQUSED", "OSERDES.DATA_RATE_TQ.BUF"],
                callback=lambda m: "OLOGIC_Y{}".format(m.group(1))))
        site_thru_features.append(
            ExtraFeatures(
                regex="[LR]IOI_ILOGIC([01])_D",
                features=["ZINV_D"],
                callback=lambda m: "ILOGIC_Y{}".format(m.group(1))))
        site_thru_features.append(ExtraFeatures(
            regex="CLK_HROW_CK_MUX_OUT_([LR])([0-9]+)",
            features=["IN_USE", "ZINV_CE"],
            callback=lambda m: "BUFHCE.BUFHCE_X{}Y{}".format(0 if m.group(1) == "L" else 1, m.group(2))
            ))
        site_thru_features.append(
            ExtraFeatures(
                regex="CLK_BUFG_BUFGCTRL([0-9]+)_I[01]",
                features=[
                    "IN_USE", "ZINV_CE0", "ZINV_S0", "IS_IGNORE1_INVERTED"
                ],
                callback=
                lambda m: "BUFGCTRL.BUFGCTRL_X0Y{}".format(m.group(1))))

        for tile, wire0, wire1 in site_thru_pips:
            for site_thru_feature in site_thru_features:
                prefix = get_feature_prefix(site_thru_feature, wire0)

                if prefix is None:
                    continue

                for feature in site_thru_feature.features:
                    self.add_cell_feature((tile, prefix, feature))

                break

    def handle_slice_routing_bels(self):
        allowed_routing_bels = list()

        used_muxes = ["SRUSEDMUX", "CEUSEDMUX"]
        allowed_routing_bels.extend(used_muxes)

        for loc in "ABCD":
            ff_mux = "{}FFMUX".format(loc)
            ff5_mux = "{}5FFMUX".format(loc)
            out_mux = "{}OUTMUX".format(loc)
            allowed_routing_bels.extend([ff_mux, ff5_mux, out_mux])

        routing_bels = self.get_routing_bels(allowed_routing_bels)

        for site, bel, pin in routing_bels:
            tile_name, tile_type, sites_in_tile = self.get_tile_info_at_site(
                site)
            slice_prefix = self.get_slice_prefix(site, tile_type,
                                                 sites_in_tile)

            if bel in used_muxes:
                if pin not in ["0", "1"]:
                    self.add_cell_feature((tile_name, slice_prefix, bel))
            else:
                self.add_cell_feature((tile_name, slice_prefix, bel, pin))

    def handle_pips(self):
        """
        Handles all the FASM features corresponding to PIPs

        In addition, emits extra features necessary to have the clock
        resources working properly, as well the emission of site route
        thru features for pseudo PIPs
        """

        # TODO: The FASM database should be reformatted so to have more
        #       regular extra PIP features.
        regexs = list()
        regexs.append(
            ExtraFeatures(
                regex="(CLK_HROW_CK_IN_[LR][0-9]+)",
                features=["_ACTIVE"],
                callback=lambda m: m.group(1)))
        regexs.append(
            ExtraFeatures(
                regex="(CLK_HROW_R_CK_GCLK[0-9]+)",
                features=["_ACTIVE"],
                callback=lambda m: m.group(1)))
        regexs.append(
            ExtraFeatures(
                regex="(HCLK_CMT_CCIO[0-9]+)",
                features=["_ACTIVE", "_USED"],
                callback=lambda m: m.group(1)))
        regexs.append(
            ExtraFeatures(
                regex="CLK_BUFG_REBUF_R_CK_(GCLK[0-9]+)_BOT",
                features=["_ENABLE_BELOW", "_ENABLE_ABOVE"],
                callback=lambda m: m.group(1)))
        regexs.append(
            ExtraFeatures(
                regex="(HCLK_CK_BUFHCLK[0-9]+)",
                features=[""],
                callback=lambda m: "ENABLE_BUFFER.{}".format(m.group(1))))

        tile_types = [
            "HCLK_L", "HCLK_R", "HCLK_L_BOT_UTURN", "HCLK_R_BOT_UTURN",
            "HCLK_CMT", "HCLK_CMT_L", "CLK_HROW_TOP_R", "CLK_HROW_BOT_R",
            "CLK_BUFG_REBUF"
        ]
        extra_features = dict((tile_type, list()) for tile_type in tile_types)

        site_thru_pips = self.fill_pip_features(extra_features)

        for tile_type, tile_pips in extra_features.items():
            for tile, pip in tile_pips:
                for extra_feature in regexs:
                    comp_regex = re.compile(extra_feature.regex)

                    m = comp_regex.match(pip)

                    if m:
                        prefix = extra_feature.callback(m)

                        for f in extra_feature.features:
                            f = "{}{}".format(prefix, f)
                            self.add_cell_feature((tile, f))

        self.handle_site_thru(site_thru_pips)

    def output_fasm(self):
        self.handle_pips()
        self.handle_slice_routing_bels()
        self.handle_slice_ff()
        self.handle_ios()
        self.handle_luts()
        self.handle_clock_resources()

        for cell_feature in sorted(list(self.cells_features)):
            print(cell_feature)

        for routing_pip in sorted(list(self.pips_features)):
            print(routing_pip)
