#!/bin/bash

SCHEDULERS="detour-lhs-400.rs backoff.rs detour-rhs-400.rs detour1.rs"
CASE_STUDIES="lean-egg"

function bench1() {
    s="$1"
    c="$2"
    [ ! -e "benchdata/$s" ] && mkdir "benchdata/$s"
    [ -e "benchdata/$s/$c.entries" ] && return

    echo "========================="
    echo "CASE STUDY '$c' run by scheduler '$s'"
    sleep 0.2

    rm -f /tmp/entries.txt
    rm -f /tmp/scheduler.rs

    cp "schedulers/$s" /tmp/scheduler.rs
    cat gym-common.rs >> /tmp/scheduler.rs

    (cd case-studies/$c; ./run.sh /tmp/scheduler.rs) |& tee "benchdata/$s/$c.log"
    mv /tmp/entries.txt "benchdata/$s/$c.entries"
}


[ ! -e benchdata ] && mkdir benchdata

for s in $SCHEDULERS
do
    for c in $CASE_STUDIES
    do
        bench1 "$s" "$c"
    done
done
