#!/bin/bash

IGNORED_SCHEDULERS="backoff-fresh.rs backoff-legal.rs detour-rhs.rs size-bounded.rs backoff-illegal.rs detour-rhs-20.rs detour-rhs-400-merge.rs detour-vanilla.rs detour-lhs-400-simple2.rs detour-lhs-400-simple4-800.rs detour-lhs-400-shortcut-simple.rs detour-lhs-400-simple4.rs detour-lhs-400-simple4-shortcut.rs detour-lhs-400-simple.rs detour-lhs-400-simple4-nostorage.rs detour-lhs-400-simple3.rs"
PRIO_SCHEDULERS="backoff.rs detour-lhs-400.rs detour1.rs detour-rhs-400.rs"

IGNORED_CASE_STUDIES="herbie lean-egg trig integ szalinski"
PRIO_CASE_STUDIES="caviar"

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

    (cd case-studies/$c; ./run.sh /tmp/scheduler.rs)
    mv /tmp/entries.txt "benchdata/$s/$c.entries"
}


[ ! -e benchdata ] && mkdir benchdata


# prio runs
for s in $PRIO_SCHEDULERS
do
    for c in $PRIO_CASE_STUDIES
    do
        bench1 "$s" "$c"
    done
done

# semi-prio and non-prio runs
for s in $PRIO_SCHEDULERS $(ls schedulers)
do
    if [[ "$IGNORED_SCHEDULERS" =~ "$s" ]]; then
        echo "Ignoring scheduler '$s' for now"
        continue
    fi

    for c in $PRIO_CASE_STUDIES $(ls case-studies | sort -r)
    do
        if [[ "$IGNORED_CASE_STUDIES" =~ "$c" ]]; then
            echo "Ignoring case study '$c' for now"
            continue
        fi

        bench1 "$s" "$c"

    done
done
