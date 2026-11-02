# Station/grid chart unification delivery

The rain and temperature station reference now uses the normal grid controls, reducers, views, table, and SVG/CSV exports. Station rows are loaded only after selecting the station reference and join only the browser catalog. The standalone `#stations` page retains its original functionality.

Preview: http://127.0.0.1:8524/Verification/test/#grid&ref=station

## Changed files

- `station_score.py`: offline spatial rows and indexed BJT-day station statistics; `--grid-only`; normal timer scoring calls the same aggregation.
- `station_common.py`: model/dataset contracts, BJT conversion, sufficient-statistic formulas.
- `station_api.py`, `server.py`: read-only datasets, streaming gzip/identity rows, arbitrary-date/lead map statistics, and station series.
- `app.js`, `grid-stations.js`, `index.html`, `modules.js`, `stations.css`: reference selection within the normal grid interface, clamped custom dates, 6/24-hour rain, station supplements, titles and exports.
- `stations.js`: shared stateless SVG map and series renderers; standalone UI preserved.
- `test_station_grid.py`, `test_grid_stations_frontend.js`, `test_stations_frontend.js`: synthetic numeric, BJT/model mapping, subset, immutable-read, HTTP compression, and shared UI integration checks.
- `STATIONS.md`, this report: operation and validation notes. `core.js` remains the common scoring implementation without changes.

## Copied snapshot and extra timer work

Copied generation `20261026T072423465236` from the production station cache into this worktree (4.03 s). Only the new aggregation ran on the copy, using:

```sh
nice -n 10 ionice -c3 /home/chengyang/.venvs/ec-gfs-server/bin/python station_score.py --grid-only
```

No production file, station cache, service, timer, or port 8513 was written/restarted. No data downloads, new pairing, or legacy four-window rebuild were run for the preview.

| Variable | Rows | gzip bytes |
| --- | ---: | ---: |
| t | 40,600 | 1,974,300 |
| rain6 | 127,506 | 712,280 |
| rain24 | 81,455 | 458,871 |

The JSON payloads before gzip are 12,841,365 / 28,719,807 / 18,380,640 bytes respectively. Map statistics contain 840,620 rows in 101,908,480 bytes (97.2 MiB).

New aggregation elapsed **37.477 s** under nice/idle I/O. This is the measured additional work per three-hour timer run for this snapshot, beyond the existing roughly ten-minute aggregate step. There are two SQL grouping scans, one per output shape; thresholds share scans, and Python never materializes all station pairs.

Full-retention/all-169-lead map requests measured 20–63 ms in-process; HTTP measured 24.68 / 28.21 / 64.62 ms for rain24 / rain6 / temperature (51 / 47 / 60 stations). Map connections are immutable/read-only with a 2 MiB SQLite cache. Station row HTTP responses stream existing gzip or bounded decompression without caching decoded rows.

Preview is running via `setsid nohup ./run_preview.sh`; log `/tmp/preview-8524.log`. After the live preview checks, process RSS was **31652 kB** (30.9 MiB), peak **97324 kB** (95.0 MiB). The peak includes serving the existing 71 MiB grid catalog; steady RSS remains small. `/tmp/preview-8524-memory.json` records current measurements.

## Validation

Used the project's scientific virtualenv. Since pytest is only installed in the system interpreter, the full run appends `/usr/lib/python3/dist-packages` to the virtualenv's import path before invoking `pytest.main(['-q'])`; all scientific dependencies resolve from the project virtualenv.

Full Python output tail (`/tmp/station-grid-pytest-final.log`):

```text
FAILED test_module_demo.py::Metadata::test_month_integrity_and_private_provenance
FAILED test_module_demo.py::Metadata::test_month_scientific_counts_from_same_run_hourly_fields
FAILED test_module_demo.py::Metadata::test_month_time_identity_and_missing_days
FAILED test_module_demo.py::HTTP::test_routes_and_static_assets
FAILED test_wn3_demo.py::RealSample::test_all_grid_interfaces_preserve_wn3_identity_and_original_values
FAILED test_wn3_demo.py::RealSample::test_corrupt_sample_rejected_and_missing_sample_graceful
FAILED test_wn3_demo.py::RealSample::test_no_fabricated_minute_fields_different_batch_or_window
FAILED test_wn3_demo.py::RealSample::test_real_source_and_same_run_trailing_rain_sum
FAILED test_wn3_demo.py::RealSample::test_scores_match_real_paired_arrays
FAILED test_wn3_demo.py::HTTPDemo::test_all_demo_routes_and_formal_routes_remain_pending
10 failed, 92 passed in 5.73s
```

All ten failures are the two known unrelated demo suites with absent `cache/wn3-module-demo` / `cache/model-interface-demo` fixtures. No fixes or demo data generation were attempted. Excluding those two suites: `91 passed in 4.70s` (`/tmp/station-grid-supported-tests.log`). An initial system-interpreter run also lacked Shapely; the final virtualenv run above resolves that environment issue.

Frontend output tail (`NODE_PATH=/tmp/st-jsdom/node_modules node test_stations_frontend.js`):

```text
PASS shared grid temperature/rain 6h/24h charts, every view, lazy loading, BJT custom bounds, lead groups, reference/hash switches, map keyboard click, station series, SVG/CSV exports
PASS standalone station tab, default EC comparison, map/keyboard/elevation, detail, rain thresholds, filters, late-response isolation, read-only refresh, empty state
```

`NODE_PATH=/tmp/st-jsdom/node_modules node test_demo_core.js`:

```text
Demo selectors, exact source reuse, no high-altitude fabrication, formal isolation and export flags: passed.
```

Live HTTP + actual snapshot DOM check (`/tmp/station-grid-live-dom.log`):

```text
LIVE EXPORT ["检验_站点实况_降水_multi.svg","检验_站点实况_降水_multi_2026-10-26.csv"]
LIVE ERRORS []
PASS live :8524 actual snapshot, temperature/rain6/rain24, views, custom BJT bounds, lead groups, map click, station series, SVG/CSV export
```

Also passed Python compilation, JavaScript syntax checks, and `git diff --check`. The serialized live preview is `/tmp/station-grid-live-preview.html`.

## Behavior and remaining environment limits

- Main continuous period/group scores now follow grid semantics: equal weight per forecast time/lead combination, RMSE combines squares, and CC is the mean spatial correlation across stations at each valid time. Undefined spatial CC (fewer than three stations or constant fields) propagates as undefined. This differs from the old embedded UI's pooled station/time correlation.
- Rain still sums H/M/F (and N) before scoring, with events defined by accumulation >= threshold. Normal common-time selection now works; matched valid times do not guarantee identical station sets.
- Station mode defaults to the existing EC pair so unavailable recent GEFS rain does not empty the initial common comparison. All actual station models remain selectable.
- Map statistics pool all pairs in the chosen BJT date range and lead set; per-station CC is a temporal correlation. Common-time, run-hour, and single-instant main-chart filters are not applied to this supplement, as stated in its note. A lead group offers a within-group fixed lead for the station series.
- Browser layout/screenshots could not be verified: installed Chromium's renderer fails inside this sandbox (global-descriptor initialization errors; single-process attempt segfaults). Both fixture and live-data jsdom checks pass; graphical browser layout review remains outstanding.
- The original worktree branch cannot be advanced here: `git add` fails creating `/home/chengyang/verification-test/.git/worktrees/verification-test-stations/index.lock` with `Read-only file system`. The requested commit is instead recorded on `station-grid-unify` in local `cache/stations/review.git` and exported to `stations-review.bundle`. The shared branch/index are unchanged. Verify/list its commit with `git bundle verify stations-review.bundle` and `git bundle list-heads stations-review.bundle`; no merge or push was performed. User-supplied `TASK_STATION_GRID_UNIFY.md` and `run_codex_unify.sh` are excluded from the commit.
