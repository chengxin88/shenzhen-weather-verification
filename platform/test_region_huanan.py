"""华南地区（粤桂琼港澳）站点筛选。"""
import unittest
from unittest.mock import patch

import station_api


def site(ident, lon=113.0, lat=23.0):
    return dict(id=ident, name=ident, lon=lon, lat=lat)


class HuananRegion(unittest.TestCase):
    def test_huanan_keeps_south_china_and_drops_neighbours(self):
        stations = [site(i) for i in ('59287', '59431', '59948', '57957', '59117', '45007', '45011', 'SZ-FT', 'ZGSZ',
                                      '58926', '59102', '57972', '59007', '57816', '52101', 'ZUGY')]
        with patch.object(station_api, 'meta', return_value=dict(stations=stations)):
            got = set(station_api.region_stations('hn'))
        self.assertEqual(got, {'59287', '59431', '59948', '57957', '59117', '45007', '45011', 'SZ-FT', 'ZGSZ'})

    def test_fixed_list_has_41_ids_plus_hk_macau_and_sz_prefixes(self):
        self.assertEqual(len(station_api.HUANAN_IDS), 41)


if __name__ == '__main__':
    unittest.main()
