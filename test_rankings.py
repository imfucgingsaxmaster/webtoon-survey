import unittest
import json
import tempfile
from pathlib import Path
from rankings import daily_points,growth_rankings,available_dates
from datetime import date

def work(ident,status='연재',source=None):
    return {'work_id':'webtoon:'+ident,'title':'작품 '+ident,'status':status,'metrics':{},
            'metric_sources':{'downloads':{'platform':'네이버시리즈','title_id':source or ident}}}

def point(day,value,approximate=False):
    return [f'2026-11-{day:02}T01:00:00+00:00',value,str(value),approximate]

class RankingTests(unittest.TestCase):
    def test_profile_only_downloads_survive_the_next_daily_refresh(self):
        from history_index import build_index
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for day,value in [(19,100),(20,130)]:
                data={'series':{'1':{'metrics':{}}},'profiles':{'webtoon:1':{'platform':'네이버시리즈',
                      'title_id':'1','observed_at':point(day,value)[0],
                      'download_check':{'value':value,'display':str(value),'approximate':False}}}}
                (root/'metadata_latest.json').write_text(json.dumps(data))
                index=build_index(root)
            result=growth_rankings([work('1')],index,'2026-11-20',days=1)
            self.assertEqual(result['rows'][0]['daily']['delta'],30)
    def test_latest_comparable_date_is_explicit_without_filling_missing_days(self):
        index={'sources':{'series:1':{'downloads':[point(9,100),point(16,120),point(20,130)]}}}
        self.assertEqual(available_dates([work('1')],index,date(2026,11,20),'downloads',7),[date(2026,11,16)])
        self.assertEqual(available_dates([work('1')],index,date(2026,11,20),'downloads',1),[])
    def test_daily_and_weekly_are_endpoint_deltas_not_sum_of_snapshots(self):
        index={'sources':{'series:1':{'downloads':[point(13,100),point(19,130),point(20,150),
            ['2026-11-20T02:00:00.123Z',160,'160',False]]}}}
        result=growth_rankings([work('1')],index,'2026-11-20')
        row=result['rows'][0]
        self.assertEqual((row['daily']['delta'],row['weekly']['delta']),(30,60))
        self.assertEqual(row['weekly']['growth'],60)

    def test_missing_exact_day_and_stale_current_never_become_zero(self):
        index={'sources':{'series:1':{'downloads':[point(16,100),point(20,150)]},
                          'series:2':{'downloads':[point(12,100),point(19,150)]}}}
        result=growth_rankings([work('1'),work('2')],index,'2026-11-20')
        self.assertEqual(result['rows'],[])
        self.assertEqual([r['comparison']['state'] for r in result['missing']],['missing_baseline','missing_current'])

    def test_korean_dates_and_offsets_pick_actual_latest_observation(self):
        rows=daily_points([['2026-11-19T16:00:00Z',10,'10',False],
                           ['2026-11-20T00:30:00+09:00',9,'9',False],
                           ['2026-11-20T01:10:00.001+09:00',11,'11',False]])
        self.assertEqual(len(rows),1)
        self.assertEqual(next(iter(rows.values()))['value'],11)

    def test_same_source_only_completed_excluded_zero_negative_and_ties_retained(self):
        source={f'series:{i}':{'downloads':[point(13,a),point(20,b)]}
                for i,a,b in [('1',100,120),('2',0,20),('3',50,45),('4',10,1000),('old',10,1000)]}
        result=growth_rankings([work('1'),work('2'),work('3'),work('4','완결'),work('5',source='new')],
                               {'sources':source},'2026-11-20')
        self.assertEqual([r['rank'] for r in result['rows']],[1,1,3])
        self.assertIsNone(result['rows'][1]['comparison']['growth'])
        self.assertEqual(result['rows'][2]['comparison']['delta'],-5)
        self.assertEqual(len(result['missing']),1)

    def test_current_metadata_fills_index_lag_and_keeps_approximation(self):
        w=work('1');w['metric_sources']['downloads']['observed_at']='2026-11-20T03:00:00Z'
        w['metrics']['downloads']={'value':12000,'display':'1.2만','approximate':True}
        result=growth_rankings([w],{'sources':{'series:1':{'downloads':[point(13,11000)]}}},'2026-11-20')
        self.assertEqual(result['rows'][0]['weekly']['delta'],1000)
        self.assertTrue(result['rows'][0]['weekly']['approximate'])
        self.assertIsNone(result['rows'][0]['daily']['delta'])

if __name__=='__main__':unittest.main()
