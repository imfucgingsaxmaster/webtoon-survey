import copy
import csv
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
import radar,metadata,works,live_data,trends,history_index,publishers,notion_export
from collection_policy import frozen_ids
import tests as fixtures

class UpgradeTests(unittest.TestCase):
    def test_completed_catalogue_is_frozen_even_when_list_still_contains_it(self):
        before=radar.collect(fixtures.FakeClient())
        before['titles'][0].update(status='완결',title='보관된 제목',public_views=123)
        calls=[]
        class Client(fixtures.FakeClient):
            def get(self,path,**params):
                calls.append((path,params));return super().get(path,**params)
        after=radar.collect_active_catalogue(Client(),before)
        expected=dict(before['titles'][0],catalog_observed_at=before['observed_at'])
        self.assertEqual(after['titles'][0],expected)
        self.assertEqual(len(calls),2)
        self.assertFalse(any('page' in p for _,p in calls))
        self.assertEqual(len({(m['title_id'],m['group']) for m in after['memberships']}),len(after['memberships']))

    def test_completed_metadata_and_on_demand_are_never_fetched(self):
        snapshot=radar.collect(fixtures.FakeClient());snapshot['titles'][0]['status']='완결'
        old=fixtures.MetadataTests().webtoon();old['contents_no']='99'
        serial=fixtures.MetadataTests().series();serial.update(original_product_id='99',status='연재')
        cache={'schema_version':1,'webtoon':{'1':old},'series':{'1':serial},'series_checks':{},'runs':{}}
        self.assertIn('1',frozen_ids(snapshot,cache)['series'])
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);live_data.write_json(root/'catalog_latest.json',snapshot);live_data.write_json(root/'metadata_latest.json',cache)
            with patch.object(live_data,'PublicClient',side_effect=AssertionError('must not fetch')):
                self.assertEqual(live_data.fill_searched_titles(['1'],root)['fetched'],0)
                self.assertEqual(live_data.fill_series_links(['1'],root)['checked'],0)
            calls=[]
            class Client:
                def get(self,path,**params):
                    calls.append(str(params['titleId']));return {'titleId':int(params['titleId']),'titleName':'Test','favoriteCount':1}
            with patch.object(metadata,'PublicClient',return_value=Client()):
                result=metadata.collect_metadata(root,series_limit=0,refresh_since='2026-10-20T00:00:00+00:00')
            self.assertNotIn('1',calls);self.assertEqual(result['webtoon']['1'],old)
            self.assertEqual(result['runs']['webtoon']['target'],9)

    def test_publisher_uses_exact_label_and_weekdays_are_official(self):
        page='''<div id="content"><div class="end_head"><h2>작품</h2><a id="spiButton2" data-url="https://series.naver.com/comic/detail.series?productNo=50"></a></div>
        <ul class="end_info"><li class="info_lst"><ul><li><span>글</span><a>작가</a></li>
        <li><span>출판사</span><a>REDICE STUDIO</a></li></ul></li></ul></div>'''
        row=metadata.parse_series(page,'50')
        self.assertEqual(row['publisher'],'REDICE STUDIO');self.assertEqual(row['publisher_label'],'출판사')
        self.assertEqual(metadata.parse_series(page.replace('출판사','다른 항목'),'50')['publisher'],'')
        detail=metadata.parse_webtoon({'titleId':1,'titleName':'테스트','publishDayOfWeekList':['WEDNESDAY','SUNDAY'],'favoriteCount':1},'1')
        self.assertEqual(detail['serial_days'],['수','일'])
        snapshot=radar.collect(fixtures.FakeClient())
        work=next(w for w in works.build_works(snapshot,{'webtoon':{'1':detail}}) if w['webtoon_id']=='1')
        self.assertEqual(work['serial_days'],['수','일'])
        fallback=next(w for w in works.build_works(snapshot,{}) if w['webtoon_id']=='1')
        self.assertEqual(fallback['serial_days'],['월','매일+'])

    def test_company_counts_unique_works_and_missing_values(self):
        def work(ident,name,count,status='연재'):
            return {'work_id':ident,'publisher':name,'downloads':count,'status':status,
                    'metrics':{'downloads':{'approximate':True}}}
        a=work('a',' A  Studio ',100);b=work('b','a studio',None,'완결');c=work('c','B',500)
        rows=publishers.publisher_rankings([a,a,b,c,work('d','',9999)])
        self.assertEqual([r['key'] for r in rows],['b','a studio',''])
        group=rows[1];self.assertEqual((group['works'],group['downloads'],group['downloads_known'],group['finished']),(2,100,1,1))
        self.assertEqual(publishers.publisher_rankings([a,b,c],sort='works')[0]['key'],'a studio')
        self.assertIsNone(publishers.publisher_rankings([b])[0]['downloads'])

    def test_index_is_incremental_and_ui_never_scans_archives(self):
        snapshot=radar.collect(fixtures.FakeClient());row=fixtures.MetadataTests().webtoon()
        cache={'webtoon':{'1':row},'series':{}}
        work=next(w for w in works.build_works(snapshot,cache) if w['webtoon_id']=='1')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);live_data.write_json(root/'catalog_latest.json',snapshot);live_data.write_json(root/'metadata_latest.json',cache)
            index=history_index.build_index(root)
            with patch.object(Path,'glob',side_effect=AssertionError('No archive scans in UI')):
                result=trends.metric_history(work,cache,snapshot,root,allow_legacy=False)
            self.assertEqual(result['interest'][0]['value'],100000)
            self.assertEqual(len(result['interest']),1)
            self.assertEqual(history_index.build_index(root)['sources'],index['sources'])

    def test_notion_zip_has_matching_rows_sources_and_formula_safe_text(self):
        snapshot=radar.collect(fixtures.FakeClient());snapshot['titles'][0]['title']='=SUM(A1)'
        rows=works.build_works(snapshot,{})
        payload=notion_export.export_notion(rows,snapshot)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            self.assertEqual(len(archive.namelist()),3)
            records=list(csv.DictReader(io.StringIO(archive.read('작품.csv').decode('utf-8-sig'))))
            self.assertEqual(len(records),len(rows))
            record=next(r for r in records if r['작품 ID']=='webtoon:1')
            self.assertEqual(record['작품명'],"'=SUM(A1)")
            self.assertIn('comic.naver.com',record['웹툰 링크'])
            self.assertEqual(record['다운로드 수'],'')
            self.assertIn('노션 사본은 자동',archive.read('Webtoon Radar.md').decode())

if __name__=='__main__':unittest.main()
