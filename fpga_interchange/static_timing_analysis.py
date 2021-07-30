#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Copyright (C) 2021 The SymbiFlow Authors.
#
# Use this source code is governed by a ISC-style
# license that can be found in LICENSE file or at
# https://opensource.org/licenses/ISC
#
# SPDX-License-Identifier: ISC
"""
This file defines TimingAnalyzer class.

The TimingAnalyzer is static timing analyzer based on Elmore delay model.
It takes as an input device, physical netlist and schema path.
Before running analysis it is required to run all create functions as netlist patcher and
delay calculator uses maps generated by these functions.

Patching is performed in steps:
    1. For all trees in net
        1.1 If node is belpin then
            1.1.1 Explore all belpins that are outputs for belpins that are used in design and store them in temp
            1.1.2 Remove form temp belpins that are visible in child nodes
            1.1.3 Add new found belpins to child nodes
        1.2 Explore child nodes
    2. Search for sources and sinks that go to the same BEL, if such pair exist, assume pseudo sitePIP
        and connect them up into single tree
Step 2 is only checked if net has more then 1 source

Signal delay calculations are performed as follows:
    1. For all trees in net
        1.1 Check type of node
        1.2 Add time delay, set resistance and in_site variable accordingly to node type
        1.3 If node has child nodes then
            1.3.1 Explore child nodes
        1.4 Else
            1.4.1 Store pair (node, dleay to node)
    2. Find longest delay and store it separately
"""

import argparse
import copy
import sys

from fpga_interchange.interchange_capnp import Interchange

# Right now interchange has fast/slow speed models and max,typ,min process corners
# SECOND_CHOICE is used when device doesn't have requested speed model.
SECOND_CHOICE = {'slow': 'fast', 'fast': 'slow'}

# This array is used, when desired process corner is unavailable in device corner models,
# in such case STA looks for any model, starting from max and finishing on min
ALL_POSSIBLE_VALUES = ['max', 'typ', 'min']

indent = 0


class TimingAnalyzer():
    def __init__(self,
                 schema_path,
                 netlist_path,
                 device_path,
                 verbose=False,
                 process="slow",
                 corner="typ"):
        self.verbose = verbose
        self.corner = corner
        self.process = process
        interchange = Interchange(schema_path)
        with open(device_path, "rb") as device_file:
            self.device = interchange.read_device_resources_raw(device_file)
        with open(netlist_path, "rb") as netlist:
            self.phy_netlist = interchange.read_physical_netlist_raw(
                netlist).as_builder()

        self.timing_to_all_ends = {}
        self.longest_path = {}

        # mapping form physical netlist strList to device strList
        self.net_dev_string_map = {}
        # mapping form device netlist strList to physical strList
        self.dev_net_string_map = {}

        # mappig from tile name and wire name to node_id
        self.node_map = {}
        # mapping from node_id to node
        self.node_id_map = {}
        # mapping from (tileType, wire) to list of pips connected to wire
        self.wire_id_to_pip_map = {}
        # mapping from (tile,wire0,wire1) to pip
        self.pip_map = {}
        # mapping from phy_netlist site name to device site type
        self.site_map = {}
        # mapping from (siteType, sitePin) to cornermodel
        self.sitePin_map = {}
        # mapping for sitePIPs from (siteType, belpinidx) to cornermodel
        self.sitePIP_map = {}
        # mapping from tile name to tile type
        self.tile_map = {}
        # mapping from device (siteType, bel, pin) to device BELPin in siteType of site
        self.BELPin_map = {}
        # mapping from phy_netlist (site, bel) to delay list
        self.cell_map = {}
        # mapping from device (siteType, belpinidx) to sitewireidx
        self.belpin_sitewire_map = {}
        # mapping from (netlist site, device bel, device belpin) to True if belpin is used in design
        self.placment_check = set()
        # mapping from (netlist site, device bel, device belpin) to (cellName, CellPin)
        self.cell_pin_map = {}
        # mapping for sitePIPs from (device siteType, device belpinidx) to device belpinidx of output pin
        self.site_pip_output_map = {}

        dev_string = {}
        for i, s in enumerate(self.device.strList):
            dev_string[s] = i
        net_string = []
        for i, s in enumerate(self.phy_netlist.strList):
            net_string.append((s, i))
        for t in net_string:
            if t[0] in dev_string:
                self.net_dev_string_map[t[1]] = dev_string[t[0]]
            else:
                self.net_dev_string_map[t[1]] = None

        net_string = {}
        for i, s in enumerate(self.phy_netlist.strList):
            net_string[s] = i
        dev_string = []
        for i, s in enumerate(self.device.strList):
            dev_string.append((s, i))
        for t in dev_string:
            if t[0] in net_string:
                self.dev_net_string_map[t[1]] = net_string[t[0]]
            else:
                self.dev_net_string_map[t[1]] = None

        for i, node in enumerate(self.device.nodes):
            for wire in node.wires:
                wire_data = self.device.wires[wire]
                self.node_map[(wire_data.tile, wire_data.wire)] = i
                self.node_id_map[i] = node

        for i, tileType in enumerate(self.device.tileTypeList):
            for wire in tileType.wires:
                self.wire_id_to_pip_map[(i, wire)] = []

            for pip in tileType.pips:
                wire0 = tileType.wires[pip.wire0]
                wire1 = tileType.wires[pip.wire1]
                self.wire_id_to_pip_map[(i, wire0)].append((pip, True))
                self.wire_id_to_pip_map[(i, wire1)].append((pip, False))
                self.pip_map[(i, wire0, wire1)] = pip

        for tile in self.device.tileList:
            self.tile_map[tile.name] = tile.type
        temp_dict = {}
        for i, site in enumerate(self.device.siteTypeList):
            temp_dict[site.name] = i
        for site in self.phy_netlist.siteInsts:
            dev_name = self.net_dev_string_map[site.type]
            self.site_map[site.site] = temp_dict[dev_name]

        temp_dict = {}
        for cell_type in self.device.cellBelMap:
            temp_dict[cell_type.cell] = cell_type.pinsDelay
        for cell in self.phy_netlist.placements:
            type_ = self.net_dev_string_map[cell.type]
            if type_ not in temp_dict.keys():
                continue
            self.cell_map[(cell.site, cell.bel)] = temp_dict[type_]

        for i, siteType in enumerate(self.device.siteTypeList):
            for j, belpin in enumerate(siteType.belPins):
                self.BELPin_map[(i, belpin.bel, belpin.name)] = j

            for j, wire in enumerate(siteType.siteWires):
                for pin in wire.pins:
                    self.belpin_sitewire_map[(i, pin)] = j

            for pip in siteType.sitePIPs:
                self.site_pip_output_map[(i, pip.inpin)] = pip.outpin
                self.site_pip_output_map[(i, pip.outpin)] = pip.outpin
                self.sitePIP_map[(i, pip.inpin)] = pip.delay

            for pin in siteType.pins:
                model = None
                which = pin.model.which()
                if which == "capacitance":
                    model = pin.model.capacitance
                elif which == "resistance":
                    model = pin.model.resistance
                else:
                    model = None
                self.sitePin_map[(i, pin.name)] = (pin.dir, model, pin.delay)

        for placed in self.phy_netlist.placements:
            for pin in placed.pinMap:
                self.placment_check.add(
                    (placed.site, self.net_dev_string_map[pin.bel],
                     self.net_dev_string_map[pin.belPin]))

                self.cell_pin_map[(self.net_dev_string_map[placed.site],
                                   self.net_dev_string_map[pin.bel],
                                   self.net_dev_string_map[pin.belPin])] = (
                                       placed.cellName, pin.cellPin)

    def net_name(self, net):
        return self.phy_netlist.strList[net.name]

    def remove_delays_from_const_networks(self, net):
        pass

    def fix_netlist(self, net):
        """
            Given physical net it will traverse and search for reachable belpins not listed in net structure
            but declared by cell placement.
            Also it tries to fix situations where net goes through site BEL as pseudo sitePIP.
            Some tools break up such net into 2 trees in one net.
        """
        global indent
        ends_array = []
        sinks_array = []
        sources_array = []

        def find_connected_bels(site, siteType, belpinIdx):
            connected_bels = []
            wireIdx = self.belpin_sitewire_map[(siteType, belpinIdx)]
            for pin in self.device.siteTypeList[siteType].siteWires[
                    wireIdx].pins:
                _belpin = self.device.siteTypeList[siteType].belPins[pin]
                if (site, _belpin.bel, _belpin.name) in self.placment_check:
                    connected_bels.append(
                        (site, self.dev_net_string_map[_belpin.bel],
                         self.dev_net_string_map[_belpin.name]))
            return connected_bels

        def dfs_traverse(vertex, start):
            global indent
            which = vertex.routeSegment.which()
            obj = None
            if which == "belPin":
                obj = vertex.routeSegment.belPin
                siteType = self.site_map[obj.site]
                bel = self.net_dev_string_map[obj.bel]
                pin = self.net_dev_string_map[obj.pin]
                belpin = self.BELPin_map[(siteType, bel, pin)]
                if self.device.siteTypeList[siteType].belPins[
                        belpin].dir not in ["input", "inout"]:
                    temp = find_connected_bels(obj.site, siteType, belpin)
                    if (obj.site, obj.bel, obj.pin) in temp:
                        temp.remove((obj.site, obj.bel, obj.pin))
                    for branch in vertex.branches:
                        if branch.routeSegment.which() != 'belPin':
                            continue
                        temp_obj = branch.routeSegment.belPin
                        if (temp_obj.site, temp_obj.bel, temp_obj.pin) in temp:
                            temp.remove((temp_obj.site, temp_obj.bel,
                                         temp_obj.pin))

                    if self.verbose:
                        indent += 1
                        print("\t" * indent + "Exploring",
                              self.phy_netlist.strList[obj.site],
                              self.phy_netlist.strList[obj.bel],
                              self.phy_netlist.strList[obj.pin])
                        if len(temp) > 0:
                            print("\t" * indent + "found bels:")
                        indent += 1
                        for new_end in temp:
                            print("\t" * indent,
                                  self.phy_netlist.strList[new_end[0]],
                                  self.phy_netlist.strList[new_end[1]],
                                  self.phy_netlist.strList[new_end[2]])
                        indent -= 2
                    old_branches = vertex.disown('branches')
                    vertex.init('branches',
                                len(old_branches.get()) + len(temp))
                    for i, branch in enumerate(old_branches.get()):
                        vertex.branches[i] = branch
                    for i, new_end in enumerate(temp):
                        branch = vertex.branches[len(old_branches.get()) + i]
                        branch.routeSegment.init('belPin')
                        branch.routeSegment.belPin.site = new_end[0]
                        branch.routeSegment.belPin.bel = new_end[1]
                        branch.routeSegment.belPin.pin = new_end[2]
            elif which == "sitePin":
                obj = vertex.routeSegment.sitePin
            elif which == "pip":
                obj = vertex.routeSegment.pip
            elif which == "sitePIP":
                obj = vertex.routeSegment.sitePIP

            last = len(vertex.branches) == 0
            if not last:
                for branch in vertex.branches:
                    dfs_traverse(branch, False)
            elif not start:
                # in some cases signal may go to sitePIP to get inverted, in such cases we must allow for sitePIP
                # to be an end to netlist
                assert which == "belPin" or which == "sitePIP"
                ends_array.append((vertex, (obj.site, obj.bel, obj.pin)))
            return

        if self.verbose:
            indent += 1
            print("\t" * indent + f"{self.phy_netlist.strList[net.name]}")
            indent += 1
        for i, source in enumerate(net.sources):
            ends_array = []
            which = source.routeSegment.which()
            if which == "belPin":
                obj = source.routeSegment.belPin
                sources_array.append((i, (obj.site, obj.bel, obj.pin)))
                dfs_traverse(source, True)
            elif which == "pip":
                sources_array.append((i, None))
                dfs_traverse(source, False)
            else:
                raise
            sinks_array.extend(ends_array)
        if len(net.sources) > 1:
            # if net has more than 1 source it's either:
            # global constant network
            # network that goes through cells as pseudo sitePIP
            if self.verbose:
                print("\t" * indent + "Searching for pseudo sitePIPs")
            # assumption is that if some bel has both net sink and source it's probably pseudo sitePIP
            old_sources = net.disown('sources')
            new_sources = []
            for sink in sinks_array:
                match = []
                for source in sources_array:
                    if source[1] is not None\
                       and source[1][0] == sink[1][0]\
                       and source[1][1] == sink [1][1]\
                       and source[1][2] != sink [1][2]:
                        match.append(source)
                node = sink[0]
                node.init('branches', len(match))
                for i, s in enumerate(match):
                    node.branches[i] = old_sources.get()[s[0]]
                    sources_array.remove(s)
            net.init('sources', len(sources_array))
            for i, source in enumerate(sources_array):
                net.sources[i] = old_sources.get()[source[0]]
        if self.verbose:
            indent -= 2

    def calculate_delays_for_net(self, net):
        """
            Given a net, this function calculates the delay from each source to each sink
            and retruns the value of the bigest delay in the net.
        """
        ends_array = []

        def get_value_from_model(model):
            process = getattr(model, self.process)
            if process.which() == self.process:
                process = getattr(process, self.process)
                corner = getattr(process, self.corner)
                if corner.which() == self.corner:
                    return getattr(corner, self.corner)
                for corner in ALL_POSSIBLE_VALUES:
                    if getattr(process, corner).which() == corner:
                        return getattr(getattr(process, corner), corner)
            process = getattr(model, SECOND_CHOICE[self.process])
            if process.which() == SECOND_CHOICE[self.process]:
                process = getattr(process, SECOND_CHOICE[self.process])
                corner = getattr(process, self.corner)
                if corner.which() == self.corner:
                    return getattr(corner, self.corner)
                for corner in ALL_POSSIBLE_VALUES:
                    if getattr(process, corner).which() == corner:
                        return getattr(getattr(process, corner), corner)
            else:
                return 0

        def get_largest_delay(delays, dType, BELPin, first_wire=True):
            if len(delays) == 0:
                return 0
            temp_delay = 0
            siteType = self.site_map[BELPin.site]
            bel = self.net_dev_string_map[BELPin.bel]
            belPinName = self.net_dev_string_map[BELPin.pin]
            index = self.BELPin_map[(siteType, bel, belPinName)]
            for delay in delays:
                pin = delay.firstPin.pin if first_wire else delay.secondPin.pin
                if pin == index and dType == delay.pinsDelayType:
                    temp_delay = max(temp_delay,
                                     get_value_from_model(delay.cornerModel))
            return temp_delay

        # This calculates delay due to connected pips, even if they are not active.
        def get_pips_delay(pip_list, resistance):
            delay = 0
            for pip in pip_list:
                pip_timing = self.device.pipTimings[pip[0].timing]
                if pip[1]:
                    delay += get_value_from_model(pip_timing.inputCapacitance)\
                             * resistance * 0.5
            return delay

        def node_delay(node, resistance, temp_delay):
            if len(self.device.nodeTimings) > 0:
                node_model = self.device.nodeTimings[node.nodeTiming]
                node_resistance = get_value_from_model(node_model.resistance)
                node_capacitance = get_value_from_model(node_model.capacitance)
                resistance += node_resistance
                temp_delay += resistance * (node_capacitance) * 0.5
            return resistance, temp_delay

        def dfs_traverse(vertex, resistance, delay, in_site):
            which = vertex.routeSegment.which()
            temp_delay = 0
            return_value = delay
            last = len(vertex.branches) == 0
            obj = None
            if which == "belPin":
                obj = vertex.routeSegment.belPin
                t = self.site_map[obj.site]
                key = (obj.site, obj.bel)
                if key in self.cell_map.keys():
                    delays = self.cell_map[key]
                    if not last:
                        temp_delay = get_largest_delay(delays, "comb", obj)
                    else:
                        temp_delay = get_largest_delay(delays, "setup", obj)
                        return_value += temp_delay

            elif which == "sitePin":
                obj = vertex.routeSegment.sitePin
                siteType = self.site_map[obj.site]
                pinName = self.net_dev_string_map[obj.pin]
                key = (siteType, pinName)
                if key in self.sitePin_map.keys():
                    direction, model, _delay = self.sitePin_map[key]
                    if direction == "output":
                        resistance += get_value_from_model(model)
                    elif direction == "input":
                        temp_delay = resistance * get_value_from_model(model)
                    else:
                        raise
                    temp_delay += get_value_from_model(_delay)
                in_site = True

            elif which == "pip":
                obj = vertex.routeSegment.pip
                tile = self.net_dev_string_map[obj.tile]
                tile_type = self.tile_map[tile]
                wire0 = self.net_dev_string_map[obj.wire0]
                wire1 = self.net_dev_string_map[obj.wire1]
                key = (tile_type, wire0, wire1)
                if key in self.pip_map.keys():
                    pip = self.pip_map[key]
                else:
                    key = (key[0], key[2], key[1])
                    pip = self.pip_map[key]

                if not pip.directional and not obj.forward:
                    temp = wire0
                    wire0 = wire1
                    wire1 = temp

                # Calculate delay from slice to tile
                node_id = self.node_map[(tile, wire0)]
                node = self.node_id_map[node_id]
                if in_site:
                    in_site = False
                    resistance, temp_delay = node_delay(
                        node, resistance, temp_delay)

                # delay on PIP
                if len(self.device.pipTimings) > 0:
                    temp_delay += get_pips_delay(
                        self.wire_id_to_pip_map[(tile_type, wire0)],
                        resistance)
                    pip_timing = self.device.pipTimings[pip.timing]

                    if  (pip.directional or obj.forward) and pip.buffered21 or\
                        not obj.forward and not pip.directional and pip.buffered20:
                        temp_delay += resistance * get_value_from_model(
                            pip_timing.internalCapacitance)

                    temp_delay += get_value_from_model(
                        pip_timing.internalDelay)
                    if (pip.directional or obj.forward) and pip.buffered21 or\
                        not obj.forward and not pip.directional and pip.buffered20:
                        resistance = get_value_from_model(
                            pip_timing.outputResistance)
                    else:
                        resistance += get_value_from_model(
                            pip_timing.outputResistance)

                    temp_delay += get_value_from_model(pip_timing.outputCapacitance)\
                                  * resistance * 0.5
                    temp_delay += get_pips_delay(
                        self.wire_id_to_pip_map[(tile_type, wire1)],
                        resistance)
                # Calculate delay for next node
                node_id = self.node_map[(tile, wire1)]
                node = self.node_id_map[node_id]
                resistance, temp_delay = node_delay(node, resistance,
                                                    temp_delay)

            elif which == "sitePIP":
                obj = vertex.routeSegment.sitePIP
                siteType = self.site_map[obj.site]
                bel = self.net_dev_string_map[obj.bel]
                belPinName = self.net_dev_string_map[obj.pin]
                index = self.BELPin_map[(siteType, bel, belPinName)]
                key = (siteType, index)
                if key in self.sitePIP_map.keys():
                    model = self.sitePIP_map[key]
                    temp_delay = get_value_from_model(model)
            for branch in vertex.branches:
                return_value = max(
                    dfs_traverse(branch, resistance, delay + temp_delay,
                                 in_site), return_value)
            if last:
                ends_array.append((self.net_dev_string_map[obj.site],
                                   self.net_dev_string_map[obj.bel],
                                   self.net_dev_string_map[obj.pin], delay))
            return return_value

        self.timing_to_all_ends[net] = []

        return_value = 0
        for source in net.sources:
            ends_array = []
            temp_delay = 0
            which = source.routeSegment.which()
            if which == "belPin":
                obj = source.routeSegment.belPin
                key = (obj.site, obj.bel)
                if key in self.cell_map.keys():
                    delays = self.cell_map[key]
                    temp_delay = get_largest_delay(delays, "clk2q", obj, False)
                for branch in source.branches:
                    return_value = max(
                        dfs_traverse(branch, 0, temp_delay, True),
                        return_value)
            elif which == 'pip':
                obj = source.routeSegment.pip
                for branch in source.branches:
                    return_value = max(
                        dfs_traverse(branch, 0, temp_delay, False),
                        return_value)
            else:
                raise
            self.timing_to_all_ends[net].append((obj, ends_array))
        self.longest_path[net] = return_value
        return return_value


# ============================================================================


def main():

    global indent
    parser = argparse.ArgumentParser(
        description="Performs static timing analysis")
    parser.add_argument(
        "--schema_dir",
        required=True,
        help="Path to FPGA interchange capnp schema files")
    parser.add_argument(
        "--physical_netlist",
        required=True,
        help="Path to physical netlist for timing analysis")
    parser.add_argument("--device", required=True, help="Path to device capnp")
    parser.add_argument(
        "--verbose",
        action='store_true',
        help="If set analyze will print more information")
    parser.add_argument(
        "--compact",
        action='store_true',
        help="If set analyze will print timings as net_anme and value in ps")

    args = parser.parse_args()
    analyzer = TimingAnalyzer(args.schema_dir, args.physical_netlist,
                              args.device, args.verbose)
    array = []
    for net in analyzer.phy_netlist.physNets:
        array.append(net)
    if args.verbose:
        print("\t" * indent + "Patching physical netlist")
    for net in array:
        analyzer.fix_netlist(net)
    for net in array:
        analyzer.calculate_delays_for_net(net)
    for i, net in enumerate(array):
        if net.type == "signal":
            if args.compact:
                for source, ends in analyzer.timing_to_all_ends[net]:
                    for end in ends:
                        key = (end[0], end[1], end[2])
                        if key in analyzer.cell_pin_map.keys():
                            (cell_name, cell_pin) = analyzer.cell_pin_map[key]
                            cell_name = analyzer.phy_netlist.strList[cell_name]
                            cell_pin = analyzer.phy_netlist.strList[cell_pin]
                            print(
                                f"{analyzer.net_name(net)}_to_{cell_name}/{cell_pin} {end[3] * 1e12}"
                            )
                continue
            print(
                "\t" * indent +
                f"Net {analyzer.net_name(net)} max time delay: {analyzer.longest_path[net] * 1e9} ns"
            )
            if args.verbose:
                indent += 1
                print("\t" * indent + "Detail report:")
                indent += 1
                for source, ends in analyzer.timing_to_all_ends[net]:
                    if hasattr(source, "site"):
                        print(
                            "\t" * indent +
                            f"(Source) Site {analyzer.phy_netlist.strList[source.site]}, "
                            +
                            "BEL {analyzer.phy_netlist.strList[source.bel]}, BELpin{analyzer.phy_netlist.strList[source.pin]}"
                        )
                    else:
                        print(
                            "\t" * indent +
                            f"(Source) TilePIP {analyzer.phy_netlist.strList[source.tile]}, "
                            +
                            "{analyzer.phy_netlist.strList[source.wire0]} -> {analyzer.phy_netlist.strList[source.wire1]}"
                        )
                    indent += 1
                    for end in ends:
                        print(
                            "\t" * indent +
                            f" -> (Sink) Site {analyzer.device.strList[end[0]]}, "
                            +
                            "BEL {analyzer.device.strList[end[1]]}, BELpin {analyzer.device.strList[end[2]]}"
                        )
                        print("\t" * (indent + 1) +
                              f" time delay {end[3] * 1e9} ns")
                    indent -= 1
                indent -= 2


# =============================================================================

if __name__ == "__main__":
    main()