import unittest
from datetime import datetime,timedelta,timezone
from unittest import mock

import numpy as np
import pandas as pd

import native_archive as native


class WN3OneHourRain(unittest.TestCase):
    def test_scores_the_matching_hour_against_rain01h(self):
        run=datetime(2026,11,4,0,tzinfo=timezone.utc)
        x=np.array([113.,114.,115.]);y=np.array([22.,23.])
        field=np.full((2,3),2.)
        grid=pd.DataFrame({'GRIDID':[1,2],'lon':[114.,114.],'lat':[22.,23.]})
        # RAIN03H would give a different answer, so this also proves the column.
        obs=pd.DataFrame({'GRIDID':[1,2],'RAIN01H':[5.,0.],'RAIN03H':[0.,5.]})
        with mock.patch.object(native,'load_wn3_field',return_value=(x,y,field)):
            rows=native.score_wn3_rain1h('unused',run,6,'native-southern-china-v2',grid,obs,1.)
        self.assertEqual([r['threshold'] for r in rows],native.THRESHOLDS)
        self.assertEqual({r['dataset'] for r in rows},{'hk-降水-1'})
        self.assertEqual(rows[0]['valid'],(run+timedelta(hours=6)).astimezone(native.BJT).isoformat()[:19])
        one=next(r for r in rows if r['threshold']==1)
        self.assertEqual((one['H'],one['M'],one['F'],one['n']),(1,0,1,2))
        three=next(r for r in rows if r['threshold']==3)
        self.assertEqual((three['H'],three['M'],three['F']),(0,1,0))

    def test_missing_observations_are_not_scored(self):
        run=datetime(2026,11,4,0,tzinfo=timezone.utc)
        x=np.array([113.,114.,115.]);y=np.array([22.,23.])
        grid=pd.DataFrame({'GRIDID':[1],'lon':[114.],'lat':[22.]})
        obs=pd.DataFrame({'GRIDID':[1],'RAIN01H':[-1.]})
        with mock.patch.object(native,'load_wn3_field',return_value=(x,y,np.ones((2,3)))):
            self.assertEqual(native.score_wn3_rain1h('unused',run,6,'native-southern-china-v2',grid,obs,1.),[])


if __name__=='__main__':
    unittest.main()
