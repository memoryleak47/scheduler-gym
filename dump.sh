#!/bin/bash

for c in $(ls case-studies | sort -r)
do
    for s in $(ls schedulers)
    do
        filename="benchdata/$s/$c.cost"
        [ ! -e "$filename" ] && continue
        score=$(cat "$filename")
        echo "$s/$c = $score"
    done

    echo
done
