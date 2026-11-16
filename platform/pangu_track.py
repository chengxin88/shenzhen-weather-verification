"""Follow a cyclone through regional Pangu-Weather fields (see fetch_pangu.py)."""
from datetime import datetime, timedelta, timezone

import numpy as np

FIRST_RADIUS = 7.0   # degrees; the anchor is the observed fix, up to 12 h after the run
NEXT_RADIUS = 5.0    # degrees in 6 h, far above any real motion
WIND_RADIUS = 3.0


def distance(lon, lat, x, y):
    return np.hypot((lon-x)*np.cos(np.radians(y)), lat-y)


def follow(lon, lat, msl, speed, x, y, radius):
    """Pressure minimum near (x, y); None when no closed low remains inside the search circle."""
    grid_x, grid_y = np.meshgrid(lon, lat); near = distance(grid_x, grid_y, x, y)
    field = np.where(near <= radius, msl, np.nan)
    if not np.isfinite(field).any(): return None
    j, i = np.unravel_index(np.nanargmin(field), field.shape)
    if near[j, i] > radius-.5: return None
    if j in (0, len(lat)-1) or i in (0, len(lon)-1): return None  # left the data box
    wind = speed[distance(grid_x, grid_y, lon[i], lat[j]) <= WIND_RADIUS]
    return float(lon[i]), float(lat[j]), float(msl[j, i]), float(np.nanmax(wind))


def track(frame, run, x, y, lead_max):
    """Rows of lead/time/lon/lat/pressure/wind from an fetch_pangu.py npz mapping."""
    start = datetime.strptime(run, '%Y%m%d%H').replace(tzinfo=timezone.utc)
    lon, lat = frame['lon'], frame['lat']; rows = []
    for index, lead in enumerate(int(v) for v in frame['leads']):
        if lead > lead_max: break
        speed = np.hypot(frame['u10'][index], frame['v10'][index])
        found = follow(lon, lat, frame['msl'][index], speed, x, y, NEXT_RADIUS if rows else FIRST_RADIUS)
        if found is None: break
        x, y, pressure, wind = found
        rows.append(dict(lead=lead, time=start+timedelta(hours=lead), lon=x, lat=y, pressure=pressure, wind=wind))
    return rows
