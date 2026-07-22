#!/bin/bash

for c in $(ls case-studies)
do
    [[ ! -e "benchdata/$1/$c.entries" ]] && continue
    [[ ! -e "benchdata/$2/$c.entries" ]] && continue

    echo
    echo "===$c"
    cat "benchdata/$1/$c.entries" | grep "stop=Some(" | cut -d "[" -F 2 | cut -d "]" -F 1 > /tmp/tmp1.txt
    cat "benchdata/$2/$c.entries" | grep "stop=Some(" | cut -d "[" -F 2 | cut -d "]" -F 1 > /tmp/tmp2.txt
    paste /tmp/tmp1.txt /tmp/tmp2.txt | awk '{
            l = $1; r = $2;
            if (l < r) left_less += 1;
            else if (l > r) right_less += 1;
        } END {
            print "Total left is smaller by:", left_less;
            print "Total right is smaller by:", right_less;
        }'
done
