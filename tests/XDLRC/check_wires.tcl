# This material is based upon work supported  by the Office of Naval Research
# under Contract No. N68335-20-C-0569. Any opinions, findings and conclusions 
# or recommendations expressed in this material are those of the author(s) and 
# do not necessarily reflect the views of the Office of Naval Research.



# An empty file called "wire_check_results.txt" is expected. If the file is not
# empty, then it lists wires that do have nodes, but are only found in ISE. These
# wires are listed in the format tile/wire, with one on each line.

# source the array of wires output from test_xdlrc
source WireArray.tcl

proc check_wires {file_name inputArray} { 
    set f [open $file_name w]
    set tile_list [get_tiles]
    set tile_count [llength $tile_list]
	set k 0
    for {set i 0} {$i < $tile_count} {incr i} { 
        set T [lindex $tile_list $i]
        set wire_list [get_wires -of_objects $T]
        set wire_count [llength $wire_list]
        for {set j 0} {$j < $wire_count} {incr j} { 
            set wire [lindex $wire_list $j]
			if {$wire == [lindex $inputArray $k]} {
				incr k
				set node [get_nodes -of_objects $wire]
				if {[llength $node] != 0} {
                    # wires that do have a node will be printed to the file
                    # if the run is successful, the file should be empty
					puts $f "\"$wire\""
				}
			}
        }        
    }
    close $f
}

link_design -part xc7a100tcsg324-1
check_wires "wire_check_results.txt" testWires