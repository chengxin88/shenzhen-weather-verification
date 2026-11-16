#!/bin/bash
# Assemble backfilled EC/GFS frames and rescore the TEST native (0.25°) archive.
# Safe to rerun; the service picks up the new catalog within 5 minutes (no restart needed).
set -e
cd /home/yjzx/verification-platform/verification-test
PY=/home/yjzx/verification-platform/venv/bin/python
$PY -u -W ignore backfill_native.py assemble
$PY -u -W ignore native_archive.py
