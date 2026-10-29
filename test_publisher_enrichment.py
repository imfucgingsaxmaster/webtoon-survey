import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import live_data,radar,works
import publisher_enrichment as enrichment
import tests as fixtures

class PublisherEnrichmentTests(unittest.TestCase):
    def fixtures(self):
        snapshot=radar.collect(fixtures.FakeClient())
        snapshot['titles'][0]['status']='완결'
        serial=fixtures.MetadataTests().series();serial['title']=snapshot['titles'][0]['title']
        serial['author']=snapshot['titles'][0]['author'];serial['publisher']=''
        serial['status']='완결'
        cache={'schema_version':1,'webtoon':{'1':fixtures.MetadataTests().webtoon()},'series':{'1':serial},'runs':{}}
        return snapshot,cache

    def test_completed_missing_publisher_is_queued_but_existing_is_not(self):
        snapshot,cache=self.fixtures()
        targets=enrichment.pending_targets(snapshot,cache)
        self.assertIn(('series','1'),[(t['platform'],t['id']) for t in targets])
        cache['series']['1']['publisher']='확인된 출판사'
        self.assertNotIn(('series','1'),[(t['platform'],t['id']) for t in enrichment.pending_targets(snapshot,cache)])

    def test_enrichment_preserves_all_metric_records_and_catalogue(self):
        snapshot,cache=self.fixtures();before=copy.deepcopy(cache)
        result={'state':'confirmed','publisher':'확인 출판사','publisher_label':'출판사',
                'observed_at':'2026-10-30T00:00:00+00:00','source_url':cache['series']['1']['source_url']}
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            live_data.write_json(root/'catalog_latest.json',snapshot);live_data.write_json(root/'metadata_latest.json',cache)
            with patch.object(enrichment,'SeriesClient'),patch.object(enrichment,'PublicClient'),patch.object(enrichment,'PacedClient'),patch.object(enrichment,'fetch_publisher',return_value=result),patch.object(enrichment,'pending_targets',return_value=[{'platform':'series','id':'1'}]):
                # Mocked stop events must explicitly allow work.
                with patch.object(enrichment,'PacedClient') as client:
                    client.return_value.stopped.is_set.return_value=False
                    enrichment.collect_publishers(root)
            after=json.loads((root/'metadata_latest.json').read_text('utf-8'))
            self.assertEqual(after['webtoon'],before['webtoon']);self.assertEqual(after['series'],before['series'])
            self.assertEqual(json.loads((root/'catalog_latest.json').read_text('utf-8')),snapshot)
            self.assertEqual(after['publisher_details']['series']['1'],result)
            row=next(w for w in works.build_works(snapshot,after) if any(s['platform']=='네이버시리즈' and s['title_id']=='1' for s in w['sources']))
            self.assertEqual(row['publisher'],'확인 출판사')

    def test_sync_uses_publisher_clock_even_with_same_metric_clock(self):
        _,old=self.fixtures();new=copy.deepcopy(old)
        new['publisher_details']={'series':{'1':{'state':'confirmed','publisher':'제작사','observed_at':'2026-10-30T00:00:00+00:00'}}}
        merged=live_data.merge_metadata(old,new,old)
        self.assertEqual(merged['publisher_details']['series']['1']['publisher'],'제작사')
        self.assertEqual(merged['series'],old['series'])

    def test_no_public_series_identity_does_not_guess_author_as_publisher(self):
        class Webtoon:
            def read(self,*args,**kwargs):return json.dumps({'titleId':1,'titleName':'테스트','contentsNo':None})
        result=enrichment.fetch_publisher({'platform':'webtoon','id':'1','title':'테스트','author':'작가'},None,Webtoon())
        self.assertEqual(result['state'],'no_series_link');self.assertEqual(result['publisher'],'')

if __name__=='__main__':unittest.main()
