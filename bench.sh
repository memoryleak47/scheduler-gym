#!/bin/bash

function score() {
    cat $1 | grep "stop=Some(" | cut -d "[" -F 2 | cut -d "]" -F 1 | awk '{sum+=$1} END {print sum}'
}

[ ! -e benchdata ] && mkdir benchdata

for scheduler in $(ls schedulers)
do
    rm -rf "benchdata/$scheduler"
    mkdir "benchdata/$scheduler"

    for c in $(ls case-studies)
    do
        if [[ "$c" == "herbie" || "$c" == "lean-egg" ]]; then
            echo "Ignoring case study '$c' for now"
            continue
        fi

        echo "========================="
        echo "CASE STUDY: $c"
        sleep 0.2

        rm -f case-studies/$c/entries.txt
        rm -f scheduler.rs

        cp schedulers/$scheduler scheduler.rs
        cat gym-common.rs >> scheduler.rs

        (cd case-studies/$c; ./run.sh ../../scheduler.rs)

        echo "-------------------------"
        echo "case study '$c' reached a score of:"
        score case-studies/$c/entries.txt | tee "benchdata/$scheduler/$c.score"
        mv case-studies/$c/entries.txt "benchdata/$scheduler/$c.entries"
    done
done
