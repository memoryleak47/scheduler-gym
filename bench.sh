#!/bin/bash

function cost() {
    cat $1 | grep "stop=Some(" | cut -d "[" -F 2 | cut -d "]" -F 1 | awk '{sum+=$1} END {print sum}'
}

IGNORED_SCHEDULERS=""
IGNORED_CASE_STUDIES="herbie"

[ ! -e benchdata ] && mkdir benchdata

for s in $(ls schedulers)
do
    if [[ "$IGNORED_SCHEDULERS" =~ "$s" ]]; then
        echo "Ignoring scheduler '$s' for now"
        continue
    fi

    [ ! -e "benchdata/$s" ] && mkdir "benchdata/$s"

    for c in $(ls case-studies | sort -r)
    do
        if [[ "$IGNORED_CASE_STUDIES" =~ "$c" ]]; then
            echo "Ignoring case study '$c' for now"
            continue
        fi

        [ -e "benchdata/$s/$c.entries" ] && continue

        echo "========================="
        echo "CASE STUDY '$c' run by scheduler '$s'"
        sleep 0.2

        rm -f /tmp/entries.txt
        rm -f /tmp/scheduler.rs

        cp "schedulers/$s" /tmp/scheduler.rs
        cat gym-common.rs >> /tmp/scheduler.rs

        (cd case-studies/$c; ./run.sh /tmp/scheduler.rs)

        echo "-------------------------"
        echo "case study '$c' run by scheduler '$s' reached a cost of:"
        cost /tmp/entries.txt | tee "benchdata/$s/$c.cost"
        mv /tmp/entries.txt "benchdata/$s/$c.entries"
    done
done
