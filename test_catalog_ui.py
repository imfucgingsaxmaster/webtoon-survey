import copy
import gzip
import json
import tempfile
import unittest
from pathlib import Path
import radar,works,trends,catalog_ui
import tests as fixtures

class DiscoveryTests(unittest.TestCase):
    def test_official_new_is_distinct_from_episode_update(self):
        item={'titleId':1,'titleName':'테스트','new':False,'up':True,'viewCount':0}
        row=radar.normalize(item)
        self.assertFalse(row['is_new']);self.assertIsNone(row['public_views'])
        item.update(new=True,viewCount=123,thumbnailUrl='https://image-comic.pstatic.net/webtoon/1/cover.jpg')
        row=radar.normalize(item)
        self.assertTrue(row['is_new']);self.assertEqual(row['public_views'],123)
        snap=radar.collect(fixtures.FakeClient());snap['titles'][0].update(row)
        result=works.build_works(snap,{})
        self.assertIn('신작',next(w for w in result if w['webtoon_id']=='1')['tags'])
        self.assertEqual(len(works.filter_works(result,tags=['신작'])),1)
        item['new']='true';self.assertFalse(radar.normalize(item)['is_new'])

    def test_shelves_are_disjoint_and_cards_escape_markup(self):
        snap=radar.collect(fixtures.FakeClient());snap['titles'][0].update(title='<script>alert(1)</script>',status='완결',cover_url='javascript:bad')
        rows=works.build_works(snap,{})
        active=catalog_ui.shelf_works(rows);finished=catalog_ui.shelf_works(rows,'finished')
        self.assertEqual(len(active)+len(finished),len(rows))
        self.assertFalse({r['work_id'] for r in active}&{r['work_id'] for r in finished})
        markup=catalog_ui.card_grid(finished,{'q':'a&b'})
        self.assertNotIn('<script>',markup);self.assertNotIn('javascript:',markup)
        self.assertIn('work=webtoon%3A1',markup);self.assertIn('q=a%26b',markup)

    def test_history_uses_observation_times_deduplicates_and_keeps_source_identity(self):
        snap=radar.collect(fixtures.FakeClient());snap['titles'][0]['title']='같은 제목'
        before=fixtures.MetadataTests().webtoon(count=100)
        after=copy.deepcopy(before);after.update(observed_at='2026-10-16T01:00:00+00:00')
        after['metrics']['interest']['value']=120;after['metrics']['interest']['display']='120'
        cache={'webtoon':{'1':after},'series':{}}
        work=next(w for w in works.build_works(snap,cache) if w['webtoon_id']=='1')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'metadata_history').mkdir()
            old={'updated_at':'2026-10-16T02:00:00+00:00','webtoon':{'1':before}}
            for name in ('a','b'):(root/'metadata_history'/(name+'.json.gz')).write_bytes(gzip.compress(json.dumps(old).encode()))
            history=trends.metric_history(work,cache,snap,root)
        self.assertEqual([r['value'] for r in history['interest']],[100,120])
        self.assertEqual(history['interest'][0]['observed_at'],before['observed_at'])
        self.assertEqual(history['views'],[]);self.assertEqual(history['downloads'],[])

if __name__=='__main__':unittest.main()
