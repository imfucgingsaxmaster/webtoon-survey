"""Public content-ID resolution and regression coverage for missing venues."""
import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import radar
import metadata
import live_data
import works
import tests as fixtures

def page(title='같은 제목',ident='50'):
    return f'''<div id="content"><div class="end_head"><h2>{title}</h2>
    <a class="btn_download"><span>9,822만</span></a><span id="commentCount">1.7만</span>
    <a id="spiButton2" data-url="https://series.naver.com/comic/detail.series?productNo={ident}"></a>
    </div></div>'''

class ResolutionTests(unittest.TestCase):
    def client(self,target='/comic/detail.series?productNo=50',second=None):
        class Client(metadata.SeriesClient):
            def __init__(self):self.calls=[]
            def html(self,path,**params):
                self.calls.append((path,params))
                if 'originalProductId' in params:raise radar.PublicRedirect('canonical',target)
                if isinstance(second,Exception):raise second
                return second or page()
        return Client()

    def test_canonical_public_redirect_only_and_evidence_retained(self):
        client=self.client();row=client.resolve_original('381824')
        self.assertEqual(row['title_id'],'50')
        self.assertEqual(row['original_product_id'],'381824')
        self.assertTrue(row['resolution_url'].endswith('originalProductId=381824'))
        self.assertEqual(row['metrics']['downloads']['value'],98220000)
        self.assertEqual(client.calls[-1],('/comic/detail.series',{'productNo':'50'}))

    def test_login_novel_external_and_extra_query_redirects_are_not_followed(self):
        for target in ['https://nid.naver.com/login','/comic/ageCheck.series',
                       '/novel/detail.series?productNo=50','http://series.naver.com/comic/detail.series?productNo=50',
                       'https://series.naver.com.evil.test/comic/detail.series?productNo=50',
                       '/comic/detail.series?productNo=50&isWebtoonAgreePopUp=true',
                       '/comic/detail.series?productNo=50&productNo=51','/comic/detail.series?productNo=50#x']:
            with self.subTest(target=target):
                client=self.client(target)
                with self.assertRaises(radar.PublicRedirect):client.resolve_original('381824')
                self.assertEqual(len(client.calls),1)

    def test_redirect_chain_and_wrong_product_rejected(self):
        with self.assertRaises(radar.PublicRedirect):
            self.client(second=radar.PublicRedirect('login','https://nid.naver.com')).resolve_original('381824')
        with self.assertRaises(ValueError):self.client(second=page(ident='51')).resolve_original('381824')

    def test_restricted_detail_preserves_only_verified_canonical_link(self):
        client=self.client(second=radar.PublicRedirect('login','https://nid.naver.com'))
        with self.assertRaises(metadata.SeriesLinkRestricted) as result:client.resolve_original('381824')
        self.assertEqual(result.exception.product_id,'50')
        self.assertTrue(result.exception.resolution_url.endswith('originalProductId=381824'))
        self.assertEqual(len(client.calls),2)

    def test_promotion_label_is_not_a_season_or_different_title(self):
        self.assertEqual(works.normalized(works.base_title('[드라마원작] 스위트홈')),works.normalized('스위트홈'))
        self.assertNotEqual(works.base_title('스위트홈 [시즌2]'),'스위트홈')
        self.assertNotEqual(works.base_title('[다른판본] 스위트홈'),'스위트홈')

    def test_common_id_merges_changed_creator_but_not_other_title(self):
        snapshot=radar.collect(fixtures.FakeClient())
        snapshot['titles'][0].update(title='싸움독학',author='박만사 / 김정현')
        webtoon=fixtures.MetadataTests().webtoon();webtoon['contents_no']='381824'
        series=metadata.parse_series(page('싸움독학'),50)
        series.update(author='박태준 / 김정현',original_product_id='381824',
                      resolution_url='https://series.naver.com/comic/detail.series?originalProductId=381824')
        cache={'webtoon':{'1':webtoon},'series':{'50':series}}
        row=next(w for w in works.build_works(snapshot,cache) if w['webtoon_id']=='1')
        self.assertEqual(len(row['venues']),2)
        self.assertEqual(row['link_basis'],'공개 공통 작품번호·제목 확인')
        series['original_product_id']='999'
        self.assertEqual(len(works.build_works(snapshot,cache)),11)
        series['original_product_id']='381824';series['title']='다른 작품'
        self.assertEqual(len(works.build_works(snapshot,cache)),11)

    def test_spacing_ignored_without_interpreting_regex(self):
        snapshot=radar.collect(fixtures.FakeClient())
        for title,query in [('전지적 독자 시점','전지적 독자시점'),('호랑이형님','호랑이 형님')]:
            snapshot['titles'][0]['title']=title
            rows=works.build_works(snapshot,{})
            self.assertEqual(works.query_title_ids(snapshot,query),['1'])
            self.assertEqual(len(works.filter_works(rows,query=query)),1)
            self.assertEqual(works.filter_works(rows,query='['),[])

    def test_newer_counts_preserve_previously_verified_link(self):
        old=metadata.parse_series(page(),50,'2026-10-15T00:00:00+00:00')
        old.update(original_product_id='99',resolution_url='https://series.naver.com/comic/detail.series?originalProductId=99',resolved_at=old['observed_at'])
        newer=metadata.parse_series(page(),50,'2026-10-16T00:00:00+00:00')
        merged=live_data.merge_metadata({'series':{'50':old}},{'series':{'50':newer}})
        self.assertEqual(merged['series']['50']['original_product_id'],'99')
        self.assertEqual(merged['series']['50']['observed_at'],newer['observed_at'])

class LookupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.env=patch.dict(os.environ,{'WEBTOON_RADAR_OFFLINE':'0'});self.env.start();self.addCleanup(self.env.stop)
        self.snapshot=radar.collect(fixtures.FakeClient());self.snapshot['titles'][0]['title']='같은 제목'
        live_data.write_json(self.root/'catalog_latest.json',self.snapshot)

    def test_lookup_migrates_old_metadata_persists_and_reuses_resolution(self):
        row=fixtures.MetadataTests().webtoon();row.pop('contents_no',None)
        live_data.write_json(self.root/'metadata_latest.json',{'schema_version':1,'webtoon':{'1':row},'series':{}})
        calls=[]
        class Webtoon:
            def get(self,*a,**k):
                calls.append('webtoon')
                return {'titleId':1,'titleName':'같은 제목','contentsNo':99,'favoriteCount':123}
        class Series:
            def resolve_original(self,n):
                calls.append(n);record=metadata.parse_series(page(),50)
                record.update(original_product_id=n,resolution_url=f'https://series.naver.com/comic/detail.series?originalProductId={n}')
                return record
        with patch.object(live_data.time,'sleep'):
            result=live_data.fill_series_links(['1','1'],self.root,webtoon_factory=Webtoon,series_factory=Series)
            again=live_data.fill_series_links(['1'],self.root,webtoon_factory=Webtoon,series_factory=Series)
        self.assertEqual((result['fetched'],again['fetched']),(1,0));self.assertEqual(calls,['webtoon','99'])
        cache=live_data.load_live_metadata(self.root)
        self.assertEqual(cache['series_checks']['1']['state'],'linked')
        self.assertEqual(len(next(w for w in works.build_works(self.snapshot,cache) if w['webtoon_id']=='1')['venues']),2)

    def test_title_mismatch_does_not_store_wrong_series(self):
        row=fixtures.MetadataTests().webtoon();row['contents_no']='99'
        live_data.write_json(self.root/'metadata_latest.json',{'schema_version':1,'webtoon':{'1':row},'series':{}})
        class Wrong:
            def resolve_original(self,n):return metadata.parse_series(page('다른 작품'),50)
        with patch.object(live_data.time,'sleep'):
            live_data.fill_series_links(['1'],self.root,series_factory=Wrong)
        cache=live_data.load_live_metadata(self.root)
        self.assertEqual(cache['series'],{});self.assertEqual(cache['series_checks']['1']['state'],'title_mismatch')

    def test_service_error_stops_and_cools_down(self):
        row=fixtures.MetadataTests().webtoon();row['contents_no']='99'
        live_data.write_json(self.root/'metadata_latest.json',{'schema_version':1,'webtoon':{'1':row},'series':{}})
        class Blocked:
            calls=0
            def resolve_original(self,n):Blocked.calls+=1;raise PermissionError('robots')
        with patch.object(live_data.time,'sleep'):
            result=live_data.fill_series_links(['1','2'],self.root,series_factory=Blocked)
            live_data.fill_series_links(['1'],self.root,series_factory=Blocked)
        self.assertEqual(result['state'],'temporarily_unavailable');self.assertEqual(Blocked.calls,1)
        self.assertIn('1',live_data.load_live_metadata(self.root)['webtoon'])

    def test_restricted_link_shows_venue_without_inventing_metrics(self):
        row=fixtures.MetadataTests().webtoon();row['contents_no']='99'
        live_data.write_json(self.root/'metadata_latest.json',{'schema_version':1,'webtoon':{'1':row},'series':{}})
        class Restricted:
            def resolve_original(self,n):raise metadata.SeriesLinkRestricted('50',f'https://series.naver.com/comic/detail.series?originalProductId={n}')
        with patch.object(live_data.time,'sleep'):
            live_data.fill_series_links(['1'],self.root,series_factory=Restricted)
        cache=live_data.load_live_metadata(self.root)
        self.assertEqual(cache['series'],{})
        work=next(w for w in works.build_works(self.snapshot,cache) if w['webtoon_id']=='1')
        self.assertEqual(work['venues'],['네이버웹툰','네이버시리즈'])
        self.assertTrue(work['series_url'].endswith('productNo=50'))
        self.assertIsNone(work['downloads']);self.assertIsNone(work['comments'])
        self.assertEqual(work['downloads_display'],'공개 접근 제한')
        self.assertEqual(len(works.filter_works([work],venues=['네이버시리즈'])),1)
        self.assertEqual(works.filter_works([work],minimums={'downloads':1}),[])
        cache['series_checks']['1']['contents_no']='wrong'
        self.assertEqual(next(w for w in works.build_works(self.snapshot,cache) if w['webtoon_id']=='1')['series_url'],'')

    def test_one_bad_response_does_not_block_following_title(self):
        first=fixtures.MetadataTests().webtoon();first['contents_no']='99'
        second=copy.deepcopy(first);second.update(title_id='2',contents_no='100')
        self.snapshot['titles'][1]['title']='같은 제목'
        live_data.write_json(self.root/'catalog_latest.json',self.snapshot)
        live_data.write_json(self.root/'metadata_latest.json',{'schema_version':1,'webtoon':{'1':first,'2':second},'series':{}})
        class Partial:
            def resolve_original(self,n):
                if n=='99':raise ValueError('missing product heading')
                row=metadata.parse_series(page(),50)
                row.update(original_product_id=n,resolution_url=f'https://series.naver.com/comic/detail.series?originalProductId={n}')
                return row
        with patch.object(live_data.time,'sleep'):
            result=live_data.fill_series_links(['1','2'],self.root,series_factory=Partial)
        self.assertEqual(result,{'checked':2,'fetched':1,'state':'ready'})
        cache=live_data.load_live_metadata(self.root)
        self.assertEqual(cache['series_checks']['1']['state'],'parse_error')
        self.assertEqual(cache['series_checks']['2']['state'],'linked')

if __name__=='__main__':unittest.main()
