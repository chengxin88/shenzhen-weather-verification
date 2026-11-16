"""整个起报重评时只写有变化的配对（2026-11-16，为国家站扩到 CMA-GD 全域做准备）。

以前站表多一个站，所有起报都要整段删掉配对再全部插入；现在只写新增、变了的配对，删掉消失的配对，
最后的表内容必须和整段重写完全相同。
"""
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

import station_score
from station_common import UTC, atomic_json, iso
from station_obs import row


class RescoreDiffTests(unittest.TestCase):
    def test_full_rescore_writes_only_changed_pairs(self):
        end = datetime(2026, 10, 25, 9, tzinfo=UTC); run = end - timedelta(hours=6); at = iso(end)
        a = dict(id='59287', type='synop', name='甲', lat=23., lon=113., elevation=10, sources=['ogimet'])
        b = dict(id='59288', type='synop', name='乙', lat=23.5, lon=113.5, elevation=20, sources=['ogimet'])
        obs = {('59287', at, 't'): row('59287', at, 't', 20, 'ogimet'),
               ('59288', at, 't'): row('59288', at, 't', 18, 'ogimet')}
        source = []
        rounds = [  # (站表, 各站预报, 预报源签名, 本轮应写入条数, 结束后的配对 {站: (预报, 实况)})
            ([a], [21.], 'source-a', 1, {'59287': (21., 20.)}),
            ([a, b], [21., 22.], 'source-b', 1, {'59287': (21., 20.), '59288': (22., 18.)}),   # 加一站：只写新站
            ([a, b], [23., 22.], 'source-c', 1, {'59287': (23., 20.), '59288': (22., 18.)}),   # 一站预报变了：只改这一条
            ([a, b], [23., 22.], 'source-d', 0, {'59287': (23., 20.), '59288': (22., 18.)}),   # 源签名变但值没变：一条不写
            ([b], [22.], 'source-e', 0, {'59288': (22., 18.)}),                                 # 减一站：只删
        ]
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(station_score, 'forecast_runs', side_effect=lambda *args, **kwargs: iter(source)), \
                patch.object(station_score, 'load_observations', side_effect=lambda *args: (obs, {}, {})), \
                patch.object(station_score, 'shenzhen_sanity', return_value={'n': 0}):
            root = Path(temporary)
            for stations, values, signature, written, expected in rounds:
                atomic_json(root / 'stations.json', {'stations': stations})
                source[:] = [('ec01', run, {6: {'t': np.array(values)}}, signature, [])]
                result = station_score.refresh(root, end)
                self.assertEqual(result['processed_runs'], 1, signature)
                self.assertEqual(result['inserted_pairs'], written, signature)
                with sqlite3.connect(root / 'working.sqlite') as db:
                    pairs = {s: (fc, ob) for s, fc, ob in db.execute("SELECT station,fc,ob FROM pairs WHERE var='t'")}
                    self.assertEqual(pairs, expected, signature)
                    # 其余列也要和整段重写时一模一样
                    for s, lead, valid, source_name, trace, report in db.execute(
                            'SELECT station,lead,valid,source,trace,report_time FROM pairs'):
                        self.assertEqual((lead, valid, source_name, trace), (6, at, 'ogimet', 0), signature)
                        self.assertEqual(report, obs[s, at, 't'].get('report_time', obs[s, at, 't']['time']), signature)


if __name__ == '__main__':
    unittest.main()
