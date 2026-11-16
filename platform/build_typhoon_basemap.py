#!/usr/bin/env python3
"""Build typhoon-basemap.json (Natural Earth, west Pacific) for the single-issue track map."""
import json
from pathlib import Path

from cartopy.io import shapereader
from shapely.geometry import box, mapping
from shapely.ops import unary_union

BOX = box(95, -2, 180, 58)


def lines(geometry, tolerance, digits=2):
    shape = geometry.intersection(BOX).simplify(tolerance)
    result = []
    def add(item):
        coords = [[round(x, digits), round(y, digits)] for x, y in item.coords]
        if len(coords) > 1: result.append(coords)
    def walk(item):
        kind = item.geom_type
        if kind == 'Polygon':
            add(item.exterior)
            for ring in item.interiors: add(ring)
        elif kind == 'LineString': add(item)
        elif kind.startswith('Multi') or kind == 'GeometryCollection':
            for part in item.geoms: walk(part)
    walk(shape)
    return result


def read(resolution, category, name):
    return shapereader.Reader(shapereader.natural_earth(resolution=resolution, category=category, name=name))


land = unary_union(list(read('50m', 'physical', 'land').geometries()))
countries = unary_union(list(read('50m', 'cultural', 'admin_0_boundary_lines_land').geometries()))
provinces = [r.geometry for r in read('10m', 'cultural', 'admin_1_states_provinces_lakes').records() if r.attributes.get('adm0_a3') == 'CHN']
data = dict(source='Natural Earth 1:50m/1:10m / WGS84', land=lines(land, .03), countries=lines(countries, .03),
            provinces=[line for line in lines(unary_union([p.boundary for p in provinces]), .04) if len(line) >= 6])
out = Path(__file__).with_name('typhoon-basemap.json')
out.write_text(json.dumps(data, separators=(',', ':')))
print(out.stat().st_size, {k: len(v) for k, v in data.items() if isinstance(v, list)})
