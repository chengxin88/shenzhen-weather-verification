from datetime import timedelta
import importlib
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import urlopen
from urllib.error import HTTPError

import numpy as np
import pandas as pd
from scipy.interpolate import RegularGridInterpolator
import module_demo as demo


class Metadata(unittest.TestCase):
    def test_unique_modes_and_explicit_source(self):
        data=demo.catalog();profiles=data['profiles']
        self.assertEqual(len(profiles),12)
        self.assertEqual(len({p['id'] for p in profiles}),12)
        self.assertEqual(sum(p['label']=='PANGU' for p in profiles),1)
        self.assertTrue(any(p['label']=='CMA-TRAMS' for p in profiles))
        for p in profiles:
            self.assertIs(p['is_demo'],True);self.assertIs(p['excluded_from_official_scores'],True)
            self.assertEqual(p['source_model'],demo.WN3)
        self.assertIn('高空',data['unavailable']['circulation'])
        self.assertIn('路径',data['unavailable']['typhoon'])

    def test_month_time_identity_and_missing_days(self):
        from model_interfaces import utc
        data=demo.read_month('2026-10')
        self.assertIs(data['is_demo'],True);self.assertIs(data['excluded_from_official_scores'],True)
        self.assertEqual(data['source_model'],demo.WN3)
        for w in data['windows']:
            model=w['models'][demo.WN3]
            self.assertEqual(model['days'],sum(d['mean'] is not None for d in model['daily']))
            self.assertEqual(model['days'],w['paired_obs']['days'])
            for day,ob in zip(model['daily'],w['paired_obs']['daily']):
                if day['mean'] is None:
                    self.assertEqual(day['scores'],{});self.assertIsNone(ob['mean']);continue
                run,valid=utc(day['run_utc']),utc(day['valid_utc'])
                self.assertEqual(valid,run+timedelta(hours=24 if w['id']=='d1' else 36))
                self.assertEqual(valid.hour,12)
                self.assertEqual(valid.strftime('%Y-%m-%d'),day['day'])
                self.assertAlmostEqual(day['obs_mean'],ob['mean'],places=12)
                self.assertGreater(day['paired_points'],0)
            self.assertAlmostEqual(model['obs_mean_total'],w['paired_obs']['mean_total'],places=12)
            self.assertGreater(len(model['missing']),0)

    def test_month_scientific_counts_from_same_run_hourly_fields(self):
        import native_archive as native
        from monthly_precip import counts,scores
        data=demo.read_month('2026-10');w=data['windows'][0]
        day=next(d for d in w['models'][demo.WN3]['daily'] if d['mean'] is not None)
        provenance=json.loads((demo.ROOT/'2026-10.provenance.json').read_text())
        paths=provenance['hourly_fields'][w['id']+':'+day['day']]
        self.assertEqual(len(paths),24)
        run=native.stamp(day['run_utc']);directory=Path(paths[0]).parent.parent.name
        arrays=[]
        for lead,path in enumerate(paths,1):
            self.assertEqual(Path(path).parent.name,run.strftime('%Y%m%d%H'))
            self.assertEqual(Path(path).name,f'f{lead:03}_rain1h.npz')
            x,y,array=native.load_wn3_field(Path(path),run,lead,'rain1h',directory);arrays.append(array)
        total=sum(arrays,np.zeros_like(arrays[0],dtype=float))
        app=Path('/home/chengyang/ec-gfs-server')
        grid=pd.read_excel(app/'data/grid.xlsx',sheet_name='数据集1').rename(columns={'格网ID（唯一）':'GRIDID','格网左下角经度（度）':'west','格网右上角经度（度）':'east','格网左下角纬度（度）':'south','格网右上角纬度（度）':'north'})
        for key in ['GRIDID','west','east','south','north']:grid[key]=pd.to_numeric(grid[key],errors='coerce')
        grid=grid.dropna(subset=['GRIDID','west','east','south','north']);grid.GRIDID=grid.GRIDID.astype(int)
        grid['lon']=(grid.west+grid.east)/2;grid['lat']=(grid.south+grid.north)/2
        from shapely.geometry import shape,Point
        from shapely.ops import unary_union
        city=unary_union([shape(f['geometry']) for f in json.loads((app/'data/shenzhen_streets.geojson').read_text())['features']])
        inside=np.array([city.covers(Point(float(a),float(b))) for a,b in zip(grid.lon,grid.lat)])
        obs=pd.read_pickle(provenance['observation_files'][day['day']])
        observed=pd.to_numeric(obs.drop_duplicates('GRIDID').set_index('GRIDID').RAIN24H,errors='coerce').reindex(grid.GRIDID).to_numpy(dtype=float,copy=True);observed[observed<0]=np.nan
        forecast=RegularGridInterpolator((y,x),total,method='nearest',bounds_error=False,fill_value=np.nan)(grid[['lat','lon']].to_numpy())
        mask=inside&np.isfinite(forecast)&np.isfinite(observed)
        self.assertEqual(day['paired_points'],int(mask.sum()))
        self.assertAlmostEqual(day['mean'],float(forecast[mask].mean()),places=10)
        self.assertAlmostEqual(day['obs_mean'],float(observed[mask].mean()),places=10)
        for t in data['thresholds']:
            c=counts(forecast[mask],observed[mask],t['value'])
            self.assertEqual(day['scores'][f"{t['value']:g}"],dict(c,**scores(c)))

    def test_month_integrity_and_private_provenance(self):
        saved=demo.ROOT
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            for extension in ['json','sha256']:
                shutil.copyfile(saved/('2026-10.'+extension),root/('2026-10.'+extension))
            try:
                demo.ROOT=root
                with (root/'2026-10.json').open('ab') as handle:handle.write(b' ')
                with self.assertRaises(ValueError):demo.read_month('2026-10')
            finally:demo.ROOT=saved
        self.assertNotIn('hourly_fields',demo.read_month('2026-10'))
        for bad in ['../2026-10','2026-10.provenance',None]:
            with self.assertRaises(ValueError):demo.read_month(bad)


class HTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server=importlib.import_module('server');cls.http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        cls.base=f'http://127.0.0.1:{cls.http.server_port}/Verification/test'
        cls.thread=threading.Thread(target=cls.http.serve_forever,daemon=True);cls.thread.start()
    @classmethod
    def tearDownClass(cls):cls.http.shutdown();cls.http.server_close();cls.thread.join()
    def read(self,path):
        try:
            with urlopen(self.base+path,timeout=10) as response:return response.status,response.read()
        except HTTPError as exc:return exc.code,exc.read()
    def test_routes_and_static_assets(self):
        for path in ['/demo-models.js','/demo-models.css','/api/wn3-module-demo/catalog','/api/wn3-module-demo/monthly/catalog','/api/wn3-module-demo/monthly/data?month=2026-10']:
            self.assertEqual(self.read(path)[0],200)
        self.assertEqual(self.read('/api/wn3-module-demo/monthly/data?month=x')[0],400)
        self.assertEqual(self.read('/api/wn3-module-demo/monthly/data?month=x&month=y')[0],400)
        self.assertEqual(self.read('/cache/wn3-module-demo/2026-10.provenance.json')[0],404)
        status,raw=self.read('/api/model-interfaces');catalog=json.loads(raw)
        self.assertEqual(sum(p['status']=='reserved' for p in catalog['products']),13)
        page=self.read('/')[1].decode();self.assertLess(page.index('demo-models.js'),page.index('./app.js'))


if __name__=='__main__':unittest.main()
