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

    def test_security_confirmation_has_its_own_fixed_reason(self):
        for text in ('private-account: 보안 확인','private-account: 보안확인','보안\n확인'):
            with self.subTest(text=text):
                self.assertEqual(account.login_reason(text),'security_check_required')
        self.assertEqual(account.login_reason('보안 확인: 보안문자를 입력하세요'),'captcha_required')

    def test_captcha_diagnostic_accepts_only_boolean_and_never_attributes(self):
        for value in (True,False):
            self.assertEqual(account.safe_diagnostics({'captcha_element_visible':value}),
                             {'captcha_element_visible':value})
        for value in ('true','private-account',1,0,None,[],{}):
            with self.subTest(type=type(value).__name__):
                self.assertEqual(account.safe_diagnostics({'captcha_element_visible':value}),{})
        self.assertEqual(account.safe_diagnostics({
            'captcha_element_visible':True,'captcha_src':'https://private.example/captcha?id=private-account',
            'captcha_title':'private-password','captcha_attributes':['private-account']}),
            {'captcha_element_visible':True})

    def test_visible_captcha_marker_stops_even_without_recognized_notice(self):
        browser=account.AccountBrowser();browser.error=RuntimeError;browser.page=MagicMock()
        browser.page.evaluate.return_value={
            'body_text':'private-account private-password','notice_text':'',
            'control_texts':['확인','private-account 확인'],
            'captcha_element_visible':True,'id_field_visible':False,'password_field_visible':False,
            'document_ready_state':'complete','visible_input_count':1,'visible_button_count':3}
        state=browser.inspect_page()
        self.assertEqual(state['known_reason'],'captcha_required')
        self.assertEqual(state['dom_kind'],'verification')
        self.assertTrue(state['captcha_element_visible'])
        for result in (state,account.safe_diagnostics(state)):
            encoded=json.dumps(result,ensure_ascii=False)
            self.assertNotIn('private-account',encoded);self.assertNotIn('private-password',encoded)
            for key in ('body_text','notice_text','control_texts'):self.assertNotIn(key,result)
        browser.page.get_by_role.assert_not_called();browser.page.locator.assert_not_called()

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

    def test_ui_diagnostics_keep_only_exact_allowlisted_strings(self):
        safe=account.safe_diagnostics({
            'ui_actions':['확인','등록 안 함','private-account 확인','확인 private-password',
                          ' 확인','확인 ',None,3,True,{'name':'확인'},['확인']],
            'ui_topics':['security','account','private-account','security private-password',
                         'Security',' security',None,3,True,{'name':'security'},['security']],
            'button_labels':['private-account'],'body_text':'private-password'})
        self.assertEqual(safe,{'ui_actions':['확인','등록 안 함'],'ui_topics':['security','account']})
        encoded=json.dumps(safe,ensure_ascii=False)
        self.assertNotIn('private-account',encoded);self.assertNotIn('private-password',encoded)
        self.assertNotIn('button_labels',safe);self.assertNotIn('body_text',safe)

    def test_ui_diagnostics_reject_non_list_containers(self):
        for invalid in ('확인','security',('확인',),{'확인'},{'name':'확인'},True,1,None):
            with self.subTest(type=type(invalid).__name__):
                self.assertEqual(account.safe_diagnostics({'ui_actions':invalid,'ui_topics':invalid}),{})

    def test_ui_diagnostics_accept_every_documented_action_and_topic(self):
        actions=['확인','취소','다음','완료','나중에','나중에 하기','저장','저장 안 함',
                 '등록','등록하기','등록 안 함','예','아니오','아니요','로그인','다시 로그인',
                 '다른 계정으로 로그인','동의','인증','계속','닫기','도움말']
        topics=['security','device','browser','register','save','account','password','verify',
                'approve','consent','access','blocked','protect','age','phone','email','captcha',
                'retry','error','passkey','otp','ip','login','connection']
        for key,values in (('ui_actions',actions),('ui_topics',topics)):
            for value in values:
                with self.subTest(key=key,value=value):
                    self.assertEqual(account.safe_diagnostics({key:[value]}),{key:[value]})

    def test_ui_diagnostics_limit_each_list_to_twenty_safe_items(self):
        safe=account.safe_diagnostics({'ui_actions':['확인']*30,'ui_topics':['security']*30})
        self.assertGreater(len(safe['ui_actions']),0);self.assertLessEqual(len(safe['ui_actions']),20)
        self.assertGreater(len(safe['ui_topics']),0);self.assertLessEqual(len(safe['ui_topics']),20)
        self.assertTrue(all(value=='확인' for value in safe['ui_actions']))
        self.assertTrue(all(value=='security' for value in safe['ui_topics']))

    def test_public_diagnostics_drop_private_labels_even_if_page_fixture_contains_them(self):
        browser=account.AccountBrowser();browser.error=RuntimeError;browser.page=MagicMock()
        browser.page.evaluate.return_value={
            'body_text':'계정 private-account private-password','notice_text':'',
            'id_field_visible':False,'password_field_visible':False,
            'document_ready_state':'complete','visible_input_count':1,'visible_button_count':3,
            'control_texts':['확인','private-account','private-password 확인']}
        state=browser.inspect_page()
        safe=account.safe_diagnostics(state)
        self.assertEqual(safe['ui_actions'],['확인']);self.assertEqual(safe['ui_topics'],['account'])
        encoded=json.dumps(safe,ensure_ascii=False)
        self.assertNotIn('private-account',encoded);self.assertNotIn('private-password',encoded)
        self.assertNotIn('body_text',safe);self.assertNotIn('notice_text',safe)
        self.assertNotIn('control_texts',state)
        browser.page.get_by_role.assert_not_called();browser.page.locator.assert_not_called()

    def waiting_browser(self):
        browser=account.AccountBrowser();browser.error=RuntimeError;browser.page=MagicMock()
        browser.page.url=account.LOGIN
        browser.login_post_sent=False;browser.login_http_status=None
        browser.login_response_navigation=False
        browser.document_version=3;browser.submitted_document_version=3
        browser.blocked=False;browser.blocked_navigation=False
        browser.dialog_seen=False;browser.dialog_kind=None;browser.dialog_reason='login_requires_attention'
        browser.blocked_resource_count=0;browser.page_error_count=0
        browser.inspect_page=Mock(return_value={
            'known_reason':'login_requires_attention','password_field_visible':True,
            'id_field_visible':True,'body_text_present':True,'dom_kind':'password_form'})
        clock={'now':0.0,'pumps':0}
        def advance(milliseconds):
            clock['now']+=milliseconds/1000
            clock['pumps']+=1
        browser.page.wait_for_timeout.side_effect=advance
        return browser,clock,advance

    def test_delayed_post_waits_for_same_url_document_commit(self):
        browser,clock,advance=self.waiting_browser()
        initial_url=browser.page.url
        def events(milliseconds):
            advance(milliseconds)
            if clock['pumps']==2:browser.login_post_sent=True
            if clock['pumps']==3:
                browser.login_http_status=200;browser.login_response_navigation=True
            if clock['pumps']==4:browser.navigated(browser.page.main_frame)
        browser.page.wait_for_timeout.side_effect=events
        with patch.object(account.time,'monotonic',side_effect=lambda:clock['now']):
            browser.wait_for_login_response(timeout=2)
        self.assertEqual(clock['pumps'],4)
        self.assertEqual(browser.page.url,initial_url)
        browser.page.wait_for_load_state.assert_called_once()
        self.assertEqual(browser.page.wait_for_load_state.call_args.args,('domcontentloaded',))
        self.assertLessEqual(browser.page.wait_for_load_state.call_args.kwargs['timeout'],2000)
        browser.page.goto.assert_not_called();browser.page.get_by_role.assert_not_called()

    def test_fast_committed_post_is_not_missed_after_click_returns(self):
        browser,clock,_=self.waiting_browser()
        browser.login_post_sent=True;browser.login_http_status=200
        browser.login_response_navigation=True;browser.document_version=4
        with patch.object(account.time,'monotonic',side_effect=lambda:clock['now']):
            browser.wait_for_login_response(timeout=2)
        browser.page.wait_for_timeout.assert_not_called()
        browser.page.wait_for_load_state.assert_called_once()

    def test_xhr_response_waits_until_initial_password_form_changes(self):
        browser,clock,advance=self.waiting_browser()
        browser.login_post_sent=True;browser.login_http_status=200
        def events(milliseconds):
            advance(milliseconds)
            if clock['pumps']==2:
                browser.inspect_page.return_value={
                    'known_reason':'login_requires_attention','password_field_visible':False}
        browser.page.wait_for_timeout.side_effect=events
        with patch.object(account.time,'monotonic',side_effect=lambda:clock['now']):
            browser.wait_for_login_response(timeout=2)
        self.assertEqual(clock['pumps'],2)
        browser.page.goto.assert_not_called()

    def test_wait_timeout_distinguishes_unsent_from_unfinished_request(self):
        for sent,code in ((False,'login_submit_timeout'),(True,'login_response_timeout')):
            with self.subTest(sent=sent):
                browser,clock,_=self.waiting_browser();browser.login_post_sent=sent
                with patch.object(account.time,'monotonic',side_effect=lambda:clock['now']),\
                     self.assertRaises(account.AccountAttention) as caught:
                    browser.wait_for_login_response(timeout=1)
                self.assertEqual(str(caught.exception),code)
                self.assertGreaterEqual(clock['now'],1)
                browser.page.get_by_role.assert_not_called();browser.page.goto.assert_not_called()

    def test_wait_stops_on_block_or_verification_without_retry(self):
        for reason in ('access_stopped','captcha_required','security_check_required','additional_verification','credentials_rejected'):
            with self.subTest(reason=reason):
                browser,clock,_=self.waiting_browser()
                if reason=='access_stopped':browser.blocked=True
                else:browser.inspect_page.return_value['known_reason']=reason
                with patch.object(account.time,'monotonic',side_effect=lambda:clock['now']),\
                     self.assertRaises(account.AccountAttention) as caught:
                    browser.wait_for_login_response(timeout=1)
                self.assertEqual(str(caught.exception),reason)
                browser.page.wait_for_timeout.assert_not_called()
                browser.page.get_by_role.assert_not_called();browser.page.goto.assert_not_called()

    def test_login_timeout_submits_credentials_only_once(self):
        browser,clock,_=self.waiting_browser()
        username=Mock();password=Mock();submit=Mock()
        def role(role,*,name,exact):
            return {'아이디 또는 전화번호':username,'비밀번호':password,'로그인':submit}[name]
        browser.page.get_by_role.side_effect=role
        browser.verify_series_session=Mock()
        with patch.object(account.time,'monotonic',side_effect=lambda:clock['now']),\
             self.assertRaises(account.AccountAttention) as caught:
            browser.login('synthetic-user','synthetic-password')
        self.assertEqual(str(caught.exception),'login_submit_timeout')
        submit.click.assert_called_once();username.fill.assert_called_once();password.fill.assert_called_once()
        browser.verify_series_session.assert_not_called()
        self.assertNotIn('synthetic-user',json.dumps(caught.exception.diagnostics))
        self.assertNotIn('synthetic-password',json.dumps(caught.exception.diagnostics))

    def test_child_frame_navigation_cannot_complete_login_document_wait(self):
        browser,_,_=self.waiting_browser()
        browser.navigated(Mock())
        self.assertEqual(browser.document_version,3)
        browser.navigated(browser.page.main_frame)
        self.assertEqual(browser.document_version,4)

    def test_context_replacement_during_async_login_waits_for_new_document(self):
        browser,clock,advance=self.waiting_browser()
        ordinary=browser.inspect_page.return_value
        def inspect():
            if clock['pumps']==2:
                raise RuntimeError('Execution context was destroyed, most likely because of a navigation')
            return ordinary
        browser.inspect_page.side_effect=inspect
        def events(milliseconds):
            advance(milliseconds)
            if clock['pumps']==2:browser.login_post_sent=True
            if clock['pumps']==3:
                browser.login_http_status=200;browser.login_response_navigation=True
            if clock['pumps']==4:browser.navigated(browser.page.main_frame)
        browser.page.wait_for_timeout.side_effect=events
        with patch.object(account.time,'monotonic',side_effect=lambda:clock['now']):
            browser.wait_for_login_response(timeout=2)
        self.assertEqual(clock['pumps'],4)
        browser.page.get_by_role.assert_not_called();browser.page.goto.assert_not_called()

    def test_non_navigation_errors_are_not_silenced_by_polling(self):
        for method in ('wait_for_login_response','settled_page'):
            with self.subTest(method=method):
                browser,clock,_=self.waiting_browser()
                failure=RuntimeError('Unexpected browser evaluation failure')
                browser.inspect_page.side_effect=failure
                with patch.object(account.time,'monotonic',side_effect=lambda:clock['now']),\
                     self.assertRaises(RuntimeError) as caught:
                    getattr(browser,method)(timeout=1)
                self.assertIs(caught.exception,failure)
                browser.page.wait_for_timeout.assert_not_called()

    def test_settled_page_waits_through_loading_and_context_replacement(self):
        browser,clock,_=self.waiting_browser()
        complete={'document_ready_state':'complete','known_reason':'login_requires_attention',
                  'password_field_visible':False,'dom_kind':'content'}
        browser.inspect_page.side_effect=[{'document_ready_state':'loading'},
            RuntimeError('Cannot find context with specified id'),complete]
        with patch.object(account.time,'monotonic',side_effect=lambda:clock['now']):
            result=browser.settled_page(timeout=1)
        self.assertEqual(result,complete);self.assertEqual(clock['pumps'],2)
        browser.page.goto.assert_not_called();browser.page.get_by_role.assert_not_called()

    def test_settled_page_keeps_one_deadline_during_repeated_transitions(self):
        for transient in (False,True):
            with self.subTest(transient=transient):
                browser,clock,_=self.waiting_browser()
                if transient:
                    browser.inspect_page.side_effect=RuntimeError('Execution context was destroyed')
                else:
                    browser.inspect_page.return_value={'document_ready_state':'loading'}
                with patch.object(account.time,'monotonic',side_effect=lambda:clock['now']),\
                     self.assertRaises(account.AccountAttention) as caught:
                    browser.settled_page(timeout=1)
                self.assertEqual(str(caught.exception),'page_load_timeout')
                self.assertGreaterEqual(clock['now'],1);self.assertLessEqual(clock['now'],1.2)
                browser.page.goto.assert_not_called();browser.page.get_by_role.assert_not_called()

    def test_committed_response_load_wait_respects_callers_remaining_budget(self):
        browser,clock,_=self.waiting_browser()
        browser.login_post_sent=True;browser.login_http_status=200
        browser.login_response_navigation=True;browser.document_version=4
        with patch.object(account.time,'monotonic',side_effect=lambda:clock['now']):
            browser.wait_for_login_response(timeout=1)
        self.assertLessEqual(browser.page.wait_for_load_state.call_args.kwargs['timeout'],1000)

    def test_context_error_classifier_does_not_treat_other_failures_as_navigation(self):
        self.assertTrue(account.navigation_in_progress(RuntimeError('Execution context was destroyed')))
        self.assertTrue(account.navigation_in_progress(RuntimeError('Cannot find context with specified id')))
        self.assertFalse(account.navigation_in_progress(RuntimeError('Access denied')))
        self.assertFalse(account.navigation_in_progress(RuntimeError('Timeout 20000ms exceeded')))

    def redirect_browser(self):
        browser,clock,advance=self.waiting_browser()
        browser.inspect_page.return_value={
            'document_ready_state':'complete','known_reason':'login_requires_attention',
            'password_field_visible':False,'id_field_visible':False,
            'body_text_present':True,'dom_kind':'content'}
        return browser,clock,advance

    def test_delayed_series_redirect_is_allowed_to_finish_without_forced_navigation(self):
        browser,clock,advance=self.redirect_browser()
        expected=browser.inspect_page.return_value
        def events(milliseconds):
            advance(milliseconds)
            if clock['pumps']==3:browser.page.url=account.HOME
        browser.page.wait_for_timeout.side_effect=events
        with patch.object(account.time,'monotonic',side_effect=lambda:clock['now']):
            state=browser.wait_for_series_redirect(timeout=2)
        self.assertEqual(state,expected);self.assertEqual(clock['pumps'],3)
        browser.page.goto.assert_not_called();browser.page.get_by_role.assert_not_called()
        browser.page.locator.assert_not_called()

    def test_unfinished_naver_interstitial_times_out_without_forcing_home(self):
        browser,clock,_=self.redirect_browser()
        with patch.object(account.time,'monotonic',side_effect=lambda:clock['now']),\
             self.assertRaises(account.AccountAttention) as caught:
            browser.wait_for_series_redirect(timeout=1)
        self.assertEqual(str(caught.exception),'login_redirect_incomplete')
        self.assertGreaterEqual(clock['now'],1);self.assertLessEqual(clock['now'],1.2)
        self.assertEqual(browser.page.url,account.LOGIN)
        browser.page.goto.assert_not_called();browser.page.get_by_role.assert_not_called()
        browser.page.locator.assert_not_called()

    def test_redirect_wait_stops_for_verification_block_or_dialog(self):
        for reason in ('captcha_required','security_check_required','additional_verification','device_verification',
                       'credentials_rejected','access_stopped','dialog'):
            with self.subTest(reason=reason):
                browser,clock,_=self.redirect_browser()
                expected=reason
                if reason=='access_stopped':browser.blocked=True
                elif reason=='dialog':
                    browser.dialog_seen=True;browser.dialog_reason='additional_verification'
                    expected='additional_verification'
                else:browser.inspect_page.return_value['known_reason']=reason
                with patch.object(account.time,'monotonic',side_effect=lambda:clock['now']),\
                     self.assertRaises(account.AccountAttention) as caught:
                    browser.wait_for_series_redirect(timeout=1)
                self.assertEqual(str(caught.exception),expected)
                browser.page.wait_for_timeout.assert_not_called()
                browser.page.goto.assert_not_called();browser.page.get_by_role.assert_not_called()
                browser.page.locator.assert_not_called()

    def test_redirect_wait_rejects_returned_password_form(self):
        browser,clock,_=self.redirect_browser()
        browser.inspect_page.return_value.update(password_field_visible=True,dom_kind='password_form')
        with patch.object(account.time,'monotonic',side_effect=lambda:clock['now']),\
             self.assertRaises(account.AccountAttention) as caught:
            browser.wait_for_series_redirect(timeout=1)
        self.assertEqual(str(caught.exception),'auth_unconfirmed')
        browser.page.wait_for_timeout.assert_not_called()
        browser.page.goto.assert_not_called();browser.page.get_by_role.assert_not_called()

    def test_redirect_wait_survives_loading_and_context_replacement(self):
        browser,clock,advance=self.redirect_browser()
        complete=browser.inspect_page.return_value
        browser.inspect_page.side_effect=[{**complete,'document_ready_state':'loading'},
            RuntimeError('Execution context was destroyed'),complete]
        def events(milliseconds):
            advance(milliseconds)
            if clock['pumps']==2:browser.page.url=account.HOME
        browser.page.wait_for_timeout.side_effect=events
        with patch.object(account.time,'monotonic',side_effect=lambda:clock['now']):
            state=browser.wait_for_series_redirect(timeout=1)
        self.assertEqual(state,complete);self.assertEqual(clock['pumps'],2)
        browser.page.goto.assert_not_called();browser.page.get_by_role.assert_not_called()

    def test_redirect_wait_has_one_deadline_even_while_document_keeps_changing(self):
        for transient in (False,True):
            with self.subTest(transient=transient):
                browser,clock,_=self.redirect_browser()
                if transient:
                    browser.inspect_page.side_effect=RuntimeError('Cannot find context with specified id')
                else:browser.inspect_page.return_value.update(document_ready_state='loading')
                with patch.object(account.time,'monotonic',side_effect=lambda:clock['now']),\
                     self.assertRaises(account.AccountAttention) as caught:
                    browser.wait_for_series_redirect(timeout=1)
                self.assertEqual(str(caught.exception),'login_redirect_incomplete')
                self.assertGreaterEqual(clock['now'],1);self.assertLessEqual(clock['now'],1.2)
                browser.page.goto.assert_not_called();browser.page.get_by_role.assert_not_called()

    def test_redirect_wait_does_not_silence_unrelated_browser_errors(self):
        browser,clock,_=self.redirect_browser()
        failure=RuntimeError('Unexpected browser evaluation failure')
        browser.inspect_page.side_effect=failure
        with patch.object(account.time,'monotonic',side_effect=lambda:clock['now']),\
             self.assertRaises(RuntimeError) as caught:
            browser.wait_for_series_redirect(timeout=1)
        self.assertIs(caught.exception,failure)
        browser.page.wait_for_timeout.assert_not_called();browser.page.goto.assert_not_called()

    def test_login_never_forces_home_when_ordinary_redirect_does_not_complete(self):
        browser,_,_=self.redirect_browser()
        browser.wait_for_login_response=Mock()
        browser.wait_for_series_redirect=Mock(side_effect=account.AccountAttention('login_redirect_incomplete'))
        browser.verify_series_session=Mock()
        with self.assertRaises(account.AccountAttention) as caught:
            browser.login('synthetic-user','synthetic-password')
        self.assertEqual(str(caught.exception),'login_redirect_incomplete')
        browser.wait_for_series_redirect.assert_called_once()
        browser.verify_series_session.assert_not_called()
        browser.page.goto.assert_called_once_with(account.LOGIN,wait_until='domcontentloaded')
        browser.page.get_by_role.return_value.click.assert_called_once()

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
            def wait_visible(*args,_locator=locator,**kwargs):
                if not _locator.is_visible():raise RuntimeError('synthetic visibility timeout')
            locator.wait_for.side_effect=wait_visible
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

    def test_async_account_menu_replaces_initial_login_template_before_verification(self):
        browser,selectors=self.session_browser(authenticated=True)
        selectors['#gnb_my'].is_visible.return_value=False
        selectors['#gnb_login_button'].is_visible.return_value=True
        def finish_account_lookup(*args,**kwargs):
            self.assertTrue(selectors['#gnb_login_button'].is_visible())
            selectors['#gnb_my'].is_visible.return_value=True
            selectors['#gnb_login_button'].is_visible.return_value=False
        selectors['#gnb_my'].wait_for.side_effect=finish_account_lookup
        self.assertTrue(browser.verify_series_session())
        selectors['#gnb_my'].wait_for.assert_called_once_with(state='visible',timeout=8000)
        selectors['#gnb_my'].click.assert_called_once()
        selectors['#gnb_logout_button'].click.assert_not_called()

    def test_hidden_logout_template_does_not_prove_authentication(self):
        browser,selectors=self.session_browser()
        self.assertEqual(selectors['#gnb_logout_button'].count(),1)
        self.assertFalse(selectors['#gnb_logout_button'].is_visible())
        self.assertFalse(browser.verify_series_session())
        selectors['#gnb_my'].click.assert_not_called()
        selectors['#gnb_logout_button'].click.assert_not_called()

    def test_ready_account_menu_without_visible_logout_is_unconfirmed(self):
        browser,selectors=self.session_browser(authenticated=True)
        selectors['#gnb_my'].click.side_effect=None
        self.assertFalse(browser.verify_series_session())
        selectors['#gnb_my'].click.assert_called_once()
        selectors['#gnb_logout_button'].wait_for.assert_called_once_with(state='visible',timeout=3000)

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
        selectors['#gnb_my'].click.assert_called_once()
        self.assertTrue(selectors['#gnb_logout_button'].is_visible())
        self.assertTrue(selectors['#gnb_login_button'].is_visible())

    def test_password_prompt_after_home_navigation_is_not_accepted(self):
        browser,selectors=self.session_browser(authenticated=True)
        before=browser.inspect_page.return_value
        after={**before,'password_field_visible':True}
        browser.inspect_page.side_effect=[before,after]
        with patch.object(account.time,'sleep'):
            self.assertFalse(browser.verify_series_session())
        selectors['#gnb_my'].click.assert_not_called()

    def test_known_verification_stops_before_any_account_menu_click(self):
        for reason in ('captcha_required','security_check_required','additional_verification','device_verification'):
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
