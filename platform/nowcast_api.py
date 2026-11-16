"""Read-only aggregation for the near-term precipitation page.

Two sources:
* ``api/nowcast/qpe`` returns the radar QPE verification file verbatim.
* ``api/nowcast/qpf`` aggregates the existing 1-hour rain score rows that the
  platform already publishes, using only the forecast that was actually
  available when the nowcast would have been issued.

Importing this module never reads or writes the score cache.
"""
from collections import defaultdict
from datetime import datetime, timedelta
import gzip
import json
import os
from pathlib import Path
import threading

ROOT = Path(__file__).resolve().parent

THRESHOLDS = [0.1, 5, 10, 20, 50]
NOWCAST_HOURS = [1, 2, 3, 4, 5, 6]
MAX_STALE_HOURS = 12
DAYS = (3, 7, 15)
METHODS = [
    {"id": "ec9km", "label": "EC 9 km", "dataset": "openmeteo-降水-1",
     "model": "ECMWF-IFS-9km-OpenMeteo", "delay_hours": 8},
    {"id": "wn3", "label": "WN3（集合平均）", "dataset": "hk-降水-1",
     "model": "Google WeatherNext 3（集合均值）", "delay_hours": 8},
]

_LOCK = threading.Lock()
_QPF_CACHE = {}
_PREPARED = {}
_QPE_CACHE = {"key": None, "value": {"available": False}}


def threshold_key(value):
    return f"{value:g}"


def _stamp(value):
    if isinstance(value, datetime):
        moment = value
    else:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    # Catalog rows carry Beijing wall-clock time; keep the wall clock as published.
    return moment.replace(tzinfo=None) if moment.tzinfo is not None else moment


def _compact(row):
    """Return (valid, lead, run, threshold, H, M, F) or None for unusable rows."""
    try:
        threshold = float(row["threshold"])
        lead = int(row["lead"])
        valid = _stamp(row["valid"])
        run = _stamp(row["run"]) if row.get("run") else datetime.min
        return (valid, lead, run, threshold,
                int(row.get("H") or 0), int(row.get("M") or 0), int(row.get("F") or 0))
    except (KeyError, TypeError, ValueError):
        return None


def prepare_rows(rows_by_method):
    """Compact raw catalog rows into the fixed method order used by the page."""
    prepared = {}
    for method in METHODS:
        values = []
        for row in rows_by_method.get(method["id"], ()):
            item = _compact(row)
            if item is not None:
                values.append(item)
        prepared[method["id"]] = values
    return prepared


def compute(prepared, days):
    """Aggregate compact rows for ``days``. Pure function; raises ValueError."""
    days = int(days)
    if days not in DAYS:
        raise ValueError("日期范围只能是 3、7 或 15 天。")

    usable = {}
    latest = None
    for method in METHODS:
        delay = int(method["delay_hours"])
        lowest = min(NOWCAST_HOURS) + delay
        highest = max(NOWCAST_HOURS) + delay + MAX_STALE_HOURS
        rows = [row for row in prepared.get(method["id"], ())
                if lowest <= row[1] <= highest]
        usable[method["id"]] = rows
        for row in rows:
            if latest is None or row[0] > latest:
                latest = row[0]

    observed = {threshold_key(value): None for value in THRESHOLDS}
    if latest is None:
        return {"days": days, "start": None, "end": None,
                "thresholds": THRESHOLDS, "hours": NOWCAST_HOURS,
                "observed": observed, "methods": []}

    start = latest - timedelta(days=days)
    result_methods = []
    observed_at_one = {}
    for method in METHODS:
        # valid -> lead -> run -> threshold -> (H, M, F)
        groups = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
        for valid, lead, run, threshold, h, m, f in usable[method["id"]]:
            if not start < valid <= latest:
                continue
            groups[valid][lead][run][threshold] = (h, m, f)

        leads = {}
        has_data = False
        for hours in NOWCAST_HOURS:
            required = hours + int(method["delay_hours"])
            cells = {}
            counted = 0
            for valid in groups:
                candidates = [lead for lead in groups[valid] if lead >= required]
                if not candidates:
                    continue
                lead = min(candidates)
                if lead > required + MAX_STALE_HOURS:
                    continue
                # One run per (valid, lead); if duplicated, keep the newest run.
                rows_at = groups[valid][lead][max(groups[valid][lead])]
                if 0.1 in rows_at:
                    counted += 1
                for threshold in THRESHOLDS:
                    cell = rows_at.get(threshold)
                    if cell is None:
                        continue
                    h, m, f = cell
                    key = threshold_key(threshold)
                    totals = cells.setdefault(key, {"H": 0, "M": 0, "F": 0})
                    totals["H"] += h
                    totals["M"] += m
                    totals["F"] += f
            scores = {threshold_key(value): cells.get(threshold_key(value))
                      for value in THRESHOLDS}
            if any(value is not None for value in scores.values()):
                has_data = True
            leads[str(hours)] = {"hours": counted, "scores": scores}
            if hours == 1:
                for value in THRESHOLDS:
                    key = threshold_key(value)
                    if key not in observed_at_one and scores[key] is not None:
                        observed_at_one[key] = scores[key]["H"] + scores[key]["M"]

        if has_data:
            result_methods.append({"id": method["id"], "label": method["label"],
                                   "delay_hours": int(method["delay_hours"]),
                                   "leads": leads})

    observed.update(observed_at_one)
    return {"days": days,
            "start": start.isoformat(timespec="seconds"),
            "end": latest.isoformat(timespec="seconds"),
            "thresholds": THRESHOLDS, "hours": NOWCAST_HOURS,
            "observed": observed, "methods": result_methods}


def _read_rows(snapshot, dataset, model):
    """Yield the rows of one dataset that belong to the requested model."""
    try:
        path = snapshot.rows_file(dataset)
    except (AttributeError, KeyError):
        return
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        return
    for row in payload["rows"]:
        if isinstance(row, dict) and row.get("model") == model and row.get("threshold") is not None:
            yield row


def _snapshot_identity(snapshot):
    return getattr(snapshot, "snapshot_id", None) or id(snapshot)


def _prepared_for(snapshot):
    identity = _snapshot_identity(snapshot)
    with _LOCK:
        cached = _PREPARED.get(identity)
    if cached is not None:
        return cached
    rows = {}
    for method in METHODS:
        rows[method["id"]] = list(_read_rows(snapshot, method["dataset"], method["model"]))
    prepared = prepare_rows(rows)
    with _LOCK:
        _PREPARED[identity] = prepared
        while len(_PREPARED) > 3:
            _PREPARED.pop(next(iter(_PREPARED)))
    return prepared


def _qpf_for(snapshot, days):
    key = (_snapshot_identity(snapshot), int(days))
    with _LOCK:
        cached = _QPF_CACHE.get(key)
    if cached is not None:
        return cached
    value = compute(_prepared_for(snapshot), days)
    with _LOCK:
        _QPF_CACHE[key] = value
        while len(_QPF_CACHE) > 6:
            _QPF_CACHE.pop(next(iter(_QPF_CACHE)))
    return value


def _current_snapshot():
    import server
    snapshot = server.current_http_snapshot()
    if snapshot is None:
        raise RuntimeError("评分资料尚未就绪")
    return snapshot


def qpf(days):
    """Return the short-range QPF scores for 3, 7 or 15 days."""
    try:
        days = int(days)
    except (TypeError, ValueError):
        raise ValueError("日期范围只能是 3、7 或 15 天。") from None
    if days not in DAYS:
        raise ValueError("日期范围只能是 3、7 或 15 天。")
    return _qpf_for(_current_snapshot(), days)


def qpe_path():
    configured = os.environ.get("NOWCAST_QPE_FILE")
    return Path(configured) if configured else ROOT / "cache" / "nowcast" / "qpe-history.json"


def qpe():
    """Return the radar QPE verification file unchanged, or available=false."""
    path = qpe_path()
    try:
        info = path.stat()
        key = (str(path), info.st_mtime_ns, info.st_size)
    except OSError:
        key = None
        value = {"available": False}
    else:
        with _LOCK:
            if _QPE_CACHE["key"] == key:
                return _QPE_CACHE["value"]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if (not isinstance(data, dict) or not isinstance(data.get("methods"), list)
                    or not isinstance(data.get("thresholds"), list)):
                raise ValueError("格式不匹配")
            value = {"available": True, "data": data}
        except (OSError, ValueError, TypeError):
            value = {"available": False}
    with _LOCK:
        _QPE_CACHE.update(key=key, value=value)
    return value
