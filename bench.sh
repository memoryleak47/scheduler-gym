#!/bin/bash

IGNORED_SCHEDULERS="backoff-legal.rs detour1-400.rs detour1.rs detour-rhs-200.rs detour-rhs-300.rs detour-rhs.rs size-bounded.rs backoff-illegal.rs detour1-rhs.rs detour-rhs-100.rs detour-rhs-20.rs detour-rhs-400-merge.rs detour-rhs-500.rs detour-vanilla.rs"
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
        mv /tmp/entries.txt "benchdata/$s/$c.entries"
    done
done
