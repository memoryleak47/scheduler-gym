#!/bin/bash

for c in $(ls case-studies | sort -r)
do
    echo "=== $c:"

    for s in $(ls schedulers)
    do
        filename="benchdata/$s/$c.cost"
        [ ! -e "$filename" ] && continue
        score=$(cat "$filename")
        echo "$score <- $s"
    done | sort -n

    echo
done
