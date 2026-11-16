#!/usr/bin/env python3
"""Backfill extended-range (174..240h) station forecasts for EC 9km (ec01) via Open-Meteo Single-Runs API."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import time
import urllib.parse
import numpy as np
import requests

ROOT = Path("/home/yjzx/verification-platform/verification-test")
SZ_ROOT = Path("/home/yjzx/verification-platform/verification-test-szdist")
EXT_LEADS = [int(h) for h in range(174, 241, 6)]


def fetch_run(session: requests.Session, ident: str, stations: list[dict]) -> dict[int, dict] | None:
    dt = datetime.strptime(ident, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    lats = ",".join(f"{st.get('lat'):.4f}" for st in stations)
    lons = ",".join(f"{st.get('lon'):.4f}" for st in stations)
    elevs = ",".join(["nan"] * len(stations))
    params = {
        "latitude": lats,
        "longitude": lons,
        "elevation": elevs,
        "models": "ecmwf_ifs",
        "run": dt.strftime("%Y-%m-%dT%H:%M"),
        "temporal_resolution": "hourly_6",
        "forecast_hours": "241",
        "hourly": "temperature_2m,dew_point_2m,pressure_msl,wind_speed_10m,precipitation",
        "wind_speed_unit": "ms",
        "temperature_unit": "celsius",
        "precipitation_unit": "mm",
        "cell_selection": "nearest",
        "timezone": "GMT",
        "timeformat": "unixtime",
    }
    url = "https://single-runs-api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)
    for attempt in range(4):
        try:
            resp = session.get(url, headers={"User-Agent": "SZVerify-EC9km/1.0"}, timeout=(10, 45))
            resp.raise_for_status()
            data = resp.json()
            break
        except Exception as exc:
            if attempt == 3:
                print(f"FAIL {ident}: {exc}", flush=True)
                return None
            time.sleep(2.0 * (attempt + 1))

    if not isinstance(data, list) or len(data) != len(stations):
        print(f"UNEXPECTED {ident}: len={len(data) if isinstance(data, list) else type(data)}", flush=True)
        return None

    times = np.asarray(data[0]["hourly"]["time"], dtype=np.int64)
    base_ts = int(dt.timestamp())
    leads_all = np.rint((times - base_ts) / 3600.0).astype(np.int16)
    lead_idx = {int(l): idx for idx, l in enumerate(leads_all)}
    if not all(int(l) in lead_idx for l in EXT_LEADS):
        print(f"MISSING LEADS {ident}: have max {leads_all.max() if len(leads_all) else None}", flush=True)
        return None

    sel = [lead_idx[int(l)] for l in EXT_LEADS]
    n_leads = len(EXT_LEADS)
    n_st = len(stations)
    t = np.full((n_leads, n_st), np.nan, dtype=np.float64)
    td = np.full((n_leads, n_st), np.nan, dtype=np.float64)
    msl = np.full((n_leads, n_st), np.nan, dtype=np.float64)
    wind = np.full((n_leads, n_st), np.nan, dtype=np.float64)
    rain = np.full((n_leads, n_st), np.nan, dtype=np.float64)

    for s_idx, item in enumerate(data):
        h = item["hourly"]
        t[:, s_idx] = [np.nan if v is None else float(v) for v in np.asarray(h["temperature_2m"], dtype=object)[sel]]
        td[:, s_idx] = [np.nan if v is None else float(v) for v in np.asarray(h["dew_point_2m"], dtype=object)[sel]]
        msl[:, s_idx] = [np.nan if v is None else float(v) for v in np.asarray(h["pressure_msl"], dtype=object)[sel]]
        wind[:, s_idx] = [np.nan if v is None else float(v) for v in np.asarray(h["wind_speed_10m"], dtype=object)[sel]]
        rain[:, s_idx] = [np.nan if v is None else float(v) for v in np.asarray(h["precipitation"], dtype=object)[sel]]

    samples: dict[int, dict] = {}
    for i, lead in enumerate(EXT_LEADS):
        samples[int(lead)] = {
            "t": t[i],
            "td": td[i],
            "msl": msl[i],
            "wind": wind[i],
            "rain": rain[i],
            "rain_start": int(lead) - 6,
        }
    return samples


def main():
    meta_path = SZ_ROOT / "cache" / "stations" / "stations.json"
    if not meta_path.exists():
        meta_path = ROOT / "cache" / "stations" / "stations.json"
    meta = json.loads(meta_path.read_text("utf-8"))
    stations = meta["stations"]
    import station_forecasts as sf

    session = requests.Session()
    session.trust_env = False
    proxy = os.getenv("OPENMETEO_EC_PROXY", "")
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}

    now = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
    start = datetime(2026, 10, 16, 0, tzinfo=timezone.utc)
    cur = start
    idents = []
    while cur <= now:
        idents.append(cur.strftime("%Y%m%d%H"))
        cur += timedelta(hours=12)

    out_dirs = [
        ROOT / "cache" / "stations" / "forecast",
        SZ_ROOT / "cache" / "stations" / "forecast",
    ]
    for d in out_dirs:
        d.mkdir(parents=True, exist_ok=True)

    ok = 0
    for ident in idents:
        targets = [d / f"ec01_{ident}.extended.npz" for d in out_dirs if d.parent.exists()]
        if all(t.exists() for t in targets):
            ok += 1
            continue
        t0 = time.time()
        samples = fetch_run(session, ident, stations)
        if samples is None:
            continue
        for target in targets:
            sf.save_samples(target, sorted(samples), samples)
        ok += 1
        print(f"OK ec01_{ident} in {time.time()-t0:.1f}s", flush=True)
        time.sleep(0.2)
    print(f"DONE ec01 extended: {ok}/{len(idents)}", flush=True)


if __name__ == "__main__":
    main()
