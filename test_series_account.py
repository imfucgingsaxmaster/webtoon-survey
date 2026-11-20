import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch

import series_account as account
from metadata import refresh_boundary

HTML='''<div id="content"><div class="end_head"><h2>작품</h2>
<a id="spiButton2" data-url="https://series.naver.com/comic/detail.series?productNo=2"></a>
<a class="btn_download"><span>2.5만</span></a></div>
<div class="end_dsc"><div class="_synopsis">공식 작품 설명</div></div>
<h5 class="end_total_episode">총 12화</h5></div>'''


class SeriesAccountTests(unittest.TestCase):
    def test_login_location_is_only_a_fixed_category(self):
        self.assertEqual(account.login_page_kind(account.HOME+'?private=secret'),'series_home')
        self.assertEqual(account.login_page_kind(account.LOGIN+'&id=private'),'login_form')
        self.assertEqual(account.login_page_kind('https://nid.naver.com/private-account'),'naver_verification')
        self.assertEqual(account.login_page_kind('https://private.example/secret'),'other')

    def test_login_diagnostics_never_return_account_text(self):
        self.assertEqual(account.login_reason('private-account: 자동입력 방지 문자를 입력하세요'),'captcha_required')
        self.assertEqual(account.login_reason('private-account: 2단계 인증'),'additional_verification')
        self.assertEqual(account.login_reason('private-account: 새로운 기기 확인'),'device_verification')
        self.assertEqual(account.login_reason('private-account / private-password'),'login_requires_attention')

    def fixture(self):
        snapshot={'titles':[{'title_id':'1','title':'작품','author':'작가','status':'연재'}]}
        cache={'schema_version':1,'webtoon':{'1':{'contents_no':'10'}},'series':{},
            'unavailable':{'series':{'2':{'observed_at':'2026-11-20T00:00:00+00:00','reason':'login'}}},
            'series_checks':{'1':{'state':'linked_restricted','series_id':'2','observed_at':'2026-11-20T00:00:00+00:00'}},'runs':{}}
        return snapshot,cache

    def test_only_stale_unavailable_active_titles_are_queued(self):
        snapshot,cache=self.fixture();since=refresh_boundary('2026-11-20T00:00:00+00:00')
        self.assertEqual(account.pending_series(snapshot,cache,since),['2'])
        cache['series']['2']={'observed_at':'2026-11-20T00:01:00+00:00','metrics':{'downloads':{'value':0}}}
        self.assertEqual(account.pending_series(snapshot,cache,since),[])
        cache['series'].clear();snapshot['titles'][0]['status']='완결'
        self.assertEqual(account.pending_series(snapshot,cache,since),[])

    def test_matching_source_updates_counts_and_clears_restricted_link(self):
        snapshot,cache=self.fixture();webtoon=copy.deepcopy(cache['webtoon'])
        row=account.record_series(cache,snapshot,'2',HTML)
        self.assertEqual(row['metrics']['downloads']['value'],25000)
        self.assertTrue(row['metrics']['downloads']['approximate'])
        self.assertEqual(row['original_product_id'],'10')
        self.assertEqual(cache['series_checks']['1']['state'],'linked')
        self.assertEqual(cache['profiles']['webtoon:1']['episode_count'],12)
        self.assertEqual(cache['webtoon'],webtoon)
        self.assertNotIn('2',cache['unavailable']['series'])

    def test_wrong_product_cannot_modify_old_observations(self):
        snapshot,cache=self.fixture();before=copy.deepcopy(cache)
        with self.assertRaises(ValueError):account.record_series(cache,snapshot,'3',HTML)
        self.assertEqual(cache,before)

    def run_collect(self,env,browser):
        snapshot,cache=self.fixture()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'metadata_latest.json').write_text(json.dumps(cache))
            (root/'catalog_latest.json').write_text(json.dumps(snapshot))
            policy=Mock();policy.robots.crawl_delay.return_value=None
            policy.robots.request_rate.return_value=None;policy.robots.can_fetch.return_value=True
            output=io.StringIO()
            with patch.dict(os.environ,env,clear=True),patch.object(account,'SeriesClient',return_value=policy),\
                 patch.object(account.time,'sleep'),patch('history_index.build_index'),contextlib.redirect_stdout(output):
                result=account.collect(root,'2026-11-20T00:00:00+00:00',browser_factory=browser)
            saved=json.loads((root/'metadata_latest.json').read_bytes())
            files={str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()}
            return result,saved,output.getvalue(),files

    def test_missing_secrets_never_start_browser(self):
        factory=Mock()
        result,_,_,_=self.run_collect({},factory)
        self.assertEqual(result['state'],'not_configured');factory.assert_not_called()

    def test_login_probe_does_not_read_products(self):
        factory=MagicMock();browser=factory.return_value.__enter__.return_value
        with patch.dict(os.environ,{'NAVER_ID':'test','NAVER_PASSWORD':'test'},clear=True):
            self.assertEqual(account.check_login(factory),{'state':'ready'})
        browser.login.assert_called_once_with('test','test');browser.read.assert_not_called()

    def test_extra_verification_stops_without_retry_or_credential_leak(self):
        factory=MagicMock();browser=factory.return_value.__enter__.return_value
        browser.login.side_effect=account.AccountAttention('login_requires_attention')
        env={'NAVER_ID':'private-test-account','NAVER_PASSWORD':'private-test-password'}
        result,saved,output,files=self.run_collect(env,factory)
        self.assertEqual(result['state'],'needs_attention')
        self.assertEqual(result['checked'],0);browser.login.assert_called_once();browser.read.assert_not_called()
        self.assertEqual(saved['series'],{})
        self.assertEqual(files,{'metadata_latest.json','catalog_latest.json'})
        for secret in env.values():self.assertNotIn(secret,output+json.dumps(saved))

    def test_success_keeps_only_catalogue_fields_and_history(self):
        factory=MagicMock();browser=factory.return_value.__enter__.return_value
        browser.read.return_value=HTML
        result,saved,_,files=self.run_collect({'NAVER_ID':'test','NAVER_PASSWORD':'test'},factory)
        self.assertEqual((result['state'],result['updated']),('complete',1))
        self.assertEqual(saved['series']['2']['metrics']['downloads']['value'],25000)
        self.assertTrue(any(p.endswith('_series_supplement.json.gz') for p in files))
        self.assertFalse(any(p.endswith('.html') or 'cookie' in p or 'storage_state' in p for p in files))


if __name__=='__main__':unittest.main()
