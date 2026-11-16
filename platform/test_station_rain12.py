import unittest

import station_obs as obs


def rain(station, at, value, **extra):
    return {'station': station, 'time': at, 'var': 'rain6', 'value': value, 'source': 'ogimet', 'flags': [], **extra}


class Rain12(unittest.TestCase):
    def test_adds_two_consecutive_six_hour_totals(self):
        selected = {('1', r['time'], 'rain6'): r for r in
                    (rain('1', '2026-11-03T00:00:00Z', 1.5), rain('1', '2026-11-03T06:00:00Z', 2.5))}
        result = obs.derive_rain12(selected)
        item = result['1', '2026-11-03T06:00:00Z', 'rain12']
        self.assertEqual((item['value'], item['hours'], item['var']), (4., 12, 'rain12'))
        self.assertNotIn(('1', '2026-11-03T00:00:00Z', 'rain12'), result)

    def test_a_missing_half_leaves_twelve_hours_missing(self):
        selected = {('1', r['time'], 'rain6'): r for r in
                    (rain('1', '2026-11-03T00:00:00Z', 1.), rain('1', '2026-11-03T12:00:00Z', 1.))}
        self.assertFalse([k for k in obs.derive_rain12(selected) if k[2] == 'rain12'])

    def test_trace_needs_both_halves(self):
        selected = {('1', r['time'], 'rain6'): r for r in
                    (rain('1', '2026-11-03T00:00:00Z', 0., trace=True), rain('1', '2026-11-03T06:00:00Z', 1.))}
        self.assertFalse(obs.derive_rain12(selected)['1', '2026-11-03T06:00:00Z', 'rain12']['trace'])


if __name__ == '__main__':
    unittest.main()
