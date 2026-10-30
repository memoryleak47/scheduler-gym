#!/usr/bin/env bash

# keep in sync with gym.py
SCHEDULERS="backoff detour-lhs-400 detour-lhs-20 detour-lhs-100 detour-lhs-200 detour-lhs-300 detour-lhs-500 detour-rhs-400 detour-min-400 detour1 ctxt-400 size-400 pat-400 detour-classbased-400 detour-rhs-400-classboost detour-rhs-400-classboost-halfed backoff-fresh backoff-illegal backoff-legal detour-classbased-1000 detour-classbased-500 detour-lhs-400-dynamic detour-lhs-400-prestart detour-lhs-400-shortcut-simple detour-lhs-400-shortcut detour-lhs-400-simple detour-lhs-400-simple2 detour-lhs-400-simple3 detour-lhs-400-simple4-800 detour-lhs-400-simple4-nostorage detour-lhs-400-simple4-shortcut detour-lhs-400-simple4 detour-lhs-400_800-shortcut detour-rhs-100 detour-rhs-20 detour-rhs-200 detour-rhs-300 detour-rhs-400-boost detour-rhs-400-boost2 detour-rhs-400-merge detour-rhs-500 detour-rhs detour-vanilla detour1-400 detour1-rhs size-bounded"
CASE_STUDIES="caviar szalinski trig integ"

cd "$(dirname "$0")"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

function bench1() {
    s="$1"
    c="$2"
    mkdir -p "benchdata/$s.rs"
    [ -e "benchdata/$s.rs/$c.entries" ] && return

    echo "[$c] scheduler '$s' started"

    # per-case-study files, so case studies can run in parallel
    sched="$TMP/$c.scheduler.rs"
    entries="$TMP/$c.entries.txt"
    rm -f "$entries"

    cp "schedulers/$s.rs" "$sched"
    cat gym-common.rs >> "$sched"

    (cd "case-studies/$c"; GYM_ENTRIES="$entries" bash ./run.sh "$sched") &> "benchdata/$s.rs/$c.log"
    if [ -e "$entries" ]; then
        mv "$entries" "benchdata/$s.rs/$c.entries"
        echo "[$c] scheduler '$s' done"
    else
        echo "[$c] scheduler '$s' FAILED (no entries), see benchdata/$s.rs/$c.log"
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
