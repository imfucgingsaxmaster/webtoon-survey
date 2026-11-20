import copy
import json
import tempfile
import threading
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import live_data
import profile_collection as profiles
import publisher_enrichment as enrichment


class ProfileCollectionTests(unittest.TestCase):
    def client(self, stopped=False):
        client=Mock()
        client.stopped=threading.Event()
        if stopped:client.stopped.set()
        return client

    def test_401_is_title_specific_but_403_and_429_stop_host(self):
        robots=Mock()
        robots.crawl_delay.return_value=None
        robots.request_rate.return_value=None
        robots.can_fetch.return_value=True
        policy=SimpleNamespace(robots=robots,allowed={'/detail'},base='https://example.invalid')
        for code in (401,403,429):
            with self.subTest(code=code):
                client=enrichment.PacedClient(policy,interval=0)
                with patch.object(enrichment.urllib.request,'build_opener') as opener:
                    opener.return_value.open.side_effect=urllib.error.HTTPError(policy.base,code,'Access response',{},None)
                    with self.assertRaises(urllib.error.HTTPError):client.read('/detail',id=1)
                    self.assertEqual(client.stopped.is_set(),code!=401)
                    if code==401:
                        opener.return_value.open.side_effect=None
                        opener.return_value.open.return_value.__enter__.return_value.read.return_value=b'public title'
                        self.assertEqual(client.read('/detail',id=2),'public title')
                    else:
                        with self.assertRaises(PermissionError):client.read('/detail',id=2)
                        self.assertEqual(opener.return_value.open.call_count,1)

    def test_series_login_falls_back_to_public_webtoon_profile(self):
        series=self.client();webtoon=self.client()
        series.read.side_effect=urllib.error.HTTPError('https://example.invalid',401,'Login required',{},None)
        webtoon.read.side_effect=[json.dumps({'titleId':1,'titleName':'작품','synopsis':'공개 설명'}),
            json.dumps({'titleId':1,'totalCount':12,'articleList':[{'subtitle':'11화'}]})]
        result=profiles.fetch_profile({'series_id':'2','webtoon_id':'1'},series,webtoon)
        self.assertEqual(result['state'],'confirmed')
        self.assertEqual(result['platform'],'네이버웹툰')
        self.assertEqual(result['episode_count'],12)
        self.assertEqual(result['attempts'][0]['state'],'unavailable')
        series.read.assert_called_once()

    def test_stopped_host_is_not_requested_and_other_host_can_continue(self):
        series=self.client(stopped=True);webtoon=self.client()
        webtoon.read.side_effect=[json.dumps({'titleId':1,'titleName':'작품'}),
            json.dumps({'titleId':1,'totalCount':0})]
        result=profiles.fetch_profile({'series_id':'2','webtoon_id':'1'},series,webtoon)
        self.assertEqual(result['state'],'confirmed')
        series.read.assert_not_called()
        result=profiles.fetch_profile({'series_id':'3','webtoon_id':''},series,webtoon)
        self.assertEqual(result['attempts'][0]['state'],'deferred')

    def test_programming_errors_are_not_silenced(self):
        series=self.client();series.read.side_effect=RuntimeError('bug')
        with self.assertRaisesRegex(RuntimeError,'bug'):
            profiles.fetch_profile({'series_id':'1'},series,self.client())

    def work(self, ident='1', status='연재'):
        return {'work_id':'webtoon:'+ident,'title':'작품','status':status,'sources':[],
            'webtoon_id':ident,'interest':10,'downloads':None}

    def test_weekly_boundary_refreshes_recent_titles_and_freezes_all_completed(self):
        now=datetime.now(timezone.utc)
        old=(now-timedelta(days=3)).isoformat()
        since=(now-timedelta(hours=1)).isoformat()
        cache={'profiles':{'webtoon:1':{'state':'confirmed','observed_at':old}}}
        with patch.object(profiles,'build_works',return_value=[self.work(),self.work('2','완결')]):
            self.assertEqual(profiles.profile_targets({},cache,True),[])
            self.assertEqual(len(profiles.profile_targets({},cache,True,since)),1)
            cache['profile_checks']={'webtoon:1':{'state':'unavailable','observed_at':now.isoformat()}}
            self.assertEqual(profiles.profile_targets({},cache,True,since),[])
            # Compare instants, not strings with differing UTC offsets.
            cache['profile_checks']['webtoon:1']['observed_at']=(now-timedelta(days=1)).astimezone(timezone(timedelta(hours=9))).isoformat()
            self.assertEqual(len(profiles.profile_targets({},cache,True,since)),1)

    def test_partial_refresh_preserves_good_profile_and_records_attempt_clock(self):
        old={'state':'confirmed','observed_at':'2026-01-01T00:00:00+00:00','description':'old'}
        cache={'schema_version':1,'profiles':{'webtoon:1':old},'webtoon':{},'series':{},'runs':{}}
        outcomes=[{'state':'unavailable','observed_at':'2026-11-20T00:00:00+00:00',
            'attempts':[{'platform':'webtoon','state':'unavailable','reason':'401'}]},
            {'state':'confirmed','observed_at':'2026-11-20T00:00:01+00:00','description':'new'},
            {'state':'unavailable','observed_at':'2026-11-20T00:00:02+00:00',
             'attempts':[{'platform':'webtoon','state':'deferred','reason':'429'}]}]
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            live_data.write_json(root/'metadata_latest.json',cache)
            live_data.write_json(root/'catalog_latest.json',{})
            targets=[dict(self.work(str(i)),series_id='') for i in range(1,4)]
            def fetch(target,*args):return copy.deepcopy(outcomes[int(target['webtoon_id'])-1])
            with patch.object(profiles,'profile_targets',return_value=targets),\
                 patch.object(profiles,'SeriesClient'),patch.object(profiles,'PublicClient'),\
                 patch.object(profiles,'PacedClient'),patch.object(profiles,'fetch_profile',side_effect=fetch):
                profiles.collect_profiles(root,refresh=True,refresh_since='2026-11-20T00:00:00+00:00')
            result=json.loads((root/'metadata_latest.json').read_bytes())
            self.assertEqual(result['profiles']['webtoon:1'],old)
            self.assertEqual(result['profiles']['webtoon:2']['description'],'new')
            run=result['runs']['profiles']
            self.assertEqual((run['checked'],run['confirmed'],run['unavailable'],run['deferred'],run['state']),(3,1,1,1,'partial'))
            merged=live_data.merge_metadata(result,cache)
            self.assertEqual(merged['profile_checks'],result['profile_checks'])
            self.assertEqual(result['webtoon'],cache['webtoon'])


if __name__=='__main__':unittest.main()
