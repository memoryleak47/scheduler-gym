#!/usr/bin/env bash
# Run all benchmark configurations unattended (start inside tmux, `run` needs a tty).
cd "$(dirname "$0")"
S=backoff,random,source-cost,outcome-cost

sudo -v || exit 1                               # ask for the password once, up front
while true; do sudo -n -v; sleep 60; done &     # keep sudo's cached login alive
KEEPALIVE=$!
trap 'kill $KEEPALIVE; sudo ./benchmode.sh off' EXIT

sudo ./benchmode.sh on || exit 1

./benchmode.sh run uv run bench.py --native-limit     --schedulers $S 2>&1 | tee log-native.log
# ./benchmode.sh run uv run bench.py --node-limit 10000 --schedulers $S 2>&1 | tee log-node.log
