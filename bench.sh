#!/bin/bash

for scheduler in $(ls schedulers)
do
    for c in $(ls case-studies)
    do
        (cd $c; ./run.sh $scheduler)
    done
done
