#!/usr/bin/python3 -B

import os
import sys

def filter_last(entries):
    return [e for e in entries if "Some(" in e["stop"]]

def filter_earliest_best(entries):
    out = []
    best_entry = None
    for e in entries:
        if best_entry is None or tuple(e["costs"]) != tuple(best_entry["costs"]):
            best_entry = e

        if "Some" in e["stop"]:
            out.append(best_entry)
            best_entry = None

    return out

def cost_sum(entries):
    s = 0
    for e in relevant_entries(entries):
        s += sum(e["costs"])
    return s

def time_sum(entries):
    s = 0
    for e in relevant_entries(entries):
        s += e["time"]
    return s

def size_sum(entries):
    s = 0
    for e in relevant_entries(entries):
        s += e["total_size"]
    return s

arg=""
if len(sys.argv) >= 2:
    arg=sys.argv[1]

if arg == "":
    cost = cost_sum
elif arg == "time":
    cost = time_sum
elif arg == "size":
    cost = size_sum
else:
    print("Weird arg", arg)
    assert(False)

arg2=""
if len(sys.argv) >= 3:
    arg2=sys.argv[2]

if arg2 == "":
   relevant_entries = filter_earliest_best
elif arg2 == "last":
   relevant_entries = filter_last
else:
    print("Weird arg2", arg2)
    assert(False)

def read(x):
    try:
        with open(x, "r") as file:
            content = file.read()
        return content
    except FileNotFoundError:
        return None

def parse_num(s):
    try:
        return int(s)
    except ValueError:
        return float(s)

# returns list of entries, or None
def get_entry_file(s, c):
    filename = f"benchdata/{s}/{c}.entries"
    data = read(filename)
    if data is None: return None
    entries = []
    for line in data.splitlines():
        entries.append(parse_entry(line))
    return entries

# example:
#ENTRY: costs=[451021565], total_size=278, time=0.000389253, iteration=1, stop=None
def parse_entry(entry):
    costs = [parse_num(x) for x in entry.split("costs=[")[1].split("]")[0].split(", ")]
    total_size = parse_num(entry.split("total_size=")[1].split(",")[0])
    time = parse_num(entry.split("time=")[1].split(",")[0])
    iteration = parse_num(entry.split("iteration=")[1].split(",")[0])
    stop = entry.split("stop=")[1].strip()

    return {
        "costs": costs,
        "total_size": total_size,
        "time": time,
        "iteration": iteration,
        "stop": stop,
    }

for c in os.listdir("case-studies"):
    # lean-egg is weird
    if c == "lean-egg": continue
    print()
    print("===", c)

    l = []
    for s in os.listdir("schedulers"):
        entries = get_entry_file(s, c)
        if entries is None: continue

        l.append((cost(entries), s))
    l = sorted(l)

    for (cs, s) in l:
        print(f"{cs} <- {s}")

