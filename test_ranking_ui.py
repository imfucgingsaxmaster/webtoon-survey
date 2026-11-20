import os
from pathlib import Path
import unittest
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
from test_rankings import point

class RankingUITests(unittest.TestCase):
    def test_period_metric_switch_and_escaped_detail_link(self):
        work={'work_id':'webtoon:1','title':'작품 <안전>','status':'연재','cover_url':'','metrics':{},
              'metric_sources':{'downloads':{'platform':'네이버시리즈','title_id':'2'},
                                'interest':{'platform':'네이버웹툰','title_id':'1'}}}
        snapshot={'titles':[],'observed_at':'2026-11-20T01:00:00Z'}
        metadata={'webtoon':{},'series':{}}
        index={'sources':{'series:2':{'downloads':[point(13,100),point(19,130),point(20,160)]},
                          'webtoon:1':{'interest':[point(13,1000),point(19,1005),point(20,1010)]}}}
        with patch.dict(os.environ,{'WEBTOON_RADAR_OFFLINE':'1'}),\
             patch('dashboard_data.dataset',return_value=(snapshot,metadata,[work])),\
             patch('dashboard_data.background_sync',return_value={'state':'offline','updated':False}),\
             patch('ranking_ui.load_index',return_value=index):
            app=AppTest.from_file(str(Path(__file__).with_name('app.py')),default_timeout=30)
            app.query_params['shelf']='rankings';app.run()
            self.assertFalse(app.exception,str(app.exception))
            self.assertEqual(app.metric[0].value,'1개')
            table=next(m.value for m in app.markdown if '<table>' in m.value)
            self.assertIn('작품 &lt;안전&gt;',table);self.assertIn('work=webtoon%3A1',table)
            self.assertIn('+60',table)
            app.radio(key='rank_period').set_value('일일').run()
            self.assertFalse(app.exception,str(app.exception))
            self.assertEqual(app.query_params['rank_period'],'1')
            app.radio(key='rank_metric').set_value('관심 수 증가량').run()
            self.assertFalse(app.exception,str(app.exception))
            table=next(m.value for m in app.markdown if '<table>' in m.value)
            self.assertIn('+5',table);self.assertIn('+10',table)
            self.assertEqual(app.query_params['rank_metric'],'interest')

if __name__=='__main__':unittest.main()
