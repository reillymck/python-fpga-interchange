This material is based upon work supported  by the Office of Naval Research under Contract No. N68335-20-C-0569. Any opinions, findings and conclusions or recommendations expressed in this material are those of the author(s) and do not necessarily reflect the views of the Office of Naval Research.
<br><br><br>

# XDLRC Generator
## Background
With Xilinx ISE, a text file representation was provided of the fpga device. This format is known
as XDLRC. The XDLRC representation is not provided by Xilinx for any devices created since ISE was
replaced with Vivado. However, there exists legacy tools that take device structure input in the form
of an XDLRC file. This code is written to output the information found in an interchange device resources
file as an XDLRC file. However, due to changes in how Xilinx represents devices, one will find that the
produced XDLRC file may have different information than expected. These are detailed below. 

In the tests directory there is test code to compare the information found in an ISE XDLRC file with
the information provided in the interchange device resources representation. The information accessible
in Vivado through tcl is used to decide which format is correct when the two differ.


## XDLRC Generation 
XDLRC files are generated from the RapidWright/interchange capnproto representation of a device. After sourcing RapidWright into your terminal, you can run the following command to generate a capnp device representation (the example command will generate a representation of the xc7a100tcsg324-1 device).
```
source rapidwright.sh
cd RapidWright
java com.xilinx.rapidwright.interchange.DeviceResourcesExample xc7a100tcsg324-1
```
This will output the following file: xc7a100tcsg324-1.device


```
usage: XDLRC.py [-h] [-x] [--no-rt NO_RT] [-t TILE | -p] SCHEMA DEVICE FAMILY PKG GRADE [FILE]

Generate XLDRC file and check for accuracy

positional arguments:
  SCHEMA                Location of CapnProto Device Schema
  DEVICE                Interchange-CapnProto device representation
  FAMILY                The family of the part
  PKG                   Name of part package
  GRADE                 Speed grade of part
  FILE                  Name of output XDLRC file

optional arguments:
  -h, --help            show this help message and exit
  -x, --extra           Generate XDLRC+ file
  --no-rt NO_RT         Exclude Routethroughs from Elements
  -t TILE, --tile TILE  Generate XDLRC for a single tile
  -p, --prim-defs       Generate XDLRC for Primitive_Defs only
```

Note:
  * If FILE is not specified the default is partName.xdlrc, ie: xc7a100t.xdlrc

For example, if you have correctly installed the above dependencies for the user "admin" in the directories specified by the above instructions, and if your .device file is located in the same directory as XDLRC.py, you could run the following command to generate an XDLRC file for the xc7a100tcsg324-1 device:
```
python3 XDLRC.py ~/RapidWright/interchange/fpga-interchange-schema/interchange xc7a100tcsg324-1.device artix7 csg324 -1
```

## XDLRC+
XDLRC+ contains everthing in the original XDLRC file, with additional key
words to denote more information.

| Key Words | Location | Description |
| --- | ----------- | ---------- |
| alternate_site_types | Right before the "tiles" declaration | Lists the alternate sites for the first site |

## Differences Between ISE XDLRC and Interchange XDLRC
The following list breaks down the 
full list of differences between ISE XDLRC and interchange XDLRC differences 
and their frequencies are listed as follows <i>Discrepancy (Occurences)</i>:
  * CARRY4_EXCEPTION (130):<br> 
    In Vivado and Interchange, with SLICELs and SLICEMs
    there are elements in addition to the CARRY4, like CARRY4_XOR, that are
    not listed in ISE.<br>
    Example in SLICEL:
    ```
    Extra Element: CARRY4_DXOR
    Conn to extra CARRY4 element. Conn: Conn(bel1='D6LUT', belpin1='O6', bel2='CARRY4_DXOR', belpin2='0')
    ```

  * CFG_ELEMENT_EXCEPTION (745):<br>
    Some elements only have CFG bits declared inside them. These elements are 
    not found in interchange, but they are in ISE.<br>
    Example in BSCAN:
    ```
    (element DISABLE_JTAG 0
			(cfg FALSE TRUE)
    )
    (element JTAG_CHAIN 0
        (cfg 1 2 3 4)
    )
    ```

  * CFG_PRIM_DEF_EXCEPTION (47): <br>
    Since some elements do not show up, the element count is often off for a 
    primitive_def.<br>
    Example:
    ```
    ISE XDLRC:
        (primitive_def BSCAN 11 14
    Interchange XDLRC:
        (primitive_def BSCAN 11 12
    ```

  * CIN_PRECYINIT_EXCEPTION (8):<br>
    In SLICEL and SLICEM, ISE does not document the connection between the CIN 
    pinwire and the PRECYINIT MUX.<br>
    Example (identical in SLICEL and SLICEM):
    ```
    Extra Conn(bel1='CIN', belpin1='CIN', bel2='PRECYINIT', belpin2='CIN') Element: CIN
    Extra PinWire(name='CIN', direction=<Direction.Input: 0>, wire='') Element: PRECYINIT
    Extra Conn(bel1='CIN', belpin1='CIN', bel2='PRECYINIT', belpin2='CIN') Element: PRECYINIT
    Element: PRECYINIT CFG: ['0', 'CIN', '1', 'AX']
    ```

  * EXTRA_PIP_EXCEPTION (10346): <br>
    Sometimes Interchange/Vivado documents pips not in ISE. It appears all of
    these pips do have nodes associated with them in Vivado.<br>
    Example in Tile: IO_INT_INTERFACE_L_X0Y199 Type: IO_INT_INTERFACE_L:
    ```
    Pip INT_INTERFACE_LOGIC_OUTS_L_B22 -> INT_INTERFACE_LOGIC_OUTS_L22
    Pip INT_INTERFACE_LOGIC_OUTS_L_B16 -> INT_INTERFACE_LOGIC_OUTS_L16
    Pip INT_INTERFACE_LOGIC_OUTS_L_B13 -> INT_INTERFACE_LOGIC_OUTS_L13
    ```

  * EXTRA_ROUTETHROUGH_EXCEPTION (1): <br>
    Some of the extra pips are also routethroughs, and sometimes the routethrough
    soley occurs in the extra pips. The routethrough is in both interchange and
    Vivado.

  * EXTRA_WIRE_EXCEPTION (97771): <br>
    For some reason, interchange has tile/wire
    information that is not included in ISE. This wire information can be
    found in Vivado 2020. The earliest occurance for this is tile
    TERM_CMT_X8Y208. Interchange includes information on 4 wires that
    connect to this tile for part xc7a100t. ISE's XDLRC for xc7a100tcsg-1
    does not show any wires for this tile, and there is no conn statement
    for a wire connecting to this tile in the rest of the ISE XDLRC. All of
    these wires have nodes associated with them.

  * EXTRA_WIRE_CONN_EXCEPTION (464226): <br>
    The wires found only in interchange/Vivado are also found in conns. 

  * SUMMARY_WIRE_EXCEPTION (21824): <br>
    The extra wires cause the total wire count in a tile to differ.

  * NODELESS_WIRE_EXCEPTION (33958): <br>
    Wire conns are generated by examining the interchange wire-node list. 
    Some wires do not have nodes, but in ISE they do have conns. Even though 
    these wires are found in Vivado and interchange, in Vivado and interchange 
    there are not associated nodes, so the conns cannot be properly generated.<br>
    Example:
    ```
    Tile: LIOI3_SING_X0Y199 Type: LIOI3_SING Wire IOI_LOGIC_OUTS6_0
    Tile: LIOI3_SING_X0Y199 Type: LIOI3_SING Wire IOI_LOGIC_OUTS21_0
    Tile: LIOI3_SING_X0Y199 Type: LIOI3_SING Wire IOI_LOGIC_OUTS4_0
    Tile: LIOI3_SING_X0Y199 Type: LIOI3_SING Wire IOI_BLOCK_OUTS3_0
    Tile: LIOI3_SING_X0Y199 Type: LIOI3_SING Wire IOI_BLOCK_OUTS1_0
    ```

  * PRIM_DEF_GENERAL_EXCEPTION (26):<br>
    Not all primitive_defs generated by ISE are included in the interchange 
    representation. One example for the xc7a100t part is the AMS_ADC, which 
    shows up in ISE's XDLRC for xc7a100tcsg-1. It seems that ISE prints the 
    primitive_defs for all 7-series parts, regardless of if they are used on 
    the specific chip.
