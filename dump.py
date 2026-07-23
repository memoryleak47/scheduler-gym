#!/usr/bin/python3 -B

import os
import sys

schedulers = os.listdir("schedulers")
# schedulers = ["backoff.rs", "detour-rhs-400.rs"]

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

def cost_v(entry):
    return sum(entry["costs"])

def time_v(entry):
    return entry["time"]

def size_v(entry):
    return entry["total_size"]

arg=""
if len(sys.argv) >= 2:
    arg=sys.argv[1]

if arg == "":
    cost = cost_v
elif arg == "time":
    cost = time_v
elif arg == "size":
    cost = size_v
else:
    print("Weird arg", arg)
    assert(False)

arg2=""
if len(sys.argv) >= 3:
    arg2=sys.argv[2]

if arg2 == "":
   relevant_entries = filter_last
elif arg2 == "earliest":
   relevant_entries = filter_earliest_best
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

db = {}

for c in os.listdir("case-studies"):
    if c == "lean-egg": continue

    db[c] = {}

    l = []
    for s in schedulers:
        entries = get_entry_file(s, c)
        if entries is not None:
            db[c][s] = entries

def check_db():
    for (c, inner) in db.items():
        n = None
        n_src = None
        for (s, entries) in inner.items():
            k = len(filter_last(entries))
            if n is None:
                n = k
                n_src = s
            else:
                if n != k:
                    raise RuntimeError(f"{c}/{n_src} and {c}/{s} disagree on number of runs: {n} vs {k}")

def effective_cost(entries):
    s = 0
    for entry in relevant_entries(entries):
        s += cost(entry)
    return s

def dumpall():
    for (c, inner) in db.items():
        print()
        print("===", c)

        l = []
        for (s, entries) in inner.items():
            if entries is None: continue
            l.append((effective_cost(entries), s))
        l = sorted(l)

        for (cs, s) in l:
            print(f"{cs} <- {s}")

def compare(s1, s2):
    for (c, inner) in db.items():
        if s1 not in inner: continue
        if s2 not in inner: continue

        print()
        print("===", c)

        r1 = relevant_entries(inner[s1])
        r2 = relevant_entries(inner[s2])

        assert(len(r1) == len(r2))
        total = len(r1)

        val1 = 0
        val2 = 0
        for x1, x2 in zip(r1, r2):
            x1 = cost(x1)
            x2 = cost(x2)
            if x1 < x2: val1 += 1
            elif x1 > x2: val2 += 1
        print(f"{s1} won {val1}/{total}")
        print(f"{s2} won {val2}/{total}")

check_db()
dumpall()
compare("backoff.rs", "detour-rhs-400.rs")
