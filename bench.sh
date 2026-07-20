#!/bin/bash

function cost() {
    cat $1 | grep "stop=Some(" | cut -d "[" -F 2 | cut -d "]" -F 1 | awk '{sum+=$1} END {print sum}'
}

[ ! -e benchdata ] && mkdir benchdata

for scheduler in $(ls schedulers)
do
    rm -rf "benchdata/$scheduler"
    mkdir "benchdata/$scheduler"

    for c in $(ls case-studies)
    do
        if [[ "$c" == "herbie" ]]; then
            echo "Ignoring case study '$c' for now"
            continue
        fi

        echo "========================="
        echo "CASE STUDY: $c"
        sleep 0.2

        rm -f /tmp/entries.txt
        rm -f /tmp/scheduler.rs

        cp schedulers/$scheduler /tmp/scheduler.rs
        cat gym-common.rs >> /tmp/scheduler.rs

        (cd case-studies/$c; ./run.sh /tmp/scheduler.rs)

        echo "-------------------------"
        echo "case study '$c' reached a cost of:"
        cost /tmp/entries.txt | tee "benchdata/$scheduler/$c.cost"
        mv /tmp/entries.txt "benchdata/$scheduler/$c.entries"
    done
done
