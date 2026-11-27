"""Optional, owner-authorized Series login using ephemeral runner secrets.

Only known comic product pages and aggregate catalogue fields are read.
Passwords, cookies, HTML, screenshots and browser traces are never saved.
Additional verification, forbidden responses and rate limits stop the session.
"""
from __future__ import annotations
import argparse
import copy
import gzip
import json
import os
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

from collection_policy import frozen_ids
from metadata import SeriesClient, parse_series, refresh_boundary, save_metadata
from radar import ROOT, UA, utcnow
from work_profiles import series_profile

HOME='https://series.naver.com/comic/home.series'
LOGIN='https://nid.naver.com/nidlogin.login?url='+quote(HOME,safe='')
UI_ACTIONS={'확인','취소','다음','완료','나중에','나중에 하기','저장','저장 안 함','등록','등록하기',
            '등록 안 함','예','아니오','아니요','로그인','다시 로그인','다른 계정으로 로그인','동의',
            '인증','계속','닫기','도움말'}
UI_TOPICS={'security':('보안',),'device':('기기','기계'),'browser':('브라우저',),
           'register':('등록',),'save':('저장',),'account':('계정','아이디'),
           'password':('비밀번호',),'verify':('인증',),'approve':('승인',),
           'consent':('동의','약관'),'access':('접근','접속'),'blocked':('차단','제한'),
           'protect':('보호',),'age':('연령','나이','성인'),'phone':('전화','휴대폰'),
           'email':('이메일','메일'),'captcha':('자동입력','보안문자','로봇'),
           'retry':('다시',),'error':('오류','실패'),'passkey':('패스키',),
           'otp':('OTP','일회용'),'ip':('IP',),'login':('로그인',),'connection':('연결',)}


class AccountAttention(RuntimeError):
    """A fixed, non-sensitive status code; never include browser exception text."""
    def __init__(self, code, diagnostics=None):
        super().__init__(code)
        self.diagnostics=diagnostics or {}


def login_page_kind(url):
    parts=urlsplit(url)
    if parts.hostname=='nid.naver.com':
        return 'login_form' if parts.path=='/nidlogin.login' else 'naver_verification'
    if parts.hostname=='series.naver.com':
        return 'series_home' if parts.path=='/comic/home.series' else 'series_other'
    if parts.hostname=='www.naver.com':return 'naver_home'
    return 'other'


def login_reason(text):
    # Keep only a fixed reason code, never the page's account-specific text.
    text=' '.join(text.split())
    for words,code in [
        (('자동화된 접근','자동화된 접속','접근이 제한','접속이 제한'),'access_restricted'),
        (('보호조치','보호 조치','계정이 정지'),'account_protection'),
        (('잘못된 요청','잘못된 접근','유효하지 않은 요청','Invalid request'),'invalid_login_request'),
        (('아이디를 입력','비밀번호를 입력','필수 정보입니다'),'input_required'),
        (('자동입력 방지','자동 입력 방지','보안 문자','보안문자','로봇이 아닙니다','로봇이 아님','영수증 문제','CAPTCHA'),'captcha_required'),
        (('보안 확인','보안확인'),'security_check_required'),
        (('2단계 인증','2단계인증','인증번호','인증 번호'),'additional_verification'),
        (('해외 로그인','해외 IP','새로운 기기','새 기기'),'device_verification'),
        (('비밀번호를 잘못','비밀번호가 일치','등록되지 않은 아이디','비밀번호가 잘못','비밀번호를 확인','비밀번호를 다시 확인','아이디를 확인','로그인 전용 아이디'),'credentials_rejected'),
        (('잠시 후 다시','일시적인 오류','일시적으로','temporarily unavailable'),'temporarily_unavailable')]:
        if any(word in text for word in words):return code
    return 'login_requires_attention'


def safe_diagnostics(value):
    """Only enumerated stages, flags and HTTP statuses may reach public logs."""
    result={}
    enums={'phase':{'open_form','enter_credentials','submit_form','await_redirect','verify_session'},
           'page_kind':{'login_form','naver_verification','series_home','series_other','naver_home','other'},
           'document_ready_state':{'loading','interactive','complete'},
           'dom_kind':{'password_form','verification','content','blank'},
           'post_submit_dom':{'password_form','verification','content','blank'},
           'dialog_kind':{'alert','confirm','prompt','beforeunload'}}
    for key,allowed in enums.items():
        if isinstance(value.get(key),str) and value[key] in allowed:result[key]=value[key]
    for key in ('blocked_navigation','access_stopped','login_post_sent','id_present','password_present',
                'id_field_visible','password_field_visible','body_text_present','dialog_seen','login_response_navigation',
                'account_menu_visible','login_button_visible','logout_button_visible','captcha_element_visible'):
        if type(value.get(key)) is bool:result[key]=value[key]
    for key in ('visible_input_count','visible_button_count','frame_count','blocked_resource_count','page_error_count'):
        if type(value.get(key)) is int and 0<=value[key]<=10000:result[key]=value[key]
    status=value.get('login_http_status')
    if type(status) is int and 100<=status<=599:result['login_http_status']=status
    for key,allowed in (('ui_actions',UI_ACTIONS),('ui_topics',UI_TOPICS)):
        if isinstance(value.get(key),list):
            result[key]=[item for item in value[key] if isinstance(item,str) and item in allowed][:20]
    return result


def navigation_in_progress(error):
    # Inspect only fixed runtime phrases; never return raw browser errors.
    return any(text in str(error) for text in (
        'Execution context was destroyed','Cannot find context with specified id',
        'most likely because of a navigation'))


def pending_series(snapshot, metadata, since):
    frozen=frozen_ids(snapshot,metadata)['series']
    blocked=set(metadata.get('unavailable',{}).get('series',{}))
    blocked.update(c['series_id'] for c in metadata.get('series_checks',{}).values()
                   if c.get('state')=='linked_restricted' and c.get('series_id'))
    result=[]
    for ident in sorted(blocked-frozen):
        if not str(ident).isdecimal() or int(ident)<1:continue
        row=metadata.get('series',{}).get(ident,{})
        fresh=bool(row.get('observed_at') and refresh_boundary(row['observed_at'])>=since)
        if fresh and row.get('metrics',{}).get('downloads',{}).get('value') is not None:continue
        result.append(ident)
    return result


def record_series(cache, snapshot, ident, html):
    row=parse_series(html,ident)
    profile=series_profile(html,ident)
    old=cache.get('series',{}).get(ident,{})
    for key in ('original_product_id','resolution_url','resolved_at','verified_webtoon_title'):
        if key in old:row[key]=old[key]
    active={r['title_id']:r for r in snapshot['titles'] if r.get('status')!='완결'}
    for wid,check in cache.get('series_checks',{}).items():
        if wid not in active or check.get('series_id')!=ident:continue
        contents=cache.get('webtoon',{}).get(wid,{}).get('contents_no')
        if contents:
            row.update(original_product_id=str(contents),verified_webtoon_title=active[wid]['title'])
        check.update(state='linked',observed_at=row['observed_at'])
        check.pop('reason',None)
        cache.setdefault('profiles',{})['webtoon:'+wid]=copy.deepcopy(profile)
    cache.setdefault('series',{})[ident]=row
    cache.setdefault('unavailable',{}).setdefault('series',{}).pop(ident,None)
    return row


class AccountBrowser:
    def __init__(self):
        self.engine=None;self.browser=None;self.context=None

    def __enter__(self):
        from playwright.sync_api import sync_playwright, Error
        self.error=Error
        try:
            self.engine=sync_playwright().start()
            self.browser=self.engine.chromium.launch(headless=True)
            self.context=self.browser.new_context(locale='ko-KR',service_workers='block')
            # No session state, tracing, screenshots or downloads are persisted.
            self.context.route('**/*',self.route)
            self.page=self.context.new_page()
            self.page.set_default_timeout(20000)
            self.login_post_sent=False
            self.login_http_status=None
            self.login_response_navigation=False
            self.document_version=0
            self.blocked_resource_count=0
            self.page_error_count=0
            self.dialog_seen=False
            self.dialog_kind=None
            self.dialog_reason='login_requires_attention'
            self.page.on('request',self.request)
            self.page.on('response',self.response)
            self.page.on('framenavigated',self.navigated)
            self.page.on('dialog',self.dialog)
            self.page.on('pageerror',self.page_error)
            self.blocked=False
            self.blocked_navigation=False
            return self
        except Error:
            self.__exit__(None,None,None)
            raise AccountAttention('browser_unavailable') from None

    def route(self,route):
        host=urlsplit(route.request.url).hostname or ''
        # Let the ordinary Naver sign-in page load its first-party verification
        # resources. Blocking its images/scripts can prevent form submission.
        # A visible CAPTCHA or other owner verification still stops collection.
        allowed=(host=='naver.com' or host.endswith('.naver.com') or
                 host=='pstatic.net' or host.endswith('.pstatic.net'))
        if not allowed:
            self.blocked_resource_count=getattr(self,'blocked_resource_count',0)+1
            if route.request.is_navigation_request():self.blocked_navigation=True
            route.abort()
        else:route.continue_()

    def page_error(self,_error):
        self.page_error_count+=1

    def navigated(self,frame):
        if frame==self.page.main_frame:self.document_version+=1

    def dialog(self,dialog):
        self.dialog_seen=True
        self.dialog_kind=dialog.type
        self.dialog_reason=login_reason(dialog.message)
        # Match Playwright's default dismissal; never confirm a security prompt.
        dialog.dismiss()

    def inspect_page(self):
        """Classify the visible DOM without returning text or any input values."""
        state=self.page.evaluate('''() => {
          const visible=e => !!e && !!(e.getClientRects().length) && getComputedStyle(e).visibility!=='hidden';
          const inputs=Array.from(document.querySelectorAll('input')).filter(visible);
          const notices=Array.from(document.querySelectorAll('[role="alert"],.error_message,.error_text,.error')).filter(visible);
          return {document_ready_state:document.readyState,
            account_menu_visible:visible(document.querySelector('#gnb_my')),
            login_button_visible:visible(document.querySelector('#gnb_login_button')),
            logout_button_visible:visible(document.querySelector('#gnb_logout_button')),
            captcha_element_visible:Array.from(document.querySelectorAll('img,iframe,input')).filter(visible).some(e=>
                /captcha|chptcha/i.test([e.id,e.getAttribute('name'),e.getAttribute('src'),e.getAttribute('title')].join(' '))),
            body_text:document.body?.innerText || '',
            notice_text:notices.map(e=>e.innerText || '').join(' '),
            control_texts:Array.from(document.querySelectorAll('button,[role="button"],a')).filter(visible).map(e=>e.innerText || ''),
            id_field_visible:inputs.some(e=>e.id==='id'||e.autocomplete==='username'),
            password_field_visible:inputs.some(e=>e.type==='password'),
            visible_input_count:inputs.length,
            visible_button_count:Array.from(document.querySelectorAll('button,[role="button"]')).filter(visible).length};
        }''')
        body=state.pop('body_text','');notice=state.pop('notice_text','')
        controls=state.pop('control_texts',[])
        state['ui_actions']=sorted({label for text in controls
            if (label:=' '.join(text.split())) in UI_ACTIONS})
        state['ui_topics']=[key for key,words in UI_TOPICS.items() if any(word in body for word in words)]
        state['body_text_present']=bool(body.strip())
        reason=login_reason(notice)
        if reason=='login_requires_attention' and not state.get('password_field_visible'):
            reason=login_reason(body)
        state['known_reason']=reason
        if state.get('captcha_element_visible'):state['known_reason']='captcha_required'
        reason=state['known_reason']
        state['frame_count']=len(self.page.frames)
        state['dom_kind']=('verification' if reason!='login_requires_attention' else
            'password_form' if state.get('password_field_visible') else
            'content' if state['body_text_present'] else 'blank')
        return state

    def verify_series_session(self):
        """Verify the existing session with the real Series account menu."""
        if self.blocked:raise AccountAttention('access_stopped')
        state=self.inspect_page()
        if state['known_reason']!='login_requires_attention':raise AccountAttention(state['known_reason'])
        if state['password_field_visible']:return False
        self.page.goto(HOME,wait_until='domcontentloaded')
        if self.blocked:raise AccountAttention('access_stopped')
        if urlsplit(self.page.url).hostname!='series.naver.com':return False
        state=self.inspect_page()
        if state['known_reason']!='login_requires_attention':raise AccountAttention(state['known_reason'])
        if state['password_field_visible']:return False
        # GNB initially shows the login template until its asynchronous account
        # lookup finishes. Wait for the positive account marker before judging
        # the login button; the final check still rejects contradictory UI.
        account_menu=self.page.locator('#gnb_my')
        try:account_menu.wait_for(state='visible',timeout=8000)
        except self.error:return False
        account_menu.click()
        try:self.page.locator('#gnb_logout_button').wait_for(state='visible',timeout=3000)
        except self.error:return False
        return (self.page.locator('#gnb_logout_button').is_visible() and
                not self.page.locator('#gnb_login_button').is_visible())

    def request(self,request):
        parts=urlsplit(request.url)
        if request.method=='POST' and parts.hostname=='nid.naver.com' and parts.path=='/nidlogin.login':
            self.login_post_sent=True

    def response(self,response):
        parts=urlsplit(response.url)
        if response.request.method=='POST' and parts.hostname=='nid.naver.com' and parts.path=='/nidlogin.login':
            self.login_http_status=response.status
            self.login_response_navigation=response.request.is_navigation_request()
        if (response.request.is_navigation_request() and
            urlsplit(response.url).hostname in ('series.naver.com','nid.naver.com') and
            response.status in (403,429)):
            self.blocked=True

    def wait_for_login_response(self,timeout=30):
        """Wait for Naver's asynchronous form submission without clicking twice."""
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if self.blocked:raise AccountAttention('access_stopped')
            if self.dialog_seen:raise AccountAttention(self.dialog_reason)
            try:
                if self.login_http_status is not None:
                    if self.login_response_navigation and self.document_version>self.submitted_document_version:
                        remaining_ms=max(1,int((deadline-time.monotonic())*1000))
                        self.page.wait_for_load_state('domcontentloaded',timeout=min(10000,remaining_ms))
                        return
                    if not self.login_response_navigation:
                        state=self.inspect_page()
                        if state['known_reason']!='login_requires_attention':raise AccountAttention(state['known_reason'])
                        if not state['password_field_visible']:return
                else:
                    state=self.inspect_page()
                    if state['known_reason']!='login_requires_attention':raise AccountAttention(state['known_reason'])
            except self.error as exc:
                if not navigation_in_progress(exc):raise
            self.page.wait_for_timeout(200)
        raise AccountAttention('login_response_timeout' if self.login_post_sent else 'login_submit_timeout')

    def settled_page(self,timeout=10):
        """Read after document replacement, retaining the original timeout."""
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if self.blocked:raise AccountAttention('access_stopped')
            if self.dialog_seen:raise AccountAttention(self.dialog_reason)
            try:
                state=self.inspect_page()
                if state['document_ready_state']!='loading':return state
            except self.error as exc:
                if not navigation_in_progress(exc):raise
            self.page.wait_for_timeout(200)
        raise AccountAttention('page_load_timeout')

    def wait_for_series_redirect(self,timeout=30):
        """Allow Naver to finish authentication and redirect itself to Series.

        Navigating manually from a post-login interstitial can interrupt the
        normal sign-in sequence or conceal an owner verification requirement.
        """
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if self.blocked:raise AccountAttention('access_stopped')
            if self.dialog_seen:raise AccountAttention(self.dialog_reason)
            try:
                state=self.inspect_page()
                if state['known_reason']!='login_requires_attention':
                    raise AccountAttention(state['known_reason'])
                if state['document_ready_state']!='loading':
                    if state['password_field_visible']:raise AccountAttention('auth_unconfirmed')
                    if urlsplit(self.page.url).hostname=='series.naver.com':return state
            except self.error as exc:
                if not navigation_in_progress(exc):raise
            self.page.wait_for_timeout(200)
        raise AccountAttention('login_redirect_incomplete')

    def login(self,username,password):
        phase='open_form'
        post_submit_dom=None
        try:
            self.page.goto(LOGIN,wait_until='domcontentloaded')
            if self.blocked or urlsplit(self.page.url).hostname!='nid.naver.com':
                raise AccountAttention('login_requires_attention')
            phase='enter_credentials'
            self.page.get_by_role('textbox',name='아이디 또는 전화번호',exact=True).fill(username)
            self.page.get_by_role('textbox',name='비밀번호',exact=True).fill(password)
            phase='submit_form'
            self.submitted_document_version=self.document_version
            self.page.get_by_role('button',name='로그인',exact=True).click()
            # A Naver message/interstitial may share the login URL. Inspect its
            # DOM, then verify an actual authenticated Series menu, not a URL.
            phase='await_redirect'
            self.wait_for_login_response()
            state=self.wait_for_series_redirect()
            post_submit_dom=state['dom_kind']
            if self.dialog_seen:raise AccountAttention(self.dialog_reason)
            if state['known_reason']!='login_requires_attention':raise AccountAttention(state['known_reason'])
            phase='verify_session'
            if not self.verify_series_session():raise AccountAttention('auth_unconfirmed')
        except (self.error,AccountAttention) as exc:
            reason=str(exc) if isinstance(exc,AccountAttention) else 'login_requires_attention'
            state={}
            try:
                state=self.inspect_page()
                if state['known_reason']!='login_requires_attention':reason=state['known_reason']
            except self.error:pass
            diagnostics={'phase':phase,'page_kind':login_page_kind(self.page.url),
                         'blocked_navigation':self.blocked_navigation,'access_stopped':self.blocked,
                         'login_post_sent':self.login_post_sent,'login_http_status':self.login_http_status,
                         'login_response_navigation':self.login_response_navigation,
                         'dialog_seen':self.dialog_seen,'dialog_kind':self.dialog_kind,
                         'blocked_resource_count':self.blocked_resource_count,'page_error_count':self.page_error_count,
                         'post_submit_dom':post_submit_dom,**state}
            raise AccountAttention(reason,safe_diagnostics(diagnostics)) from None

    def read(self,ident):
        if self.blocked:raise AccountAttention('access_stopped')
        url='https://series.naver.com/comic/detail.series?productNo='+ident
        try:
            response=self.page.goto(url,wait_until='domcontentloaded')
            if self.blocked:raise AccountAttention('access_stopped')
            if self.page.url!=url or (response and response.status in (401,403,429)):
                raise AccountAttention('verification_required')
            self.page.locator('#content .end_head h2').wait_for()
            return self.page.content()
        except self.error:
            raise AccountAttention('page_requires_attention') from None

    def __exit__(self,*args):
        if self.context:self.context.close()
        if self.browser:self.browser.close()
        if self.engine:self.engine.stop()


def check_login(browser_factory=AccountBrowser):
    username=os.environ.get('NAVER_ID','');password=os.environ.get('NAVER_PASSWORD','')
    if not username or not password:return {'state':'not_configured'}
    try:
        with browser_factory() as browser:browser.login(username,password)
        return {'state':'ready'}
    except AccountAttention as exc:
        result={'state':'needs_attention','reason_code':str(exc)}
        if exc.diagnostics:result['diagnostics']=safe_diagnostics(exc.diagnostics)
        return result
    finally:username=password=None


def collect(root=ROOT,refresh_since=None,publish=False,browser_factory=AccountBrowser):
    root=Path(root);path=root/'metadata_latest.json'
    cache=json.loads(path.read_bytes());snapshot=json.loads((root/'catalog_latest.json').read_bytes())
    since=refresh_boundary(refresh_since or cache.get('runs',{}).get('weekly_verification',{}).get('started_at') or utcnow())
    ids=pending_series(snapshot,cache,since)
    run={'started_at':utcnow(),'refresh_since':since.isoformat(),'target':len(ids),
         'checked':0,'updated':0,'unavailable':0,'state':'running'}
    def save(state):
        run.update(state=state,updated_at=utcnow(),remaining=len(ids)-run['checked'])
        cache.setdefault('runs',{})['series_account']=copy.deepcopy(run)
        save_metadata(cache,path)
        if publish:
            from data_publish import publish_data
            publish_data(root,'data: supplement Series catalogue observations')

    username=os.environ.get('NAVER_ID','');password=os.environ.get('NAVER_PASSWORD','')
    if not username or not password:
        save('not_configured')
        print('::notice::Series account supplement is not configured; public refresh is retained.')
        return run
    if not ids:save('complete');return run
    policy=SeriesClient()
    interval=max(2.0,policy.robots.crawl_delay(UA) or 0)
    rate=policy.robots.request_rate(UA)
    if rate:interval=max(interval,rate.seconds/rate.requests)
    try:
        with browser_factory() as browser:
            browser.login(username,password)
            for ident in ids:
                url='https://series.naver.com/comic/detail.series?productNo='+ident
                if not policy.robots.can_fetch(UA,url):raise AccountAttention('policy_stopped')
                time.sleep(interval)
                html=browser.read(ident)
                try:
                    record_series(cache,snapshot,ident,html)
                    run['updated']+=1
                except ValueError:
                    # Identity checks and original observations remain intact.
                    run['unavailable']+=1
                finally:
                    html=None
                run['checked']+=1
                if run['checked']%20==0:save('running')
        save('partial' if run['unavailable'] else 'complete')
    except AccountAttention as exc:
        run['reason_code']=str(exc)
        if exc.diagnostics:run['diagnostics']=safe_diagnostics(exc.diagnostics)
        save('needs_attention')
        print('::warning::Series account supplement stopped. Sign-in or additional verification needs owner attention; existing observations are preserved.')
    finally:
        username=password=None
        if run['updated']:
            folder=root/'metadata_history';folder.mkdir(exist_ok=True)
            stamp=utcnow().replace(':','').replace('+0000','Z')
            (folder/(stamp+'_series_supplement.json.gz')).write_bytes(gzip.compress(json.dumps(cache,ensure_ascii=False).encode(),mtime=0))
            from history_index import build_index
            build_index(root)
            if publish:
                from data_publish import publish_data
                publish_data(root,'data: preserve supplemented Series observation history')
    print(json.dumps(run,ensure_ascii=False),flush=True)
    return run


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',default=str(ROOT));parser.add_argument('--refresh-since')
    parser.add_argument('--publish',action='store_true')
    parser.add_argument('--check-login',action='store_true',help='Check sign-in once without reading or modifying catalogue data')
    args=parser.parse_args()
    result=check_login() if args.check_login else collect(args.root,args.refresh_since,args.publish)
    if args.check_login:print(json.dumps(result))
    if result['state'] in ('needs_attention','partial','not_configured'):raise SystemExit(2)
