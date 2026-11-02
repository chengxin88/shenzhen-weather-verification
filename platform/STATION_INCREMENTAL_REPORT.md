# Station incrementality report

Worktree: `verification-test-stations`, branch/base `station-incremental` / `598365a`.
All execution is offline with `--score-only`, the supplied Python, `nice -n 10 ionice -c3`, and `STATIONS_CACHE_DIR=$PWD/cache/stations-dev`. Production files, cache, port 8513, and systemd units were not changed. `cache/stations-dev.orig` is the pristine `cp -a` backup and remains on disk.

## Measured baseline

The preserved main code is in `cache/incremental-measurements/baseline-code`. Instrumentation changes only timing and ROOT resolution for the copied module location. The scoring endpoint is fixed at **2026-10-27T03:00:00Z**. `baseline-A.log`, `baseline-timing.json`, and `cache/stations-dev-baseline-A` contain the full run and result.

| Phase | Baseline seconds |
| --- | ---: |
| Fetch (`--score-only`) | 0 |
| Observations | 5.511 |
| Discovery | 11.097 |
| Forecast checks/sampling, excluding discovery | 2221.403 |
| Pair generator, excluding SQLite writes | 15.636 |
| Window aggregates | 735.762 |
| Grid rows/map | 35.585 |
| Common report | 1.107 |
| Shenzhen sanity | 155.573 |
| Daily CSV exports | 31.585 |
| SQLite backup | 3.824 |
| SQL writes, coverage, bookkeeping and recording residual | 63.116 |
| Total | **3280.20 (54.67 min)** |

Peak RSS: **453.0 MiB** (GNU time: 463880 KiB). Baseline: 129 runs rescored, 855 skipped, 518430 inserted pairs, 5740355 total pairs. The copied-cache run was faster than the reported 1.5–2 h production runs; these are measured results, not an estimate of a cold production archive.

Baseline per-model source checks/sampling were: aifs 83.411 s, aigfs 162.989 s, ec01 83.492 s, ecmwf 363.808 s, gefs 1.082 s, gfs 356.604 s, wn3 1170.019 s. Pair-generator times were respectively 0.148, 0.258, 7.702, 0.435, 0, 2.794, 4.299 s. The original full-run measurement did not separately attribute SQLite writes to models; a frozen-input scoring profile supplies those timings separately below.

## Implementation

- `job_state` records scoring VERSION and forecast identity; `job_valid` records the exact observation digest, including absence, for each model/run/valid. Unchanged sources re-pair only changed valid times. New runs, changed sources and VERSION changes pair the whole run. The original jobs digest can prove an unchanged legacy run and seed the new tables without pairing it.
- Forecast identities no longer include the sliding active-lead list. Descriptors check sources before exposing station arrays. A changed valid time loads only needed leads plus up to 24 preceding hours for the existing exact rain tiling. Cached per-lead source identities permit reuse of unaffected station samples.
- Existing native fields are always stat'ed, detecting revisions independent of JSON metadata. Negative field lookups can be reused only with matching directory identity/mtime/ctime, after a two-second timestamp-resolution guard. Recently edited directories are checked directly.
- Aggregation uses six shared grouping scans per day window rather than 90. Rain counters pool as exact integers. Continuous pooled scores retain the original raw SUM order, avoiding cancellation changes in low-variance pressure CC. Detail scores stream to SQLite; the n>1 legacy CC rule and n>=3 grid rule are both retained. Grid's two bounded scans remain.
- Shenzhen sanity now caches the grid-cell lookup and per-file grid values by source signature, while always recomputing scores against current station observations. This removes the measured 155.6 s full reread on an unchanged tick.
- Full SQLite backup remains: the measured baseline cost was only 3.8 s. Working files are never hard-linked into immutable snapshots.

### Precision migration

The original sample writer saved float32 after scoring fresh float64 values. Historical pairs and their NPZ cache can therefore disagree in low bits with unchanged source data (direct samples showed differences around 1–2e-6 for temperature). Blindly forcing the old pairer over rounded cached inputs does not reproduce its own published historical fc values.

New NPZ writes use float64. On a proven unchanged source, a one-time migration restores published forecast values into the cache only when they round to exactly the same float32 value. It never changes a pair row. Larger discrepancies fail explicitly. A `pair_precision` marker prevents repeating the array load on later unchanged ticks. This format migration is the one-time exception to loading arrays only for runs needing pairing.

Legacy native rain intervals need a separate reconstruction because published rows store 6/24-hour totals rather than individual intervals. `station_precision.py` admits original metadata values, or the legacy regional JSON's three-decimal values, only when every interval has the same float32 encoding. It solves the interval choices against all existing 6/24-hour forecast totals and requires exact reproduction of every total before saving. It leaves already exact intervals untouched, never uses observations to select values, and raises on an unexplainable discrepancy. The `pair_precision=2` certificate completes this migration; its integration test also verifies repeat calls leave the NPZ unchanged.

The sampling certificate and arrays are atomically published in one NPZ; the certificate is stored in its ZIP comment, which old NumPy readers ignore. Compatible native certificate sidecars are maintained. The full-reference test uses the resulting certified station caches as frozen inputs to the unchanged main pairer; every pair is rebuilt independently, with fc/ob compared exactly. This tests pairing and products against identical station inputs; it does not independently validate interpolation against original forecast fields.

## Crash safety and publication

Pair deletion/insertion, job digests and `dirty_days` commit in one SQLite transaction per run. A killed transaction rolls back. A kill after committed pairing but before publication leaves the old snapshot current and the pending export days durable for the next run.

All products are built in `.incomplete-<generation>`. SQLite writers close and files/directories are fsynced before the generation directory is renamed and `current.json` is atomically replaced and fsynced. Export files are also synced before pending days are cleared. The complete current generation and one other newest complete generation are retained; incomplete directories do not count, and current is protected even following rollback to an older generation. Reports are also stored within the generation.

The actual interrupted B3 from the preceding session was checked before restoring anything. Both `cache/stations-dev-B2/current.json` and the interrupted cache pointed to **`20261027T050636656244`**, the verified B2 generation. `tools/compare_station_runs.py cache/stations-dev-B2 cache/stations-dev` passed: 5,742,260 exact pairs, 748,700 detail rows, 868,967 grid-map daily rows and all other compared products unchanged; zero mismatches and maximum relative error 0.0. A SHA-256 manifest additionally confirms every file in the current snapshot is byte-identical to B2, and the pointer bytes are identical too. Thus committed work from the killed tick did not leak into published reader products. Evidence: `crash-safety-B2-killed-B3.json` / `.log` and `crash-safety-manifest.json`.

The rerun preserves the interrupted cache as `cache/stations-dev-killed-B3-finish2` and restores `cache/stations-dev` with `cp -a` from verified B2 after checking free disk space (818 GB). It reapplies the offline B3 fixture and uses `setsid`/`nohup`, nice/ionice and `/usr/bin/time -v` for B3 and the unchanged-end repeat. A waiting launcher keeps the sandbox session alive; the initial detached-only launcher was reaped before it started a tick. These launchers and measurement logs are excluded from the implementation commit.

## Timing and equivalence results

The fixed-input equivalence checks both passed:

| Comparison | Exact pair rows | Detail rows | Grid-map daily rows | Mismatches | Maximum relative error |
| --- | ---: | ---: | ---: | ---: | ---: |
| A versus B1, frozen identical inputs | 5,740,355 | 748,691 | 868,822 | **0** | **0.0** |
| Full baseline versus consecutive B2, repaired precision | 5,742,260 | 748,700 | 868,967 | **0** | **0.0** |

Both comparisons also cover all four score/station-score windows, all temperature/rain grid rows, datasets and common reports. Evidence: `equivalence-A-B1-frozen.json` and `equivalence-reference-B2-repaired.json` in `cache/incremental-measurements/`. No comparison tolerances were relaxed. The repaired B2 full baseline independently rebuilt all 988 runs / 5,742,260 pairs in 1,135.17 s (18.92 min), with peak RSS 262.6 MiB; its inputs were frozen station arrays rather than live archive reads.

| Run | Worker elapsed | GNU time wall | Peak RSS | Scope |
| --- | ---: | ---: | ---: | --- |
| Main baseline A | 3,280.20 s (54.67 min) | 54:41.46 | 453.0 MiB | Live readers; 129 full runs |
| Candidate migration B1, live inputs | 2,172.32 s (36.21 min) | 36:12.80 | 446.7 MiB | 130 full runs; source drift described below |
| Candidate B1, frozen inputs | 328.51 s (5.48 min) | 5:28.81 | 260.2 MiB | Correctness replay; excludes archive discovery/sampling |
| Consecutive B2, including precision migration | 1,703.31 s (28.39 min) | 28:23.85 | 443.6 MiB | 4 full + 118 partial; 27,283 inserted pairs |
| B3, end advanced to 06:00Z | 1,639.67 s (27.33 min) | **27:20.26** | **487.9 MiB** (499,644 KiB) | 9 full + 112 partial; 19,091 inserted pairs |
| First same-end repeat, source changes | 1,440.60 s (24.01 min) | **24:00.90** | **476.1 MiB** (487,544 KiB) | 4 full runs; 551 inserted pairs; not a no-op |

**The approximately 20-minute target was not met by B3.** It completed successfully with no refresh errors and remained below the memory limit, but source identity checks alone took 1,224.565 s (20.41 min). The scoring/pairing phase took another 245.731 s, including on-demand station sampling. Worker startup/database setup added about 19.7 s outside the phase totals, including a covering-index migration in the restored B2 database. Excluding that setup still leaves a 27.00-minute scoring/publishing run, so it does not explain the target miss. The comparison with the measured main baseline is about 2x overall; the requested 15–20-minute steady-state result is not demonstrated.

B3 ran from 15:27:57 to 15:55:17 CST with `--score-only --end 2026-10-27T06:00:00Z`, after reapplying three temperature revisions and nine appended reports. It finished with 872 unchanged runs, 442 changed valid times and 5,744,053 total pairs in generation `20261027T075256404003`. The one forecast issue, rejected WN3 manifest `run_2026100905.json` (`WN3非已验证区域原生缓存`), is identical to B2's existing warning. The refresh status was `complete`, with an empty errors list and exit status 0. The same-end repeat started immediately afterward; its final measurement is recorded below when complete. B1/B2 timings include one-time migrations and are not steady-state target claims.

| Phase | B3 seconds |
| --- | ---: |
| Fetch (`--score-only`) | 0 |
| Observations | 5.574 |
| Roll-off | 0.833 |
| Discovery | 2.499 |
| Source checks, all models | 1,224.565 |
| Sampling/pairing/SQL, all models | 245.731 |
| Window aggregates | 74.503 |
| Grid rows/map | 35.221 |
| Common report | 0.997 |
| Shenzhen sanity | 5.979 |
| Daily CSV exports | 14.585 |
| Coverage | 3.107 |
| SQLite backup | 5.900 |
| Publication | 0.030 |

B3 source-check / score seconds by model: aifs 135.988 / 45.355; aigfs 127.614 / 52.955; ecmwf 248.465 / 28.748; gefs 0.927 / 0.032; gfs 179.634 / 17.756; ec01 13.199 / 1.980; wn3 518.738 / 98.905. Evidence: `candidate-B3.log`, `candidate-B3-refresh-status.json`, `candidate-B3-score-status.json`.

The first same-end repeat ran from 15:55:17 to 16:19:18 CST. Despite unchanged raw observations and end time, it processed four changed/new forecast runs, with 27 changed valid times, 551 inserted pairs and 5,744,461 total pairs. It therefore cannot be presented as a true no-op. Source checks took 1,244.814 s, sampling/pairing/SQL 22.694 s and aggregates including grid 114.456 s. Evidence: `candidate-B3-noop.log` and its saved status files. A serialized follow-up started at 16:19:47 CST, keeps the same command/end and records a no-op only when processed runs, full/partial runs, inserted pairs and changed valid times are all zero. Its launcher and result files are retained under `cache/incremental-measurements/` and excluded from the commit.

Scheduling caveat: the B3/first-repeat supervisor and its child command both applied `nice -n 10`; the additive adjustments produced effective niceness 19. The follow-up uses a single adjustment (niceness 10). All timed workers use `ionice -c3`. B3 is therefore a more deprioritized measurement than requested, not a precisely matched scheduler comparison with baseline A; the approximately 2x speedup and target miss describe these measured runs, not a prediction for every production tick.

The unrestricted candidate migration run took **2172.32 s (36.21 min)**, peak **446.7 MiB**. It scored 130 runs and skipped 856, inserting 518546 pairs. Forecast sources continued arriving during the long experiments: this run contained 116 additional pairs from WN3 run `2026-10-26T20:00:00Z`. A streaming merge confirms **5740355 matched pairs, zero modified and zero removed**, with exactly those 116 additions. This is input drift, so it is not used as an equivalence pass.

The raw original station-input recordings remain in `forecast-input-A-recorded`. The controlled B1 run started from the pristine cache with those exact inputs, controlling both source arrival and old cache rounding. The consecutive B2 real-reader tick used three revised raw IEM temperatures and nine appended hourly reports, then the unchanged main scorer fully rebuilt pairs from its frozen certified station inputs. `tools/freeze_station_cache.py` records those inputs without copying result rows into the reference.

`tools/compare_station_runs.py` compares every pair column exactly, detail scores, scores/station-scores for all four windows, decompressed grid rows, all grid-map rows, datasets and common report. Derived floats use relative 1e-9 / absolute 1e-12. Only generation timestamps and gzip byte sizes are ignored. SQLite rows are merged by key so added rows do not cause cascaded false differences.

## Tests

Final station/scoring/grid suite: **52 tests passed**, including HTTP tests, in 6.073 s:

```bash
STATIONS_CACHE_DIR="$PWD/cache/stations-dev" PYTHONDONTWRITEBYTECODE=1 \
  nice -n 10 ionice -c3 /home/chengyang/.venvs/ec-gfs-server/bin/python \
  -m unittest test_stations test_station_incremental test_station_grid
NODE_PATH=/tmp/st-jsdom/node_modules \
  node --test test_stations_frontend.js test_grid_stations_frontend.js
```

Both frontend files passed (2/2, 3.666 s). Final logs: `finish2-python-tests.log`, `finish2-frontend-tests.log`.

An earlier full discovery run recorded 93 tests and 14 environment errors: six HTTP cases then blocked by sandbox socket creation and eight known missing demo-cache cases in `test_module_demo` / `test_wn3_demo`. The final requested station suite now passes its HTTP tests too; unrelated demo-cache tests were not rerun.

Tests include new/revised/removed observations, new run, VERSION bump, roll-off, advancing end, unchanged legacy migration, selective lead loading with rain dependencies, transactional insertion failure, actual SIGKILL with SQLite integrity check, interrupted publishing and retry of pending exports, current protection/incomplete pruning, source replacement/addition including coarse timestamps, lossless NPZ/certificate recovery, backward-readable ZIP contents, precision migration and repeatability, aggregate/window equivalence, and sanity-cache source revision handling.

## Deployment (instructions only; not executed)

1. In a writable checkout, review and commit the explicit implementation file list below on `station-incremental`, based on `598365a`. Wait for an inactive station worker before applying the reviewed commit to the then-current production branch, resolving any later main changes normally. Deploy the code and docs; never copy the synthetic development cache or its raw IEM fixture into production.
2. Wait until the existing station worker is inactive. Keep the current immutable snapshots and make a backup before the first new-code tick. From `/home/chengyang/verification-test`, run `flock -n cache/stations/refresh.lock cp -a cache/stations cache/stations.pre-incremental-YYYYMMDD-HHMMSS`, replacing the timestamp with a new, unused backup name. A failed lock acquisition means wait and retry; the existing refresh flock remains the serialization mechanism.
3. Run the station unit/frontend commands above in the normal deployment environment with a private test cache. The missing demo caches are independent of this work.
4. From `/home/chengyang/verification-test`, run `STATIONS_CACHE_DIR="$PWD/cache/stations" nice -n 10 ionice -c3 /usr/bin/time -v /home/chengyang/.venvs/ec-gfs-server/bin/python refresh_stations.py --score-only`. Allow the one-time metadata/precision migration to finish. If the flock reports a timer-owned run, wait for that run rather than starting another worker.
5. Inspect `refresh-status.json`, `score-status.json`, `current.json`, generation files, per-phase timings and RSS. Verify at least two normal three-hour ticks, including actual new forecast runs and observations. The existing timer invokes the new code on its next execution; no unit, port or service restart is needed.
6. Keep the current TimeoutStartSec (10200 s) for rollout and rare full-source rescoring. Consider lowering it only after normal production measurements. No unit change is included here.

For code rollback, wait for a completed worker and restore the reviewed prior code; NPZ ZIP comments do not break old readers. After an interrupted format migration, let the new worker recover/repair its certificate sidecars before a code-only rollback, or restore the pre-deployment cache backup. Reapplying after old-code writes should include a fresh version audit rather than trusting stale new-only job metadata.

## Risks and review artifacts

Source identity checks still traverse the archive drive; performance depends on drive latency, competing WSL I/O and the number of changed forecast sources. A genuine source or VERSION change intentionally remains a full-run operation. An unchanged tick still checks source identities and rebuilds aggregate/grid products and the immutable snapshot; only pairing and daily exports can be skipped. The first migration is slower than a steady tick. Memory is bounded through small SQLite caches, file-backed temporary sorting, streaming detail output, and a bounded sample worker pool.

Git staging was attempted and failed with `Unable to create .../index.lock: Read-only file system`. Following the second handoff, the tree is left uncommitted for Claude; no merge or push was attempted. The exact implementation file list is:

```text
refresh_stations.py
station_common.py
station_forecasts.py
station_score.py
station_precision.py
test_station_incremental.py
tools/benchmark_station_run.py
tools/compare_station_runs.py
tools/freeze_station_cache.py
STATIONS.md
STATION_INCREMENTAL_REPORT.md
```

Exclude all `cache/`, logs, `TASK_*`, and `run_*` files from the commit. The pristine backup, verified B2, baseline references, final development cache and interrupted B3 cache remain available for review. All measurement artifacts are under `cache/incremental-measurements/`.

In this worktree once its Git metadata is writable, the exact commit commands are:

```bash
git switch station-incremental
git add refresh_stations.py station_common.py station_forecasts.py station_score.py \
  station_precision.py test_station_incremental.py tools/benchmark_station_run.py \
  tools/compare_station_runs.py tools/freeze_station_cache.py \
  STATIONS.md STATION_INCREMENTAL_REPORT.md
git diff --cached --check
git commit -m "Make station scoring incremental by valid time"
```
