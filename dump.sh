#!/bin/bash

function cost() {
    cat $1 | grep "stop=Some(" | cut -d "[" -F 2 | cut -d "]" -F 1 | awk '{sum+=$1} END {print sum}'
}

for c in $(ls case-studies | sort -r)
do
    echo "=== $c:"

    for s in $(ls schedulers)
    do
        filename="benchdata/$s/$c.entries"
        [ ! -e "$filename" ] && continue
        cst=$(cost "$filename")
        echo "$cst <- $s"
    done | sort -n

    echo
done
