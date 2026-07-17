#!/bin/bash

for scheduler in $(ls schedulers)
do
    for c in $(ls case-studies | sort -r)
    do
        echo "========================="
        echo "CASE STUDY: $c"
        sleep 0.2

        (cd case-studies/$c; ./run.sh ../../schedulers/$scheduler)
    done
done
