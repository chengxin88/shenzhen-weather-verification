#!/bin/bash
# Assemble backfilled EC/GFS frames and rescore the TEST native (0.25°) archive.
# Safe to rerun; the service picks up the new catalog within 5 minutes (no restart needed).
set -e
cd /home/chengyang/verification-test
PY=/home/chengyang/.venvs/ec-gfs-server/bin/python
$PY -u -W ignore backfill_native.py assemble
$PY -u -W ignore native_archive.py
