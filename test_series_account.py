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
    def test_normal_sign_in_resources_load_without_allowing_external_hosts(self):
        browser=account.AccountBrowser()
        for url in ('https://nid.naver.com/image.png','https://wtm.pstatic.net/image.png','https://www.naver.com/'):
            route=Mock();route.request.url=url;route.request.resource_type='image'
            browser.route(route)
            route.continue_.assert_called_once();route.abort.assert_not_called()
        route=Mock();route.request.url='https://naver.com.example/track'
        browser.route(route)
        route.abort.assert_called_once();route.continue_.assert_not_called()

    def test_login_location_is_only_a_fixed_category(self):
        self.assertEqual(account.login_page_kind(account.HOME+'?private=secret'),'series_home')
        self.assertEqual(account.login_page_kind(account.LOGIN+'&id=private'),'login_form')
        self.assertEqual(account.login_page_kind('https://nid.naver.com/private-account'),'naver_verification')
        self.assertEqual(account.login_page_kind('https://private.example/secret'),'other')

    def test_login_diagnostics_never_return_account_text(self):
        self.assertEqual(account.login_reason('비밀번호를 입력해 주세요'),'input_required')
        self.assertEqual(account.login_reason('private-account: 자동입력 방지 문자를 입력하세요'),'captcha_required')
        self.assertEqual(account.login_reason('private-account: 2단계 인증'),'additional_verification')
        self.assertEqual(account.login_reason('private-account: 새로운 기기 확인'),'device_verification')
        self.assertEqual(account.login_reason('아이디 또는 비밀번호가\n잘못 되었습니다'),'credentials_rejected')
        self.assertEqual(account.login_reason('잠시 후 다시 시도해주세요'),'temporarily_unavailable')
        self.assertEqual(account.login_reason('private-account / private-password'),'login_requires_attention')

    def test_login_diagnostics_only_keep_allowlisted_types_and_values(self):
        safe=account.safe_diagnostics({'phase':'await_redirect','page_kind':'login_form',
             'login_post_sent':True,'login_http_status':200,'id_present':True,'password_present':True,
             'account':'private-account','password':'private-password','url':'private-url',
             'access_stopped':'private-text','blocked_navigation':False})
        self.assertEqual(safe,{'phase':'await_redirect','page_kind':'login_form',
             'login_post_sent':True,'login_http_status':200,'id_present':True,'password_present':True,
             'blocked_navigation':False})
        self.assertEqual(account.safe_diagnostics({'phase':'private','page_kind':'private','login_http_status':True}),{})

    def test_submit_diagnostics_do_not_read_request_body(self):
        browser=account.AccountBrowser();browser.login_post_sent=False;browser.login_http_status=None
        request=Mock();request.url='https://nid.naver.com/nidlogin.login?private=hidden';request.method='POST'
        browser.request(request)
        response=Mock();response.url=request.url;response.request=request;response.status=200
        browser.response(response)
        self.assertTrue(browser.login_post_sent);self.assertEqual(browser.login_http_status,200)
        request.post_data.assert_not_called()

    def session_browser(self,authenticated=False,reason='login_requires_attention'):
        browser=account.AccountBrowser()
        browser.error=RuntimeError
        browser.blocked=False;browser.blocked_navigation=False
        browser.page=MagicMock();browser.page.url=account.HOME
        browser.page.goto.return_value=Mock(status=200)
        browser.inspect_page=Mock(return_value={
            'body_text_present':True,'id_field_visible':False,'password_field_visible':False,
            'known_reason':reason,'document_ready_state':'complete',
            'visible_input_count':1,'visible_button_count':1,'frame_count':0})
        selectors={name:MagicMock() for name in ('#gnb_my','#gnb_logout_button','#gnb_login_button')}
        for locator in selectors.values():
            locator.first=locator
            locator.count.return_value=1
            locator.is_visible.return_value=False
        selectors['#gnb_my'].is_visible.return_value=authenticated
        selectors['#gnb_login_button'].is_visible.return_value=not authenticated
        def open_menu(*args,**kwargs):
            selectors['#gnb_logout_button'].is_visible.return_value=authenticated
        selectors['#gnb_my'].click.side_effect=open_menu
        browser.page.locator.side_effect=lambda selector:selectors[selector]
        return browser,selectors

    def test_public_home_does_not_prove_a_signed_in_session(self):
        browser,selectors=self.session_browser()
        with patch.object(account.time,'sleep'):
            self.assertFalse(browser.verify_series_session())
        selectors['#gnb_my'].click.assert_not_called()
        selectors['#gnb_logout_button'].click.assert_not_called()

    def test_authenticated_menu_must_expose_logout_without_login(self):
        browser,selectors=self.session_browser(authenticated=True)
        with patch.object(account.time,'sleep'):
            self.assertTrue(browser.verify_series_session())
        selectors['#gnb_my'].click.assert_called_once()
        selectors['#gnb_logout_button'].click.assert_not_called()
        browser.page.goto.assert_called_once()
        self.assertEqual(browser.page.goto.call_args.args[0],account.HOME)

    def test_home_with_no_authentication_markers_is_unconfirmed(self):
        browser,selectors=self.session_browser()
        selectors['#gnb_login_button'].is_visible.return_value=False
        selectors['#gnb_my'].wait_for.side_effect=RuntimeError('synthetic timeout')
        with patch.object(account.time,'sleep'):
            self.assertFalse(browser.verify_series_session())
        selectors['#gnb_my'].click.assert_not_called()

    def test_contradictory_login_marker_is_not_accepted_as_authenticated(self):
        browser,selectors=self.session_browser(authenticated=True)
        selectors['#gnb_login_button'].is_visible.return_value=True
        with patch.object(account.time,'sleep'):
            self.assertFalse(browser.verify_series_session())

    def test_password_prompt_after_home_navigation_is_not_accepted(self):
        browser,selectors=self.session_browser(authenticated=True)
        before=browser.inspect_page.return_value
        after={**before,'password_field_visible':True}
        browser.inspect_page.side_effect=[before,after]
        with patch.object(account.time,'sleep'):
            self.assertFalse(browser.verify_series_session())
        selectors['#gnb_my'].click.assert_not_called()

    def test_known_verification_stops_before_any_account_menu_click(self):
        for reason in ('captcha_required','additional_verification','device_verification'):
            with self.subTest(reason=reason):
                browser,selectors=self.session_browser(authenticated=True,reason=reason)
                with patch.object(account.time,'sleep'),self.assertRaises(account.AccountAttention) as caught:
                    browser.verify_series_session()
                self.assertEqual(str(caught.exception),reason)
                selectors['#gnb_my'].click.assert_not_called()

    def test_blocked_access_cannot_become_authenticated(self):
        browser,selectors=self.session_browser(authenticated=True)
        browser.blocked=True
        with patch.object(account.time,'sleep'),self.assertRaises(account.AccountAttention) as caught:
            browser.verify_series_session()
        self.assertEqual(str(caught.exception),'access_stopped')
        selectors['#gnb_my'].click.assert_not_called()

    def test_missing_fields_are_reported_without_waiting_for_credential_locators(self):
        browser=account.AccountBrowser();browser.error=RuntimeError;browser.page=MagicMock()
        browser.page.evaluate.return_value={
            'body_text':'private-account unrelated page text','notice_text':'',
            'id_field_visible':False,'password_field_visible':False,
            'document_ready_state':'complete','visible_input_count':0,
            'visible_button_count':0,'frame_count':0}
        result=browser.inspect_page()
        self.assertFalse(result['id_field_visible']);self.assertFalse(result['password_field_visible'])
        self.assertTrue(result['body_text_present'])
        browser.page.get_by_role.assert_not_called()
        browser.page.locator.assert_not_called()
        browser.page.wait_for_timeout.assert_not_called()
        self.assertNotIn('body_text',result);self.assertNotIn('notice_text',result)
        self.assertNotIn('private-account',json.dumps(result))

    def test_page_notices_are_classified_without_exporting_private_text(self):
        browser=account.AccountBrowser();browser.error=RuntimeError;browser.page=MagicMock()
        browser.page.evaluate.return_value={
            'body_text':'private-account private-password',
            'notice_text':'private-account: 2단계 인증이 필요합니다',
            'id_field_visible':False,'password_field_visible':False,
            'document_ready_state':'complete','visible_input_count':0,
            'visible_button_count':1,'frame_count':0}
        result=browser.inspect_page()
        self.assertEqual(result['known_reason'],'additional_verification')
        encoded=json.dumps(result)
        for private in ('private-account','private-password'):
            self.assertNotIn(private,encoded)
        self.assertNotIn('body_text',result);self.assertNotIn('notice_text',result)

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
        browser.login.side_effect=account.AccountAttention('login_requires_attention',
            {'phase':'await_redirect','page_kind':'login_form','login_post_sent':True,'login_http_status':200,
             'account':'private-test-account'})
        env={'NAVER_ID':'private-test-account','NAVER_PASSWORD':'private-test-password'}
        result,saved,output,files=self.run_collect(env,factory)
        self.assertEqual(result['state'],'needs_attention')
        self.assertEqual(result['checked'],0);browser.login.assert_called_once();browser.read.assert_not_called()
        self.assertEqual(saved['runs']['series_account']['diagnostics'],
            {'phase':'await_redirect','page_kind':'login_form','login_post_sent':True,'login_http_status':200})
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
