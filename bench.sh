#!/bin/bash

for scheduler in $(ls schedulers)
do
    for c in $(ls case-studies | sort -r)
    do
        (cd case-studies/$c; ./run.sh ../../schedulers/$scheduler)
    done
done
