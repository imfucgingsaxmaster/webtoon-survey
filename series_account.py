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


class AccountAttention(RuntimeError):
    """A fixed, non-sensitive status code; never include browser exception text."""


def login_reason(text):
    # Keep only a fixed reason code, never the page's account-specific text.
    for words,code in [
        (('자동입력 방지','자동 입력 방지','보안 문자','보안문자'),'captcha_required'),
        (('2단계 인증','2단계인증','인증번호','인증 번호'),'additional_verification'),
        (('해외 로그인','해외 IP','새로운 기기','새 기기'),'device_verification'),
        (('비밀번호를 잘못','비밀번호가 일치','등록되지 않은 아이디'),'credentials_rejected')]:
        if any(word in text for word in words):return code
    return 'login_requires_attention'


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
            self.page.on('response',self.response)
            self.blocked=False
            return self
        except Error:
            self.__exit__(None,None,None)
            raise AccountAttention('browser_unavailable') from None

    def route(self,route):
        host=urlsplit(route.request.url).hostname or ''
        allowed=host in ('nid.naver.com','series.naver.com') or host.endswith('.pstatic.net')
        if not allowed or route.request.resource_type in ('image','media','font'):
            route.abort()
        else:route.continue_()

    def response(self,response):
        if (response.request.is_navigation_request() and
            urlsplit(response.url).hostname in ('series.naver.com','nid.naver.com') and
            response.status in (403,429)):
            self.blocked=True

    def login(self,username,password):
        try:
            self.page.goto(LOGIN,wait_until='domcontentloaded')
            if self.blocked or urlsplit(self.page.url).hostname!='nid.naver.com':
                raise AccountAttention('login_requires_attention')
            self.page.get_by_role('textbox',name='아이디 또는 전화번호',exact=True).fill(username)
            self.page.get_by_role('textbox',name='비밀번호',exact=True).fill(password)
            self.page.get_by_role('button',name='로그인',exact=True).click()
            # CAPTCHA, OTP, device and age verification are never handled here.
            self.page.wait_for_url(HOME,timeout=30000,wait_until='domcontentloaded')
            if self.blocked:raise AccountAttention('access_stopped')
        except self.error:
            try:reason=login_reason(self.page.locator('body').inner_text(timeout=2000))
            except self.error:reason='login_requires_attention'
            raise AccountAttention(reason) from None

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
        return {'state':'needs_attention','reason_code':str(exc)}
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
