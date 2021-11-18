# This material is based upon work supported  by the Office of Naval Research
# under Contract No. N68335-20-C-0569. Any opinions, findings and conclusions 
# or recommendations expressed in this material are those of the author(s) and 
# do not necessarily reflect the views of the Office of Naval Research.


proc tile_pips_to_json {file_name} { 
    set f [open $file_name w]
    set tile_list [get_tiles]
    set tile_count [llength $tile_list]
    puts $f "\{"
    for {set i 0} {$i < $tile_count} {incr i} { 
        set T [lindex $tile_list $i]
        puts $f "\"$T\":\{"
        puts $f "\"pips\":\["
        set pip_list [get_pips -of_objects $T]
        set pip_count [llength $pip_list]
        for {set j 0} {$j < $pip_count} {incr j} { 
            set raw [lindex $pip_list $j]
			set pip [string trimleft [string map {"->>" " " "<<->>" " " "<-" " " "->" " " "<<-" " "} [string range $raw [string first "." $raw] 1000]] "."]
			if { $j==[expr {$pip_count-1} ]} {
				puts $f "\"$pip\""
			} else {
				puts $f "\"$pip\","
			}
        }
        puts $f "\],"
        puts $f "\"wires\":\["
        set wire_list [get_wires -of_objects $T]
        set wire_count [llength $wire_list]
        for {set j 0} {$j < $wire_count} {incr j} { 
            set wire [lindex $wire_list $j]
			if { $j==[expr {$wire_count-1} ]} {
				puts $f "\"$wire\""
			} else {
				puts $f "\"$wire\","
			}
        }
        puts $f "\],"
		puts $f "\"sites\":\["
		set site_list [get_sites -of_objects $T]
		set site_count [llength $site_list]
		for {set j 0} {$j < $site_count} {incr j} {
			set site [lindex $site_list $j]
			if { $j==[expr {$site_count-1} ]} {
				puts $f "\"$site\""
			} else {
				puts $f "\"$site\","
			}
		}
		puts $f "\]"
        if { $i==[expr {$tile_count-1} ]} {
            puts $f "\}"
        } else {
            puts $f "\},"
        }
        
    }
    puts $f "\}"
    close $f
}


link_design -part xc7a100tcsg324-1
tile_pips_to_json "xc7a100tcsg324_info.json"