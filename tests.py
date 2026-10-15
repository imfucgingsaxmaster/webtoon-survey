import copy
import io
import tempfile
import unittest
from pathlib import Path
from openpyxl import load_workbook
import radar

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

if __name__=='__main__':unittest.main()
