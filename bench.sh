#!/usr/bin/env bash

SCHEDULERS="detour-lhs-400.rs detour-lhs-20.rs detour-lhs-100.rs detour-lhs-200.rs detour-lhs-300.rs detour-lhs-500.rs backoff.rs detour-rhs-400.rs detour1.rs ctxt-400.rs size-400.rs pat-400.rs detour-classbased-400.rs detour-rhs-400-classboost.rs detour-rhs-400-classboost-halfed.rs detour-min-400.rs backoff-fresh.rs backoff-illegal.rs backoff-legal.rs detour-classbased-1000.rs detour-classbased-500.rs detour-lhs-400-dynamic.rs detour-lhs-400-prestart.rs detour-lhs-400-shortcut-simple.rs detour-lhs-400-shortcut.rs detour-lhs-400-simple.rs detour-lhs-400-simple2.rs detour-lhs-400-simple3.rs detour-lhs-400-simple4-800.rs detour-lhs-400-simple4-nostorage.rs detour-lhs-400-simple4-shortcut.rs detour-lhs-400-simple4.rs detour-lhs-400_800-shortcut.rs detour-rhs-100.rs detour-rhs-20.rs detour-rhs-200.rs detour-rhs-300.rs detour-rhs-400-boost.rs detour-rhs-400-boost2.rs detour-rhs-400-merge.rs detour-rhs-500.rs detour-rhs.rs detour-vanilla.rs detour1-400.rs detour1-rhs.rs size-bounded.rs"
CASE_STUDIES="caviar szalinski trig integ"

cd "$(dirname "$0")"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

function bench1() {
    s="$1"
    c="$2"
    mkdir -p "benchdata/$s"
    [ -e "benchdata/$s/$c.entries" ] && return

    echo "[$c] scheduler '$s' started"

    # per-case-study files, so case studies can run in parallel
    sched="$TMP/$c.scheduler.rs"
    entries="$TMP/$c.entries.txt"
    rm -f "$entries"

    cp "schedulers/$s" "$sched"
    cat gym-common.rs >> "$sched"

    (cd "case-studies/$c"; GYM_ENTRIES="$entries" bash ./run.sh "$sched") &> "benchdata/$s/$c.log"
    if [ -e "$entries" ]; then
        mv "$entries" "benchdata/$s/$c.entries"
        echo "[$c] scheduler '$s' done"
    else
        echo "[$c] scheduler '$s' FAILED (no entries), see benchdata/$s/$c.log"
    fi
}

mkdir -p benchdata

for c in $CASE_STUDIES
do
    # schedulers run sequentially per case study, since each builds in place
    (
        for s in $SCHEDULERS
        do
            bench1 "$s" "$c"
        done
    ) &
done
wait
