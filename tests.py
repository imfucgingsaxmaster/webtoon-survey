import copy
import io
import tempfile
import unittest
from pathlib import Path
from openpyxl import load_workbook
import radar
import metadata

class FakeClient:
    robots_hash='test-only'
    def get(self,path,**params):
        def item(n):return {'titleId':n,'titleName':f'Test {n}','author':'Tester','starScore':9.5,'finish':False}
        if path.endswith('/weekday'):
            if params.get('week'):return {'titleList':[item(1),item(9)]}
            return {'titleListMap':{day:[item(i+1)] for i,day in enumerate(radar.WEEKDAYS)}}
        page=params['page']
        return {'titleList':[item(10+page)],'pageInfo':{'page':page,'totalRows':2,'totalPages':2,'nextPage':page+1 if page<2 else 0}}

class CatalogueTests(unittest.TestCase):
    def setUp(self):self.snapshot=radar.collect(FakeClient())
    def test_all_pages_and_deduplication(self):
        self.assertEqual(len(self.snapshot['titles']),10);self.assertEqual(len(self.snapshot['memberships']),11)
        self.assertEqual(self.snapshot['coverage'][-1]['pages'],2);radar.validate_snapshot(self.snapshot)
    def test_missing_weekday_fails(self):
        client=FakeClient();base=client.get
        def get(path,**p):
            result=base(path,**p)
            if 'titleListMap' in result:del result['titleListMap']['MONDAY']
            return result
        client.get=get
        with self.assertRaises(ValueError):radar.collect(client)
    def test_changed_pagination_fails(self):
        client=FakeClient();base=client.get
        def get(path,**p):
            result=base(path,**p)
            if p.get('page')==2:result['pageInfo']['totalRows']=3
            return result
        client.get=get
        with self.assertRaises(ValueError):radar.collect(client)
    def test_duplicate_cross_page_fails(self):
        client=FakeClient();base=client.get
        def get(path,**p):
            result=base(path,**p)
            if p.get('page')==2:result['titleList'][0]['titleId']=11
            return result
        client.get=get
        with self.assertRaises(ValueError):radar.collect(client)
    def test_snapshot_integrity_and_idempotence(self):
        with tempfile.TemporaryDirectory() as folder:
            db=Path(folder)/'test.sqlite';self.assertTrue(radar.ingest(self.snapshot,db));self.assertFalse(radar.ingest(self.snapshot,db))
            altered=copy.deepcopy(self.snapshot);altered['titles'][0]['title']='changed'
            with self.assertRaises(ValueError):radar.ingest(altered,db)
            with radar.connect(db) as con:self.assertEqual(con.execute('SELECT COUNT(*) FROM catalogue_titles').fetchone()[0],10)
    def test_pdf_dataset_rejected(self):
        with self.assertRaises(ValueError):radar.validate_snapshot({'schema_version':1,'source':'research.pdf'})
    def test_no_fake_views(self):
        self.assertNotIn('viewCount',radar.normalize({'titleId':1,'titleName':'Example','viewCount':0}))
    def test_excel_formula_injection(self):
        self.snapshot['titles'][0]['title']='=1+1';output=io.BytesIO()
        radar.export_excel(self.snapshot,output,radar.compare(self.snapshot,None))
        self.assertEqual(load_workbook(io.BytesIO(output.getvalue()))['전체 작품']['B2'].data_type,'s')
    def test_comparison_baseline(self):
        self.assertTrue(radar.compare(self.snapshot,None)['baseline'])
        self.assertEqual(radar.compare(self.snapshot,self.snapshot)['added'],[])
    def test_disallowed_endpoint(self):
        with self.assertRaises(PermissionError):object.__new__(radar.PublicClient).get('/webtoon/detail',titleId=1)

class MetadataTests(unittest.TestCase):
    def webtoon(self,n=1,count=100000):
        return metadata.parse_webtoon({'titleId':n,'titleName':'같은 제목','favorite':True,'favoriteCount':count,
            'curationTagList':[{'tagName':'무협/사극','curationType':'GENRE_HISTORICAL'},
                               {'tagName':'먼치킨','curationType':'CUSTOM_TAG'}]},n,'2026-10-15T00:00:00+00:00')

    def series(self,downloads='2억 8,683만',comments='1.4만'):
        html=f'''<div id="content"><div class="end_head"><h2>같은 제목</h2>
          <div class="score_area"><em>9.8</em></div><a class="btn_download"><span>{downloads}</span></a>
          <span id="commentCount">{comments}</span><a id="spiButton2" data-url="https://series.naver.com/comic/detail.series?productNo=1"></a>
          <a class="btn_interest">관심</a></div><ul class="end_info"><li class="info_lst"><ul><li>연재중</li>
          <li><a href="/comic/categoryProductList.series?genreCode=88">무협</a></li><li><span>글</span> <a>작가</a></li>
          </ul></li></ul></div>'''
        return metadata.parse_series(html,1,'2026-10-15T00:00:00+00:00')

    def test_units_and_precision(self):
        for text,value,approx in [('794,200',794200,False),('0',0,False),('1.4만',14000,True),('9,985만',99850000,True),('2억 8,683만',286830000,True),('1억 2만 3천 4',100023004,True)]:
            self.assertEqual(metadata.parse_count(text),{'value':value,'display':text,'approximate':approx})
        self.assertIsNone(metadata.parse_count('')['value'])
        for invalid in ('로그인','-1','1만 2억','0.00001만'):
            with self.assertRaises(ValueError):metadata.parse_count(invalid)

    def test_webtoon_interest_is_public_aggregate(self):
        row=self.webtoon();self.assertEqual(row['metrics']['interest']['value'],100000)
        self.assertNotIn('favorite',row)
        self.assertEqual(row['genres'],['무협/사극'])
        self.assertIsNone(row['metrics']['downloads']['value'])
        self.assertIsNone(self.webtoon(count=None)['metrics']['interest']['value'])
        self.assertEqual(self.webtoon(count=0)['metrics']['interest']['value'],0)
        with self.assertRaises(ValueError):self.webtoon(count=True)
        with self.assertRaises(ValueError):metadata.parse_webtoon({'titleId':2,'titleName':'Wrong'},1)

    def test_series_counts_do_not_invent_interest(self):
        row=self.series();self.assertEqual(row['metrics']['downloads']['value'],286830000)
        self.assertTrue(row['metrics']['comments']['approximate'])
        self.assertIsNone(row['metrics']['interest']['value'])
        self.assertEqual(row['genres'],['무협'])
        with self.assertRaises(ValueError):metadata.parse_series('<h2>Login</h2>',1)

    def test_tag_thresholds_and_filter_modes(self):
        snapshot=radar.collect(FakeClient())
        rows=metadata.build_rows(snapshot,{'webtoon':{'1':self.webtoon()},'series':{'1':self.series()}})
        self.assertEqual(len(rows),11)  # all ten webtoons stay, plus distinct Series product
        self.assertEqual(sum(r['title_id']=='1' for r in rows),2)
        self.assertEqual(len(metadata.filter_rows(rows,tags=['관심:10만 이상','태그:먼치킨'])),1)
        self.assertEqual(len(metadata.filter_rows(rows,tags=['관심:10만 이상','댓글:1만 이상'])),0)
        self.assertEqual(len(metadata.filter_rows(rows,tags=['관심:10만 이상','댓글:1만 이상'],mode='하나 이상 포함')),2)
        self.assertEqual(len(metadata.filter_rows(rows,minimums={'interest':100001})),0)
        self.assertEqual(len(metadata.filter_rows(rows,minimums={'interest':1})),1)
        self.assertEqual(len(metadata.filter_rows(rows,query='무협')),2)
        self.assertEqual(metadata.filter_rows(rows,query='['),[])  # literal, no regex

    def test_excel_keeps_null_raw_and_safe_text(self):
        snapshot=radar.collect(FakeClient());snapshot['titles'][0]['title']='=HYPERLINK("bad")'
        rows=metadata.build_rows(snapshot,{'webtoon':{'1':self.webtoon()},'series':{'1':self.series()}})
        buf=io.BytesIO();metadata.export_search_excel(rows,buf)
        ws=load_workbook(io.BytesIO(buf.getvalue()))['작품 태그 검색']
        self.assertEqual(ws['C2'].data_type,'s');self.assertEqual(ws['F2'].value,100000)
        self.assertIsNone(ws['H2'].value);self.assertEqual(ws.cell(ws.max_row,8).value,286830000)
        self.assertIn('2억 8,683만',ws.cell(ws.max_row,9).value)

    def test_sqlite_platform_identity_and_idempotence(self):
        cache={'webtoon':{'1':self.webtoon()},'series':{'1':self.series()}}
        with tempfile.TemporaryDirectory() as folder:
            db=Path(folder)/'m.sqlite'
            metadata.export_metadata(cache,db);metadata.export_metadata(cache,db)
            with radar.connect(db) as con:
                self.assertEqual(con.execute('SELECT COUNT(*) FROM title_metrics').fetchone()[0],2)
                self.assertEqual(con.execute('SELECT interest FROM title_metrics WHERE platform=?',('네이버웹툰',)).fetchone()[0],100000)

    def test_series_allowlist_excludes_restricted_routes(self):
        client=object.__new__(metadata.SeriesClient)
        for path in ('/search/search.series','/comic/categoryProductList.series','/my/favorite','/viewer'):
            with self.assertRaises(PermissionError):client.html(path)

    def test_resumable_budget_and_failure_preserves_previous(self):
        from unittest.mock import patch
        import json
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'catalog_latest.json').write_text(json.dumps(radar.collect(FakeClient())),encoding='utf-8')
            class Details:
                def get(self,path,**p):return {'titleId':int(p['titleId']),'titleName':'Test','favoriteCount':123,'curationTagList':[]}
            with patch.object(metadata,'PublicClient',return_value=Details()):
                first=metadata.collect_metadata(root,webtoon_limit=2,series_limit=0)
                second=metadata.collect_metadata(root,webtoon_limit=2,series_limit=0)
            self.assertEqual(len(first['webtoon']),2);self.assertEqual(len(second['webtoon']),4)
            self.assertEqual(second['runs']['webtoon']['state'],'partial')
            class Blocked:
                def get(self,*a,**k):raise PermissionError('robots changed')
            with patch.object(metadata,'PublicClient',return_value=Blocked()):
                with self.assertRaises(RuntimeError):metadata.collect_metadata(root,webtoon_limit=2,series_limit=0)
            self.assertEqual(len(metadata.load_metadata(root/'metadata_latest.json')['webtoon']),4)

if __name__=='__main__':unittest.main()
