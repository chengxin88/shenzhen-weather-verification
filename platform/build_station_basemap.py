"""Build the bundled WGS84 station map from locally installed Natural Earth data.

Usage: python build_station_basemap.py --natural-earth /path/to/natural_earth
Only this offline step needs Cartopy/Shapely; serving station metadata uses JSON.
"""
import argparse
import json
from contextlib import closing
from pathlib import Path

from cartopy.io.shapereader import Reader
from shapely.geometry import box

from station_common import BOX, ROOT, XJ_BOUNDS, CMAGD_BOX

# Map, station selection and scoring share one extent, including the 27 N cap.
MAP_BOX = BOX
# Lines also reach the Xinjiang view; the plot clips them to whichever frame is showing.
LINE_BOX = (88., BOX[1], BOX[2], 47.)
# Pearl River Delta / Hong Kong detail layers for zoomed maps (~200 m tolerance).
DETAIL_BOX = (112.8, 21.6, 115.2, 23.3)
TOLERANCE, DETAIL_TOLERANCE = .015, .002


def lines(geometry):
    if geometry.is_empty:
        return
    if geometry.geom_type == 'LineString':
        points = [[round(x, 5), round(y, 5)] for x, y in geometry.coords]
        if len({tuple(p) for p in points}) >= 2:
            yield points
    elif hasattr(geometry, 'geoms'):
        for part in geometry.geoms:
            yield from lines(part)


def build(source):
    result = dict(map_bounds=list(MAP_BOX), station_bounds=list(BOX), map_source='Natural Earth 1:10m / WGS84',
                  detail_bounds=list(DETAIL_BOX), xj_bounds=list(XJ_BOUNDS),
                  xj_landmarks=[dict(name='巴里坤3风电场', lon=94.2, lat=44.4)])
    result.update(layers(source, box(*LINE_BOX), TOLERANCE))
    result.update({k + '_detail': v for k, v in layers(source, box(*DETAIL_BOX), DETAIL_TOLERANCE).items()})
    result.update({k + '_cmagd': v for k, v in layers(source, box(*CMAGD_BOX), TOLERANCE).items()})
    result['cmagd_bounds'] = list(CMAGD_BOX)
    return result


def layers(source, extent, tolerance):
    result = dict(coastline=[], country_boundaries=[], province_boundaries=[])
    sources = [('coastline', 'physical/ne_10m_coastline.shp'),
               ('country_boundaries', 'cultural/ne_10m_admin_0_boundary_lines_land.shp'),
               ('province_boundaries', 'cultural/ne_10m_admin_1_states_provinces_lines.shp')]
    for layer, filename in sources:
        with closing(Reader(source / filename)) as reader:
            for record in reader.records():
                if record.geometry is None or not record.geometry.intersects(extent):
                    continue
                if layer == 'province_boundaries' and record.attributes['ADM0_A3'] != 'CHN':
                    continue
                target = layer
                if layer == 'country_boundaries':
                    # Hong Kong/Macao boundaries are internal administrative lines.
                    if record.attributes['SOV_A3_L'] == record.attributes['SOV_A3_R']:
                        target = 'province_boundaries'
                # Clip LINE geometry, never polygon exteriors: clipping must not
                # invent a rectangular coastline along the viewport's edges.
                clipped = record.geometry.intersection(extent).simplify(tolerance)
                result[target].extend(lines(clipped))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--natural-earth', required=True, type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'station-basemap.json')
    args = parser.parse_args()
    args.output.write_text(json.dumps(build(args.natural_earth), separators=(',', ':')) + '\n')
    print(args.output)
