#!/bin/bash

for c in $(ls case-studies | sort -r)
do
    echo "=== $c:"

    for s in $(ls schedulers)
    do
        filename="benchdata/$s/$c.cost"
        [ ! -e "$filename" ] && continue
        score=$(cat "$filename")
        s2=$(printf "%-13s\n" "$s")
        echo "$s2 = $score"
    done

    echo
done
