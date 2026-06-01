#!/bin/bash

for scheduler in $(ls schedulers)
do
    for c in $(ls case-studies)
    do
        (cd case-studies/$c; ./run.sh ../../schedulers/$scheduler)
    done
done
