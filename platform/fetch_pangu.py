#!/usr/bin/env python3
"""Cut NOAA/CIRA Pangu-Weather (IFS initial conditions) surface fields to a west Pacific box.

Each source file is 2-5 GB, but one variable at one lead is a single HDF5 chunk, so only
the leads asked for are transferred. Runs in its own venv (h5py is not in the server venv).
"""
import argparse, os, time
from pathlib import Path

import fsspec
import h5py
import numpy as np

BASE = 'https://noaa-oar-mlwp-data.s3.amazonaws.com/PANG_v100_IFS/{y}/{md}/PANG_v100_IFS_{run}_f000_f240_06.nc'
OUT = Path(__file__).resolve().parent/'cache/native-backfill/pangu-tc'
LON = (95., 180.)
LAT = (0., 55.)
STEP = 6


def fetch(run, lead_max):
    path = OUT/f'pangu_tc_{run}.npz'
    if path.exists():
        with np.load(path, allow_pickle=False) as z:
            if int(z['leads'].max()) >= lead_max: return 'skip'
    url = BASE.format(y=run[:4], md=run[4:8], run=run)
    handle = fsspec.open(url, block_size=1 << 20, cache_type='readahead', client_kwargs={'trust_env': True}).open()
    with h5py.File(handle, 'r') as f:
        for key, value in (('model_name', 'panguweather'), ('initialization_model', 'IFS'), ('forecast_hour_step', str(STEP))):
            found = f.attrs[key]
            if (found.decode() if isinstance(found, bytes) else str(found)) != value: raise ValueError(f'unexpected {key}')
        lat, lon = f['latitude'][:], f['longitude'][:]
        rows = np.flatnonzero((lat >= LAT[0]) & (lat <= LAT[1])); cols = np.flatnonzero((lon >= LON[0]) & (lon <= LON[1]))
        y0, y1, x0, x1 = rows[0], rows[-1]+1, cols[0], cols[-1]+1
        leads = np.arange(0, lead_max+1, STEP)
        fields = {name: np.stack([f[name][lead//STEP, y0:y1, x0:x1] for lead in leads]).astype(np.float32) for name in ('msl', 'u10', 'v10')}
    fields['msl'] /= 100
    order = np.argsort(lat[y0:y1])
    OUT.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    with temp.open('wb') as out:
        np.savez_compressed(out, lon=lon[x0:x1].astype(float), lat=lat[y0:y1][order].astype(float), leads=leads, run=np.array(run),
                            **{k: v[:, order, :] for k, v in fields.items()})
    os.replace(temp, path)
    return 'ok'


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('run', help='YYYYMMDDHH, 00Z or 12Z')
    parser.add_argument('--lead-max', type=int, default=72)
    args = parser.parse_args()
    started = time.time(); print(args.run, fetch(args.run, args.lead_max), f'{time.time()-started:.0f}s', flush=True)
