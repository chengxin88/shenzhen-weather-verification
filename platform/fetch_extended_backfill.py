#!/usr/bin/env python3
"""Backfill extended-range (171-240h) surface verification frames for ecmwf, gfs, aifs, aigfs.

Frames are written to cache/native-backfill/<slug> in the regional-v1 layout
(station box 104-121E / 18-27N, 0.25 deg) so both native_archive.py and
station_forecasts.py automatically discover and score 168-240h leads without
touching the live wide-region cache on /mnt/g.
"""
import argparse, json, os, re, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
import numpy as np, requests, eccodes

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_gefs import session, get, crop, log, iso

BASE_OUT = Path(__file__).resolve().parent / "cache" / "native-backfill"
BOUNDS = [104., 18., 121., 27.]
LON = np.arange(BOUNDS[0], BOUNDS[2] + 1e-6, .25)
LAT = np.arange(BOUNDS[1], BOUNDS[3] + 1e-6, .25)
SHAPE = (len(LAT), len(LON))

EC_SOURCES = (
    "https://storage.googleapis.com/ecmwf-open-data",
    "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com",
)


def current(path):
    try:
        return np.load(path, mmap_mode="r", allow_pickle=False).shape == SHAPE
    except (OSError, ValueError):
        return False


def compute_rh(t_c, td_c):
    return np.clip(
        100.0 * np.exp(17.625 * td_c / (243.04 + td_c) - 17.625 * t_c / (243.04 + t_c)),
        0.0,
        100.0,
    ).astype(np.float32)


def save_npy(folder, name, arr):
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / f"{name}.tmp.{os.getpid()}.{threading.get_ident()}.npy"
    np.save(tmp, np.asarray(arr, dtype=np.float32))
    os.replace(tmp, folder / f"{name}.npy")


def save_json(meta_path, doc):
    tmp = meta_path.with_suffix(f".tmp.{os.getpid()}.{threading.get_ident()}")
    tmp.write_text(json.dumps(doc, ensure_ascii=False))
    os.replace(tmp, meta_path)


def idx_message(s, url, lines, pattern):
    for n, line in enumerate(lines):
        if re.search(pattern, line):
            start = int(line.split(":")[1])
            end = int(lines[n + 1].split(":")[1]) - 1 if n + 1 < len(lines) else None
            return get(s, url, (start, end))
    return None


# --- ECMWF (ifs/0p25/oper) & AIFS (aifs-single/0p25/oper) ---
EC_PARAMS = {
    "2t": ("surface_t", 1.0, -273.15),
    "2d": ("surface_td", 1.0, -273.15),
    "10u": ("surface_u", 1.0, 0.0),
    "10v": ("surface_v", 1.0, 0.0),
    "msl": ("surface_msl", 0.01, 0.0),
    "tp": ("tp_cum", None, 0.0),
}


def ec_stem(slug, run, lead):
    return BASE_OUT / slug / f"{slug}_{run:%Y%m%d%H}_f{lead:03d}_global1"


def ec_download_lead(slug, run, lead, s, helper=False):
    stem = ec_stem(slug, run, lead)
    folder = stem.with_suffix(".fields")
    needed = ["tp"] if helper else list(EC_PARAMS)
    missing = [p for p in needed if not current(folder / f"{EC_PARAMS[p][0]}.npy")]
    if not missing and (helper or current(folder / "surface_rh.npy")):
        return "cached"
    subpath = (
        f"{run:%Y%m%d}/{run:%H}z/ifs/0p25/oper/{run:%Y%m%d%H}0000-{lead}h-oper-fc"
        if slug == "ecmwf"
        else f"{run:%Y%m%d}/{run:%H}z/aifs-single/0p25/oper/{run:%Y%m%d%H}0000-{lead}h-oper-fc"
    )
    index_bytes = None
    base_url = None
    for src in EC_SOURCES:
        u = f"{src}/{subpath}"
        index_bytes = get(s, u + ".index")
        if index_bytes is not None:
            base_url = u
            break
    if index_bytes is None:
        return "missing"
    entries = [json.loads(line) for line in index_bytes.decode().splitlines() if line.strip()]
    by_param = {e.get("param"): e for e in entries if e.get("levtype") == "sfc"}
    for p in missing:
        e = by_param.get(p)
        if not e:
            return f"no-{p}"
        raw = get(s, base_url + ".grib2", (int(e["_offset"]), int(e["_offset"]) + int(e["_length"]) - 1))
        if raw is None:
            return f"no-{p}"
        name, scale, offset = EC_PARAMS[p]
        if p == "tp":
            g = eccodes.codes_new_from_message(raw)
            try:
                units = eccodes.codes_get(g, "units")
            finally:
                eccodes.codes_release(g)
            scale = 1000.0 if units == "m" else 1.0
        arr = crop(raw, LON, LAT) * np.float32(scale) + np.float32(offset)
        save_npy(folder, name, arr)
    if not helper and current(folder / "surface_t.npy") and current(folder / "surface_td.npy"):
        if not current(folder / "surface_rh.npy"):
            t_c = np.load(folder / "surface_t.npy")
            td_c = np.load(folder / "surface_td.npy")
            save_npy(folder, "surface_rh", compute_rh(t_c, td_c))
    return "ok"


def ec_finish_lead(slug, run, lead, helper=False):
    if helper:
        return "helper"
    stem = ec_stem(slug, run, lead)
    meta = stem.with_suffix(".json")
    folder = stem.with_suffix(".fields")
    if meta.is_file() and current(folder / "rain6h.npy") and current(folder / "surface_rh.npy"):
        return "cached"
    prev_tp = ec_stem(slug, run, lead - 6).with_suffix(".fields") / "tp_cum.npy"
    if not (current(folder / "tp_cum.npy") and current(prev_tp) and current(folder / "surface_t.npy")):
        return "incomplete"
    rain = np.maximum(np.load(folder / "tp_cum.npy") - np.load(prev_tp), 0.0).astype(np.float32)
    save_npy(folder, "rain6h", rain)
    valid = run + timedelta(hours=lead)
    model_name = "ECMWF-IFS" if slug == "ecmwf" else "ECMWF-AIFS"
    doc = dict(
        schema=2,
        model=model_name,
        run=iso(run),
        valid=iso(valid),
        lead=lead,
        storage=f"{slug}-regional-v1",
        bounds=BOUNDS,
        native_grid_degrees=0.25,
        native_grid_shape=[len(LAT), len(LON)],
        periodic_longitude=False,
        lon=LON.tolist(),
        lat=LAT.tolist(),
        interval_hours=6,
        rain_start=iso(valid - timedelta(hours=6)),
        rain_accum_start=0,
        **{"global": False},
        source=f"ECMWF Open Data {model_name} 0.25",
    )
    save_json(meta, doc)
    return "ok"


# --- AIGFS (nomads) ---
AIGFS_FIELDS = {
    "surface_t": (r":TMP:2 m above ground:", 1.0, -273.15),
    "surface_msl": (r":PRMSL:mean sea level:", 0.01, 0.0),
    "surface_u": (r":UGRD:10 m above ground:", 1.0, 0.0),
    "surface_v": (r":VGRD:10 m above ground:", 1.0, 0.0),
}


def aigfs_frame(run, lead, s):
    stem = BASE_OUT / "aigfs" / f"aigfs_{run:%Y%m%d%H}_f{lead:03d}_global1"
    meta = stem.with_suffix(".json")
    folder = stem.with_suffix(".fields")
    wanted = list(AIGFS_FIELDS) + ["rain6h"]
    missing = [n for n in wanted if not current(folder / f"{n}.npy")]
    if meta.is_file() and not missing:
        return "cached"
    url = f"https://nomads.ncep.noaa.gov/pub/data/nccf/com/aigfs/prod/aigfs.{run:%Y%m%d}/{run:%H}/model/atmos/grib2/aigfs.t{run:%H}z.sfc.f{lead:03d}.grib2"
    idx = get(s, url + ".idx")
    if idx is None:
        return "missing"
    lines = idx.decode().splitlines()
    for name in missing:
        pattern, scale, offset = (
            AIGFS_FIELDS[name]
            if name != "rain6h"
            else (rf":APCP:surface:{lead - 6}-{lead} hour acc", 1.0, 0.0)
        )
        raw = idx_message(s, url, lines, pattern)
        if raw is None:
            return f"no-{name}"
        arr = crop(raw, LON, LAT) * np.float32(scale) + np.float32(offset)
        if name == "rain6h":
            arr = np.maximum(arr, 0.0)
        save_npy(folder, name, arr)
    valid = run + timedelta(hours=lead)
    doc = dict(
        schema=2,
        model="NCEP-AIGFS",
        run=iso(run),
        valid=iso(valid),
        lead=lead,
        storage="aigfs-regional-v1",
        bounds=BOUNDS,
        native_grid_degrees=0.25,
        native_grid_shape=[len(LAT), len(LON)],
        periodic_longitude=False,
        lon=LON.tolist(),
        lat=LAT.tolist(),
        interval_hours=6,
        rain_start=iso(valid - timedelta(hours=6)),
        rain_accum_start=lead - 6,
        **{"global": False},
        source="NOAA NOMADS AIGFS 0.25",
    )
    save_json(meta, doc)
    return "ok"


# --- GFS (noaa-gfs-bdp-pds S3, 3-hourly 171..240) ---
GFS_FIELDS = {
    "surface_t": (r":TMP:2 m above ground:", 1.0, -273.15),
    "surface_rh": (r":RH:2 m above ground:", 1.0, 0.0),
    "surface_msl": (r":PRMSL:mean sea level:", 0.01, 0.0),
    "surface_u": (r":UGRD:10 m above ground:", 1.0, 0.0),
    "surface_v": (r":VGRD:10 m above ground:", 1.0, 0.0),
}


def gfs_stem(run, lead):
    return BASE_OUT / "gfs" / f"gfs_{run:%Y%m%d%H}_f{lead:03d}_global1"


def gfs_download_lead(run, lead, s):
    stem = gfs_stem(run, lead)
    folder = stem.with_suffix(".fields")
    rain_key = "rain3h" if lead % 6 == 3 else "apcp6h"
    wanted = list(GFS_FIELDS) + [rain_key]
    missing = [n for n in wanted if not current(folder / f"{n}.npy")]
    if not missing:
        return "cached"
    url = f"https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.{run:%Y%m%d}/{run:%H}/atmos/gfs.t{run:%H}z.pgrb2.0p25.f{lead:03d}"
    idx = get(s, url + ".idx")
    if idx is None:
        return "missing"
    lines = idx.decode().splitlines()
    for name in missing:
        if name in GFS_FIELDS:
            pattern, scale, offset = GFS_FIELDS[name]
        elif name == "rain3h":
            pattern, scale, offset = (rf":APCP:surface:{lead - 3}-{lead} hour acc", 1.0, 0.0)
        else:
            pattern, scale, offset = (rf":APCP:surface:{lead - 6}-{lead} hour acc", 1.0, 0.0)
        raw = idx_message(s, url, lines, pattern)
        if raw is None:
            return f"no-{name}"
        arr = crop(raw, LON, LAT) * np.float32(scale) + np.float32(offset)
        if name in ("rain3h", "apcp6h"):
            arr = np.maximum(arr, 0.0)
        save_npy(folder, name, arr)
    return "ok"


def gfs_finish_lead(run, lead):
    stem = gfs_stem(run, lead)
    meta = stem.with_suffix(".json")
    folder = stem.with_suffix(".fields")
    if meta.is_file() and current(folder / "rain3h.npy"):
        return "cached"
    if lead % 6 == 0:
        prev_r3 = gfs_stem(run, lead - 3).with_suffix(".fields") / "rain3h.npy"
        if not (current(folder / "apcp6h.npy") and current(prev_r3)):
            return "incomplete"
        r3 = np.maximum(np.load(folder / "apcp6h.npy") - np.load(prev_r3), 0.0).astype(np.float32)
        save_npy(folder, "rain3h", r3)
    if not current(folder / "rain3h.npy") or not current(folder / "surface_t.npy"):
        return "incomplete"
    valid = run + timedelta(hours=lead)
    doc = dict(
        schema=2,
        model="NCEP-GFS",
        run=iso(run),
        valid=iso(valid),
        lead=lead,
        storage="gfs-regional-v1",
        bounds=BOUNDS,
        native_grid_degrees=0.25,
        native_grid_shape=[len(LAT), len(LON)],
        periodic_longitude=False,
        lon=LON.tolist(),
        lat=LAT.tolist(),
        interval_hours=3,
        rain_start=iso(valid - timedelta(hours=3)),
        rain_accum_start=lead - 3,
        **{"global": False},
        source="NOAA GFS 0.25",
    )
    save_json(meta, doc)
    return "ok"


def run_model(slug, valid_days=8, workers=12):
    (BASE_OUT / slug).mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    valid_start = (now - timedelta(days=valid_days)).replace(hour=0, minute=0, second=0, microsecond=0)
    valid_end = now - timedelta(hours=1)
    first_run = valid_start - timedelta(hours=240)
    last_run = valid_end - timedelta(hours=171)
    hours = (0, 12) if slug == "ecmwf" else (0, 6, 12, 18)
    cur = first_run.replace(hour=0, minute=0, second=0, microsecond=0)
    runs = []
    while cur <= last_run:
        for h in hours:
            r = cur.replace(hour=h)
            if first_run <= r <= last_run:
                if slug == "aigfs" and r < datetime(2026, 10, 24, tzinfo=timezone.utc):
                    continue
                runs.append(r)
        cur += timedelta(days=1)
    runs.sort(reverse=True)

    jobs = []
    for r in runs:
        if slug in ("ecmwf", "aifs"):
            active = [L for L in range(174, 241, 6) if valid_start <= r + timedelta(hours=L) <= valid_end]
            if active:
                helper = min(active) - 6
                jobs.append((r, helper, True))
                for L in active:
                    jobs.append((r, L, False))
        elif slug == "aigfs":
            for L in range(174, 241, 6):
                if valid_start <= r + timedelta(hours=L) <= valid_end:
                    jobs.append((r, L, False))
        elif slug == "gfs":
            active = [L for L in range(171, 241, 3) if valid_start <= r + timedelta(hours=L) <= valid_end]
            if active and active[0] % 6 == 0:
                jobs.append((r, active[0] - 3, True))
            for L in active:
                jobs.append((r, L, False))

    log(slug, "runs", len(runs), "jobs", len(jobs))
    local = threading.local()

    def work(job):
        if not hasattr(local, "s"):
            local.s = session()
        r, L, helper = job
        if slug in ("ecmwf", "aifs"):
            return ec_download_lead(slug, r, L, local.s, helper=helper)
        elif slug == "aigfs":
            return aigfs_frame(r, L, local.s)
        else:
            return gfs_download_lead(r, L, local.s)

    counts = {}
    t0 = time.time()
    with ThreadPoolExecutor(workers) as ex:
        futures = {ex.submit(work, j): j for j in jobs}
        for n, f in enumerate(as_completed(futures), 1):
            try:
                res = f.result()
            except Exception as e:
                res = f"err:{type(e).__name__}:{e}"
            counts[res] = counts.get(res, 0) + 1
            if n % 40 == 0 or n == len(jobs):
                log(slug, f"{n}/{len(jobs)} {time.time() - t0:.1f}s", counts)

    if slug in ("ecmwf", "aifs", "gfs"):
        fin_counts = {}
        for r, L, helper in sorted(jobs):
            res = ec_finish_lead(slug, r, L, helper=helper) if slug in ("ecmwf", "aifs") else ("helper" if helper else gfs_finish_lead(r, L))
            fin_counts[res] = fin_counts.get(res, 0) + 1
        log(slug, "FINISHED", fin_counts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="aigfs,ecmwf,aifs,gfs")
    ap.add_argument("--days", type=int, default=8)
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()
    for slug in [m.strip() for m in args.models.split(",") if m.strip()]:
        run_model(slug, valid_days=args.days, workers=args.workers)


if __name__ == "__main__":
    main()
