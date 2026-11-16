"""Small offline snapshots for NMIC window policy; no raw input or network."""
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
import gzip
import json
import os
import sqlite3
import tempfile
import unittest

import station_api as api
from station_common import iso, stamp


class WindowPolicy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.directory = self.root/'snapshots/20261111T000000'
        self.directory.mkdir(parents=True)
        self.env = patch.dict(os.environ, STATIONS_CACHE_DIR=str(self.root))
        self.env.start(); self.addCleanup(self.env.stop)
        self.addCleanup(self.clear)
        self.stations = [dict(id=f'N{i}', sources=['ogimet', 'nmic'], province_code='44',
                              lon=113.2+i*.1, lat=23.1) for i in range(3)]
        self.stations += [dict(id='SZ-test', sources=['szgrid'], lon=114.1, lat=22.6),
                          dict(id='VHHH', sources=['iem'], lon=114.2, lat=22.3)]
        self.stations += [dict(id=f'O{i}', sources=['ogimet'], lon=110, lat=30) for i in range(5)]
        self.stations += [dict(id='52112', sources=['nmic'], lon=93, lat=43)]
        self.metadata = dict(stations=self.stations, window_end='2026-11-11T00:00:00Z',
                             nmic_end='2026-11-08T18:00:00Z')
        self.save()
        (self.root/'current.json').write_text(json.dumps(dict(generation=self.directory.name)))
        start = stamp(self.metadata['nmic_end'])-timedelta(days=3)
        stop = stamp(self.metadata['nmic_end'])
        with sqlite3.connect(self.directory/'pairs.sqlite') as db:
            db.execute('CREATE TABLE pairs(model,run,lead,valid,station,var,fc,ob,trace)')
            for at, fc in ((start-timedelta(seconds=1),99), (start,1), (stop,2),
                           (stop+timedelta(seconds=1),99), (stamp(self.metadata['window_end']),99)):
                for site in self.stations:
                    for variable in ('t', 'rain24'):
                        db.execute('INSERT INTO pairs VALUES (?,?,?,?,?,?,?,?,?)',
                                   ('ec01',iso(at-timedelta(hours=24)),24,iso(at),site['id'],variable,fc,0,0))

    def clear(self):
        api.cached_json.cache_clear(); api.overview_cached.cache_clear()

    def save(self):
        (self.directory/'meta.json').write_text(json.dumps(self.metadata)); self.clear()

    def overview(self, region, element='t', days=3):
        return api.overview(dict(region=region, element=element, days=str(days)))

    def test_strict_majority_mixed_sources_empty_and_dynamic_membership(self):
        flags = api.window_metadata(self.metadata)['region_nmic_majority']
        self.assertEqual(flags, dict(all=False, hn=True, hun=False, gba=True, hk=False, sz=False, xj=True))
        self.assertFalse(api.nmic_majority('gba', []))
        stations = [dict(s) for s in self.stations]
        stations[0]['sources'] = ['ogimet']  # 2/5, no hardcoded hn/gba exception
        self.assertFalse(api.nmic_majority('hn', stations))
        half = [dict(id='a', sources=['nmic']), dict(id='b', sources=[])]
        self.assertFalse(api.nmic_majority('all', half))
        # 新疆站不属于南方地区的分母。
        # 南方地区（全部站点）不参与这条规则，即使国家站过半也照旧用最新资料（用户 2026-11-11 定）。
        self.assertFalse(api.nmic_majority('all', half[:1]+[dict(id='52112', sources=[])]))

    def test_effective_end_missing_older_equal_newer_and_other_regions(self):
        self.assertEqual(api.effective_window_end(self.metadata, 'hn'), stamp(self.metadata['nmic_end']))
        for value in (None, self.metadata['window_end'], '2026-11-12T00:00:00Z'):
            metadata = dict(self.metadata, nmic_end=value)
            self.assertEqual(api.effective_window_end(metadata, 'hn'), stamp(metadata['window_end']))
        metadata = {k:v for k,v in self.metadata.items() if k!='nmic_end'}
        self.assertEqual(api.effective_window_end(metadata, 'hn'), stamp(metadata['window_end']))
        self.assertEqual(api.effective_window_end(self.metadata, 'all'), stamp(metadata['window_end']))

    def test_adjusted_overview_exact_boundaries_temperature_and_rain(self):
        for region in ('hn', 'gba'):
            for element in ('t', 'rain'):
                result = self.overview(region, element)
                self.assertEqual((result['window_start'],result['window_end']),
                                 ('2026-11-05T18:00:00Z','2026-11-08T18:00:00Z'))
                self.assertEqual(result['end'],'2026-11-09T02:00:00')
                self.assertEqual(result['data_end_reason'],'nmic')
                self.assertEqual(result['station_count'],5)
                self.assertEqual(result['rows'][0]['n'],10)
                self.assertEqual(result['coverage_start'],result['window_start'])
                self.assertEqual(result['coverage_end'],result['window_end'])
                if element=='t': self.assertEqual(result['rows'][0]['MAE'],1.5)
                else: self.assertEqual(result['rows'][0]['F'],10)

    def test_unadjusted_results_every_value_unchanged_with_or_without_nmic_end(self):
        before = {(r,e,d):self.overview(r,e,d) for r in ('all','hk','sz')
                  for e in ('t','rain') for d in (3,7,15)}
        del self.metadata['nmic_end']; self.save()
        for (region,element,days), expected in before.items():
            self.assertEqual(self.overview(region,element,days),expected)
            self.assertNotIn('data_end_reason',expected)

    def test_old_snapshots_and_newer_nmic_do_not_adjust_overview(self):
        for value in (None, self.metadata['window_end'], '2026-11-12T00:00:00Z'):
            self.metadata['nmic_end'] = value; self.save()
            result = self.overview('hn')
            self.assertEqual(result['window_end'],self.metadata['window_end'])
            self.assertNotIn('data_end_reason',result)

    def test_meta_and_dataset_fields_follow_their_own_generation(self):
        (self.directory/'grid-datasets.json').write_text(json.dumps([dict(id='station-t',var='t')]))
        old = self.root/'snapshots/20261110T000000'; old.mkdir()
        older = dict(self.metadata, nmic_end='2026-11-07T18:00:00Z')
        (old/'meta.json').write_text(json.dumps(older))
        (old/'grid-datasets.json').write_text(json.dumps([dict(id='station-wind',var='wind')]))
        (old/'grid-rows-wind.json.gz').write_bytes(b'')
        self.assertEqual(api.meta()['nmic_end'],self.metadata['nmic_end'])
        self.assertTrue(api.meta()['region_nmic_majority']['hn'])
        datasets = api.grid_datasets()
        self.assertEqual([d['nmic_end'] for d in datasets], [self.metadata['nmic_end'],older['nmic_end']])
        self.assertEqual(datasets[1]['grid_snapshot'],old.name)
        del self.metadata['nmic_end']; self.save()
        self.assertIsNone(api.meta()['nmic_end'])

    def test_grid_rows_keeps_manual_dates_later_than_nmic(self):
        path = self.directory/'grid-rows-t.json.gz'
        rows = [dict(valid=d+'T08:00:00',lead=24) for d in ('2026-11-09','2026-11-10','2026-11-11')]
        with gzip.open(path,'wt') as handle: json.dump(rows,handle)
        actual = api.grid_rows(dict(dataset='station-气温-2m',start='2026-11-10',end='2026-11-11'))
        self.assertEqual(actual,rows[1:])


class PublishedWindow(unittest.TestCase):
    def test_producer_records_latest_selected_nmic_observation_and_none(self):
        import contextlib
        import io
        import numpy as np
        import station_score
        from station_obs import row
        end = stamp('2026-11-11T00:00:00Z'); run = end-timedelta(hours=6)
        station = dict(id='59287',name='test',type='synop',lat=23.,lon=113.,elevation=10,
                       sources=['ogimet','nmic'],province_code='44')
        observations = {('59287',iso(end),'t'):row('59287',iso(end),'t',20,'ogimet'),
                        ('59287',iso(end-timedelta(hours=3)),'t'):row('59287',iso(end-timedelta(hours=3)),'t',20,'nmic'),
                        ('59287',iso(end-timedelta(hours=6)),'t'):row('59287',iso(end-timedelta(hours=6)),'t',20,'nmic')}
        source = [('ec01',run,{6:{'t':np.array([21.])}},'test',[])]
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()), \
             patch.object(station_score,'forecast_runs',side_effect=lambda *a:iter(source)), \
             patch.object(station_score,'load_observations',side_effect=lambda *a:(observations,{'nmic':{'latest':iso(end)}},{})), \
             patch.object(station_score,'shenzhen_sanity',return_value={'n':0}):
            root = Path(directory)
            (root/'stations.json').write_text(json.dumps(dict(stations=[station])))
            result = station_score.refresh(root,end)
            metadata = json.loads((root/'snapshots'/result['generation']/'meta.json').read_text())
            self.assertEqual(metadata['nmic_end'],iso(end-timedelta(hours=3)))
            observations = {key:value for key,value in observations.items() if value['source']!='nmic'}
            result = station_score.refresh(root,end)
            metadata = json.loads((root/'snapshots'/result['generation']/'meta.json').read_text())
            self.assertIsNone(metadata['nmic_end'])


if __name__ == '__main__': unittest.main()
