"""Fill missing official Series publishers without changing any archived metrics.

Independent observations can enrich completed works once. A known publisher
is never put back in the weekly queue. Failed lookups are cooled down.
"""
from __future__ import annotations
import argparse
import copy
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path
from radar import ROOT, UA, PublicClient, PublicRedirect, NoRedirect, utcnow
from metadata import SeriesClient, parse_series, series_product_id, save_metadata
from works import build_works, normalized, base_title, same_creators


class PacedClient:
    """Only existing allowlisted endpoints, bounded concurrency and global pace."""
    def __init__(self, policy, interval=.35):
        self.policy=policy
        self.interval=max(interval,policy.robots.crawl_delay(UA) or 0)
        rate=policy.robots.request_rate(UA)
        if rate:self.interval=max(self.interval,rate.seconds/rate.requests)
        self.lock=threading.Lock();self.next=0;self.stopped=threading.Event()

    def read(self,path,**params):
        p=self.policy
        if path not in p.allowed:raise PermissionError('Endpoint is not allowlisted')
        url=p.base+path+'?'+urllib.parse.urlencode(params)
        if not p.robots.can_fetch(UA,url):raise PermissionError('Path is disallowed')
        with self.lock:
            if self.stopped.is_set():raise PermissionError('Collection paused after access response')
            delay=max(0,self.next-time.monotonic())
            if delay:time.sleep(delay)
            self.next=time.monotonic()+self.interval
        request=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'text/html,application/json'})
        try:
            with urllib.request.build_opener(NoRedirect()).open(request,timeout=20) as response:
                raw=response.read(12_000_001)
        except urllib.error.HTTPError as exc:
            if exc.code in (401,403,429):self.stopped.set()
            raise
        if len(raw)>12_000_000:raise ValueError('Unexpected response size')
        return raw.decode('utf-8')


def pending_targets(snapshot,metadata,force=False):
    targets=[];seen=set();cutoff=datetime.now(timezone.utc)-timedelta(days=7)
    for work in build_works(snapshot,metadata):
        if work.get('publisher'):continue
        serial=next((r for r in work['sources'] if r['platform']=='네이버시리즈'),None)
        platform='series' if serial else 'webtoon'
        ident=serial['title_id'] if serial else work.get('webtoon_id')
        if not ident or (platform,ident) in seen:continue
        old=metadata.get('publisher_details',{}).get(platform,{}).get(ident,{})
        if old and not force and datetime.fromisoformat(old['observed_at'])>cutoff:continue
        seen.add((platform,ident))
        targets.append({'platform':platform,'id':ident,'title':work['title'],'author':work['author'],
                        'status':work['status'],'interest':work.get('interest') or 0,
                        'downloads':work.get('downloads') or 0,'series_url':work.get('series_url',''),
                        'contents_no':metadata.get('webtoon',{}).get(ident,{}).get('contents_no')})
    return sorted(targets,key=lambda r:(r['status']=='완결',-r['interest'],-r['downloads'],r['id']))


def fetch_publisher(target,series,webtoon):
    result={'observed_at':utcnow(),'publisher':'','publisher_label':'','state':'pending'}
    ident=target['id']
    try:
        if target['platform']=='series':
            record=parse_series(series.read('/comic/detail.series',productNo=ident),ident)
        else:
            product=series_product_id(target.get('series_url',''))
            contents=target.get('contents_no')
            if not product and not contents:
                data=json.loads(webtoon.read('/api/article/list/info',titleId=ident))
                if str(data.get('titleId'))!=ident:raise ValueError('Webtoon identity mismatch')
                contents=data.get('contentsNo')
                result['identity_source_url']=f'https://comic.naver.com/api/article/list/info?titleId={ident}'
            if not product and not contents:
                return dict(result,state='no_series_link',reason='공식 웹툰 정보에 시리즈 공통 작품번호가 제공되지 않음')
            if product:
                html=series.read('/comic/detail.series',productNo=product)
            else:
                resolution=f'https://series.naver.com/comic/detail.series?originalProductId={contents}'
                result.update(contents_no=str(contents),resolution_url=resolution)
                try:html=series.read('/comic/detail.series',originalProductId=contents)
                except PublicRedirect as exc:
                    product=series_product_id(urllib.parse.urljoin(resolution,exc.target))
                    if not product:raise
                    html=series.read('/comic/detail.series',productNo=product)
                from bs4 import BeautifulSoup
                share=BeautifulSoup(html,'html.parser').select_one('#spiButton2[data-url]')
                canonical=series_product_id(share['data-url']) if share else None
                if not canonical or (product and product!=canonical):raise ValueError('Series identity mismatch')
                product=canonical
            record=parse_series(html,product)
            if normalized(base_title(record['title']))!=normalized(base_title(target['title'])) and not same_creators(target,record):
                return dict(result,state='identity_review',reason='공식 연결의 제목·작가 재확인 필요')
        result.update(publisher=record['publisher'],publisher_label=record['publisher_label'],
                      source_url=record['source_url'],series_id=record['title_id'],series_title=record['title'],
                      observed_at=utcnow(),state='confirmed' if record['publisher'] else 'not_listed')
    except PublicRedirect:
        result.update(state='unavailable',reason='공개 작품 페이지 확인 필요')
    except urllib.error.HTTPError as exc:
        result.update(state='unavailable',reason=f'HTTP {exc.code}')
    except (TimeoutError,urllib.error.URLError):
        result.update(state='unavailable',reason='일시적인 응답 지연')
    except (ValueError,PermissionError) as exc:
        result.update(state='unavailable',reason=str(exc)[:150])
    return result


def collect_publishers(root=ROOT,limit=None,workers=3,batch_size=100,publish=False,force=False):
    root=Path(root);path=root/'metadata_latest.json'
    metadata=json.loads(path.read_text('utf-8'));snapshot=json.loads((root/'catalog_latest.json').read_text('utf-8'))
    targets=pending_targets(snapshot,metadata,force)
    if limit is not None:targets=targets[:limit]
    series=PacedClient(SeriesClient());webtoon=PacedClient(PublicClient())
    started=utcnow();checked=confirmed=0
    def checkpoint(state):
        # Reload so metric collection and publisher enrichment cannot overwrite each other.
        current=json.loads(path.read_text('utf-8'))
        current['publisher_details']=copy.deepcopy(metadata.get('publisher_details',{}))
        current.setdefault('runs',{})['publishers']={'started_at':started,'updated_at':utcnow(),'state':state,
            'target':len(targets),'checked':checked,'confirmed':confirmed,
            'scope':'미확인 출판사 보완 · 완결 지표와 관측시각 보존'}
        save_metadata(current,path)
        print(json.dumps(current['runs']['publishers'],ensure_ascii=False),flush=True)
        if publish:
            import subprocess
            def git(*args):return subprocess.run(['git',*args],cwd=root,check=True)
            git('add','metadata_latest.json')
            changed=subprocess.run(['git','diff','--cached','--quiet'],cwd=root).returncode
            if changed==1:
                git('commit','-m','data: fill official Series publishers without refreshing archived metrics')
                git('pull','--rebase','origin','main');git('push','origin','HEAD:main')
            elif changed:raise RuntimeError('Unable to inspect publisher checkpoint')
    checkpoint('running' if targets else 'complete')
    with ThreadPoolExecutor(max_workers=max(1,min(workers,3))) as pool:
        for offset in range(0,len(targets),batch_size):
            if series.stopped.is_set() or webtoon.stopped.is_set():break
            futures={pool.submit(fetch_publisher,t,series,webtoon):t for t in targets[offset:offset+batch_size]}
            for future in as_completed(futures):
                target=futures[future];result=future.result()
                metadata.setdefault('publisher_details',{}).setdefault(target['platform'],{})[target['id']]=result
                checked+=1;confirmed+=result['state']=='confirmed'
            checkpoint('complete' if checked==len(targets) else 'running')
    if checked!=len(targets):
        checkpoint('paused');raise RuntimeError('Publisher lookup paused after access response; observations saved')
    return {'target':len(targets),'checked':checked,'confirmed':confirmed}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',default=str(ROOT));parser.add_argument('--limit',type=int)
    parser.add_argument('--batch-size',type=int,default=100);parser.add_argument('--publish',action='store_true')
    parser.add_argument('--force',action='store_true')
    args=parser.parse_args()
    if args.batch_size<1:parser.error('batch-size must be positive')
    collect_publishers(args.root,args.limit,batch_size=args.batch_size,publish=args.publish,force=args.force)
