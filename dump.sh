#!/bin/bash

schedulers=$(ls schedulers)

# configure the set of schedulers you are interested in.
schedulers="backoff.rs detour-rhs-400.rs"

input="$1"

function cost() {
    if [ "$input" == "size" ]; then
        size_sum $*
    elif [ "$input" == "time" ]; then
        time_sum $*
    elif [ -z "$input" ]; then
        cost_sum $*
    else
        echo "Unknown input '$input'"
        exit 1
    fi
}

function cost_sum() {
    cat $1 | grep "stop=Some(" | cut -d "[" -F 2 | cut -d "]" -F 1 | awk '{sum+=$1} END {print sum}'
}

function size_sum() {
    cat $1 | grep "stop=Some(" | cut -d "=" -F 3 | cut -d "," -F 1 | awk '{sum+=$1} END {print sum}'
}

function time_sum() {
    cat $1 | grep "stop=Some(" | cut -d "=" -F 4 | cut -d "," -F 1 | awk '{sum+=$1} END {print sum}'
}

for c in $(ls case-studies | sort -r)
do
    echo "=== $c:"

    for s in $schedulers
    do
        filename="benchdata/$s/$c.entries"
        [ ! -e "$filename" ] && continue
        cst=$(cost "$filename")
        echo "$cst <- $s"
    done | sort -n

    echo
done
