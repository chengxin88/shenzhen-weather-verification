import gzip
import json
import os
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

import nowcast_api as nowcast

EC = nowcast.METHODS[0]
WN3 = nowcast.METHODS[1]


def row(valid, lead, threshold, H=0, M=0, F=0, method=EC):
    moment = datetime.fromisoformat(valid)
    if lead < 0:
        moment = moment + timedelta(hours=-lead)
    return {"model": method["model"], "dataset": method["dataset"],
            "run": (moment - timedelta(hours=lead)).isoformat(timespec="seconds"),
            "valid": moment.isoformat(timespec="seconds"), "lead": lead,
            "threshold": threshold, "n": 100, "H": H, "M": M, "F": F}


def scores(rows, days=3):
    return nowcast.compute(nowcast.prepare_rows(rows), days)


class LeadSelection(unittest.TestCase):
    def test_picks_smallest_lead_that_was_available(self):
        valid = "2026-11-01T12:00:00"
        rows = {EC["id"]: [row(valid, 3, 0.1, H=100), row(valid, 9, 0.1, H=10),
                           row(valid, 15, 0.1, H=20)]}
        value = scores(rows)
        leads = value["methods"][0]["leads"]
        self.assertEqual(leads["1"]["scores"]["0.1"]["H"], 10)
        self.assertEqual(leads["2"]["scores"]["0.1"]["H"], 20)
        self.assertEqual(leads["6"]["scores"]["0.1"]["H"], 20)

    def test_lead_older_than_stale_limit_is_skipped(self):
        valid = "2026-11-01T12:00:00"
        rows = {EC["id"]: [row(valid, 22, 0.1, H=5)]}
        value = scores(rows)
        leads = value["methods"][0]["leads"]
        # k=1 needs lead >= 9 and accepts at most 21, so the only row is too old.
        self.assertIsNone(leads["1"]["scores"]["0.1"])
        self.assertEqual(leads["6"]["scores"]["0.1"]["H"], 5)
        # The column note is defined from k=1, so it stays empty here.
        self.assertIsNone(value["observed"]["0.1"])

    def test_rows_outside_the_usable_lead_range_never_appear(self):
        valid = "2026-11-01T12:00:00"
        rows = {EC["id"]: [row(valid, 3, 0.1, H=1), row(valid, 27, 0.1, H=2)]}
        value = scores(rows)
        self.assertEqual(value["methods"], [])


class MissingThresholds(unittest.TestCase):
    def test_missing_5mm_row_only_leaves_that_threshold_empty(self):
        valid = "2026-11-01T12:00:00"
        rows = {EC["id"]: [row(valid, 9, 0.1, H=4, M=2, F=1)]}
        value = scores(rows)
        scores_at_one = value["methods"][0]["leads"]["1"]["scores"]
        self.assertIsNone(scores_at_one["5"])
        self.assertEqual(scores_at_one["0.1"], {"H": 4, "M": 2, "F": 1})

    def test_observed_falls_back_to_the_next_method_per_threshold(self):
        valid = "2026-11-01T12:00:00"
        rows = {EC["id"]: [row(valid, 9, 0.1, H=7, M=3)],
                WN3["id"]: [row(valid, 9, 0.1, H=1, M=1, method=WN3),
                            row(valid, 9, 5, H=2, M=2, method=WN3)]}
        value = scores(rows)
        self.assertEqual(value["observed"]["0.1"], 10)
        self.assertEqual(value["observed"]["5"], 4)
        self.assertIsNone(value["observed"]["10"])


class DateWindow(unittest.TestCase):
    def test_start_is_exclusive_and_end_is_inclusive(self):
        end = datetime(2026, 11, 10, 7)
        start = end - timedelta(days=3)
        rows = {EC["id"]: [row(start.isoformat(), 9, 0.1, H=999),
                           row((start + timedelta(hours=1)).isoformat(), 9, 0.1, H=2),
                           row(end.isoformat(), 9, 0.1, H=1)]}
        value = scores(rows, 3)
        self.assertEqual(value["start"], start.isoformat(timespec="seconds"))
        self.assertEqual(value["end"], end.isoformat(timespec="seconds"))
        self.assertEqual(value["methods"][0]["leads"]["1"]["scores"]["0.1"]["H"], 3)
        self.assertEqual(value["methods"][0]["leads"]["1"]["hours"], 2)

    def test_invalid_days_raise(self):
        prepared = {"ec9km": [], "wn3": []}
        for days in (1, 5, "x"):
            with self.assertRaises(ValueError):
                nowcast.compute(prepared, days)
        with self.assertRaises(ValueError):
            nowcast.qpf(5)


def write_payload(path, rows):
    payload = {"dataset": "fixture", "snapshot_id": "x", "rows": rows}
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle)


class SnapshotCache(unittest.TestCase):
    class Fake:
        def __init__(self, identity, paths):
            self.snapshot_id = identity
            self.paths = paths

        def rows_file(self, dataset):
            return self.paths[dataset]

    def test_cache_follows_the_snapshot_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = {method["dataset"]: root / (method["id"] + ".json.gz") for method in nowcast.METHODS}
            valid = "2026-11-10T07:00:00"
            write_payload(paths[EC["dataset"]], [row(valid, 9, 0.1, H=1)])
            write_payload(paths[WN3["dataset"]], [])
            first = self.Fake("one", paths)
            value = nowcast._qpf_for(first, 3)
            self.assertEqual(value["methods"][0]["leads"]["1"]["scores"]["0.1"]["H"], 1)

            # Same snapshot id: still served from cache although the file changed.
            write_payload(paths[EC["dataset"]], [row(valid, 9, 0.1, H=9)])
            self.assertIs(nowcast._qpf_for(first, 3), value)

            second = self.Fake("two", paths)
            newer = nowcast._qpf_for(second, 3)
            self.assertEqual(newer["methods"][0]["leads"]["1"]["scores"]["0.1"]["H"], 9)

    def test_method_without_rows_is_not_returned(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = {method["dataset"]: root / (method["id"] + ".json.gz") for method in nowcast.METHODS}
            valid = "2026-11-10T07:00:00"
            write_payload(paths[EC["dataset"]], [])
            write_payload(paths[WN3["dataset"]], [row(valid, 9, 0.1, H=2, method=WN3)])
            value = nowcast._qpf_for(self.Fake("only", paths), 3)
            self.assertEqual([method["id"] for method in value["methods"]], [WN3["id"]])

    def test_missing_dataset_file_returns_no_scores(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            missing = root / "absent.json.gz"
            paths = {method["dataset"]: missing for method in nowcast.METHODS}
            value = nowcast._qpf_for(self.Fake("empty", paths), 3)
            self.assertEqual(value["methods"], [])
            self.assertIsNone(value["start"])


class QpeFile(unittest.TestCase):
    sample = {"version": 1, "clock": "整点小时", "truth": "实况", "radar": "雷达",
              "period": {"start": "2025-04-01", "end": "2026-04-24", "events": 1},
              "thresholds": [0.1, 5], "methods": [{"id": "a", "show": True, "counts": {}}]}

    def with_file(self, path, value=None, raw=None):
        if raw is not None:
            path.write_text(raw, encoding="utf-8")
        else:
            path.write_text(json.dumps(value), encoding="utf-8")
        return mock.patch.dict(os.environ, {"NOWCAST_QPE_FILE": str(path)})

    def test_missing_bad_and_normal_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "qpe.json"
            with mock.patch.dict(os.environ, {"NOWCAST_QPE_FILE": str(path)}):
                self.assertEqual(nowcast.qpe(), {"available": False})
            with self.with_file(path, raw="{not json"):
                self.assertEqual(nowcast.qpe(), {"available": False})
            with self.with_file(path, {"methods": [], "clock": "x"}):
                self.assertEqual(nowcast.qpe(), {"available": False})
            with self.with_file(path, self.sample):
                self.assertEqual(nowcast.qpe(), {"available": True, "data": self.sample})

    def test_file_update_is_read_again(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "qpe.json"
            with self.with_file(path, self.sample):
                first = nowcast.qpe()
                changed = dict(self.sample, radar="新雷达")
                path.write_text(json.dumps(changed), encoding="utf-8")
                stamp = path.stat()
                os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000_000))
                second = nowcast.qpe()
            self.assertEqual(first["data"]["radar"], "雷达")
            self.assertEqual(second["data"]["radar"], "新雷达")


if __name__ == "__main__":
    unittest.main()
