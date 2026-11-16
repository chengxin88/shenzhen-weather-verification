"""湖南地区（国家站省码 43）站点筛选，以及湖南站实况以国家站为准。"""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import station_api
import station_obs


def site(ident, province=None, lon=112.0, lat=28.0):
    return dict(id=ident, name=ident, lon=lon, lat=lat, type='synop', **({'province_code': province} if province else {}))


class HunanRegion(unittest.TestCase):
    def test_hunan_is_province_43_only(self):
        stations = [site('57662', '43'), site('57687', '43'), site('59287', '44'), site('57957', '45'), site('57816'), site('SZ-FT')]
        with patch.object(station_api, 'meta', return_value=dict(stations=stations)):
            self.assertEqual(set(station_api.region_stations('hun')), {'57662', '57687'})
            self.assertFalse({'57662', '57687'} & set(station_api.region_stations('hn')))

    def test_region_is_accepted_everywhere(self):
        self.assertIn('hun', station_api.OVERVIEW_REGIONS)
        self.assertEqual(station_api.OVERVIEW_REGIONS.index('hun'), station_api.OVERVIEW_REGIONS.index('hn')+1)
        stations = [dict(site('57662', '43'), sources=['ogimet', 'nmic']), dict(site('57687', '43'), sources=['nmic']), dict(site('57816'), sources=['ogimet'])]
        self.assertTrue(station_api.nmic_majority('hun', stations))

    def test_province_table_has_hunan(self):
        provinces = json.loads((Path(station_api.__file__).parent/'nmic_provinces.json').read_text(encoding='utf-8'))
        hunan = [i for i, code in provinces.items() if code == '43']
        self.assertEqual(len(hunan), 84)
        self.assertTrue({'57662', '57584', '57687', '57972'} <= set(hunan))
        self.assertEqual(sum(code in ('44', '45', '46') for code in provinces.values()), 176)

    def test_hunan_national_station_value_wins(self):
        stations = [site('57662', '43'), site('57816')]
        row = lambda station, source, value: dict(station=station, time='2026-11-10T06:00:00Z', var='wind', value=value, source=source, flags=[])
        rows = [row('57662', 'ogimet', 3.0), row('57662', 'nmic', 2.4), row('57816', 'ogimet', 3.0), row('57816', 'nmic', 2.4)]
        chosen = station_obs.select_observations(rows, stations)
        self.assertEqual(chosen[('57662', '2026-11-10T06:00:00Z', 'wind')]['source'], 'nmic')
        self.assertEqual(chosen[('57816', '2026-11-10T06:00:00Z', 'wind')]['source'], 'ogimet')


if __name__ == '__main__':
    unittest.main()
