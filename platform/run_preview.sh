#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
export PYTHONDONTWRITEBYTECODE=1
export STATIONS_CACHE_DIR="${STATIONS_CACHE_DIR:-$PWD/cache/stations}"
export VERIFICATION_READ_ONLY=1
export VERIFICATION_PREVIEW_DATA_ROOT=/home/chengyang/verification-test
export HTTP_PROXY=http://127.0.0.1:17908 HTTPS_PROXY=http://127.0.0.1:17908
export http_proxy=http://127.0.0.1:17908 https_proxy=http://127.0.0.1:17908
export NO_PROXY=localhost,127.0.0.1,::1 no_proxy=localhost,127.0.0.1,::1
exec /home/chengyang/.venvs/ec-gfs-server/bin/python server.py --port 8524
