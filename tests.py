import csv
import json
import tempfile
import unittest
from pathlib import Path
from radar import ROOT, FIELDS, validate_rows, ingest, load_rows, summarize, export_excel

class RadarTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name)/'test.sqlite'
        self.registry = json.loads((ROOT/'sources.json').read_text(encoding='utf-8'))
        with (ROOT/'research.csv').open(encoding='utf-8-sig',newline='') as f:
            self.rows = list(csv.DictReader(f))

    def test_research_totals_and_no_invented_metrics(self):
        rows=validate_rows(self.rows,self.registry)
        s=summarize(rows)
        self.assertEqual(s['records'],105)
        self.assertEqual(s['unique_titles'],102)
        self.assertEqual(s['ranked_records'],0)
        self.assertEqual(s['undated_records'],105)
        self.assertEqual(s['platform_counts']['네이버시리즈'],0)

    def test_idempotent_import(self):
        self.assertEqual(ingest(ROOT/'research.csv',ROOT/'sources.json',self.db),105)
        self.assertEqual(ingest(ROOT/'research.csv',ROOT/'sources.json',self.db),0)
        self.assertEqual(len(load_rows(self.db)),105)

    def test_unapproved_source_rejected(self):
        self.rows[0]['source_id']='unapproved'
        with self.assertRaises(ValueError): validate_rows(self.rows,self.registry)

    def test_duplicate_rejected(self):
        with self.assertRaises(ValueError): validate_rows(self.rows+[self.rows[0]],self.registry)

    def test_invalid_rank_and_date_rejected(self):
        for field,value in [('rank','0'),('rank','-1'),('observed_on','2099-01-01'),('weekday','토요일')]:
            rows=[dict(self.rows[0])]; rows[0][field]=value
            with self.assertRaises(ValueError): validate_rows(rows,self.registry)

    def test_modified_snapshot_rolls_back(self):
        ingest(ROOT/'research.csv',ROOT/'sources.json',self.db)
        self.rows[0]['genre']='변경된 분류'
        path=Path(self.tmp.name)/'changed.csv'
        with path.open('w',encoding='utf-8',newline='') as f:
            w=csv.DictWriter(f,fieldnames=FIELDS); w.writeheader(); w.writerows(self.rows)
        with self.assertRaises(ValueError): ingest(path,ROOT/'sources.json',self.db)
        self.assertNotEqual(load_rows(self.db)[0]['genre'],'변경된 분류')

    def test_series_accepted_with_permission(self):
        registry={'licensed':{'approved':True,'permission_basis':'unit test permission','kind':'licensed_export','platforms':['네이버시리즈']}}
        row=dict(self.rows[0],source_id='licensed',platform='네이버시리즈')
        self.assertEqual(validate_rows([row],registry)[0]['platform'],'네이버시리즈')

    def test_excel_formula_injection_stays_text(self):
        from openpyxl import load_workbook
        row=validate_rows([dict(self.rows[0],title='=1+1')],self.registry)
        path=Path(self.tmp.name)/'export.xlsx'
        export_excel(row,path)
        cell=load_workbook(path)['조사목록']['B2']
        self.assertEqual(cell.data_type,'s')
        self.assertEqual(cell.value,'=1+1')

if __name__=='__main__': unittest.main()
