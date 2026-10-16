"""Read public GitHub observations and fill searched titles without losing cache."""
from __future__ import annotations
import copy
import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from radar import ROOT, PublicClient, PublicRedirect, NoRedirect, utcnow, validate_snapshot
from metadata import load_metadata, parse_webtoon, SeriesClient
from trends import archive_observation

REPOSITORY='imfucgingsaxmaster/webtoon-survey'
_lock=threading.RLock()
_last_lookup=0.0

def read_json(path,default):
    return json.loads(Path(path).read_text(encoding='utf-8')) if Path(path).exists() else copy.deepcopy(default)

def write_json(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    temporary.replace(path)

def checked_metadata(value):
    if value.get('schema_version')!=1:raise ValueError('Invalid public observation schema')
    for platform in ('webtoon','series'):
        rows=value.get(platform,{})
        if not isinstance(rows,dict):raise ValueError('Invalid records')
        for ident,row in rows.items():
            if not ident.isdecimal() or str(row.get('title_id'))!=ident:raise ValueError('Invalid source identity')
            datetime.fromisoformat(row['observed_at'])
            for metric in row['metrics'].values():
                n=metric['value']
                if n is not None and (type(n) is not int or n<0):raise ValueError('Invalid count')
    return value

def merge_metadata(*values):
    result={'schema_version':1,'webtoon':{},'series':{},'series_checks':{},'unavailable':{'webtoon':{},'series':{}},'runs':{}}
    for value in values:
        for platform in ('webtoon','series'):
            for ident,row in value.get(platform,{}).items():
                old=result[platform].get(ident)
                if not old or row['observed_at']>old['observed_at']:
                    result[platform][ident]=copy.deepcopy(row)
                    if platform=='series' and old:
                        for key in ('original_product_id','resolution_url','resolved_at'):
                            if key not in row and key in old:result[platform][ident][key]=old[key]
                elif platform=='series' and row.get('resolved_at','')>old.get('resolved_at',''):
                    for key in ('original_product_id','resolution_url','resolved_at'):
                        if key in row:result[platform][ident][key]=row[key]
            for ident,row in value.get('unavailable',{}).get(platform,{}).items():
                old=result['unavailable'][platform].get(ident)
                if not old or row['observed_at']>old['observed_at']:result['unavailable'][platform][ident]=copy.deepcopy(row)
        if value.get('updated_at','')>=result.get('updated_at',''):
            result['updated_at']=value.get('updated_at','')
        for key,run in value.get('runs',{}).items():
            old=result['runs'].get(key,{})
            if run.get('started_at','')>=old.get('started_at',''):result['runs'][key]=copy.deepcopy(run)
        for ident,check in value.get('series_checks',{}).items():
            if check.get('observed_at','')>=result['series_checks'].get(ident,{}).get('observed_at',''):
                result['series_checks'][ident]=copy.deepcopy(check)
    for platform in ('webtoon','series'):
        for ident,blocked in list(result['unavailable'][platform].items()):
            if result[platform].get(ident,{}).get('observed_at','')>=blocked['observed_at']:
                del result['unavailable'][platform][ident]
    return result

def load_live_metadata(root=ROOT):
    root=Path(root)
    return merge_metadata(load_metadata(root/'metadata_latest.json'),
                          read_json(root/'detail_seeds.json',{}),
                          read_json(root/'.radar_cache'/'details.json',{}))

def public_json(url):
    request=urllib.request.Request(url,headers={'User-Agent':'WebtoonRadar/0.4','Accept':'application/json'})
    with urllib.request.build_opener(NoRedirect()).open(request,timeout=20) as response:
        raw=response.read(15_000_001)
    if len(raw)>15_000_000:raise ValueError('Public snapshot too large')
    return json.loads(raw)

def sync_public_observations(root=ROOT,force=False,fetcher=public_json):
    """At most one public ref check per five minutes; no auth or secret required."""
    if os.environ.get('WEBTOON_RADAR_OFFLINE')=='1':return {'state':'offline','updated':False}
    root=Path(root);state_path=root/'.radar_cache'/'sync.json'
    with _lock:
        state=read_json(state_path,{})
        if not force and time.time()-state.get('checked_at',0)<300:return dict(state,updated=False)
        state['checked_at']=time.time()
        try:
            ref=fetcher(f'https://api.github.com/repos/{REPOSITORY}/git/ref/heads/main')
            sha=ref['object']['sha']
            if len(sha)!=40 or any(c not in '0123456789abcdef' for c in sha):raise ValueError('Invalid public revision')
            changed=sha!=state.get('sha') or not (root/'metadata_latest.json').exists()
            if changed:
                base=f'https://raw.githubusercontent.com/{REPOSITORY}/{sha}/'
                catalogue=fetcher(base+'catalog_latest.json');validate_snapshot(catalogue)
                metadata=checked_metadata(fetcher(base+'metadata_latest.json'))
                # Keep newer local/on-demand records even when the public commit is older.
                merged=merge_metadata(load_metadata(root/'metadata_latest.json'),metadata)
                existing=read_json(root/'catalog_latest.json',{})
                if catalogue['observed_at']>=existing.get('observed_at',''):
                    write_json(root/'catalog_latest.json',catalogue)
                write_json(root/'metadata_latest.json',merged)
            state.update(state='ok',sha=sha,synced_at=utcnow(),error='')
            write_json(state_path,state)
            return dict(state,updated=changed)
        except Exception as exc:
            state.update(state='unavailable',error=f'{type(exc).__name__}: {str(exc)[:200]}')
            write_json(state_path,state)
            return dict(state,updated=False)

def fill_searched_titles(title_ids,root=ROOT,client_factory=PublicClient):
    """Short, serialized public lookups. A blocked service is never retried here."""
    global _last_lookup
    if os.environ.get('WEBTOON_RADAR_OFFLINE')=='1':return {'fetched':0,'state':'offline'}
    root=Path(root);cache_path=root/'.radar_cache'/'details.json'
    with _lock:
        cache=read_json(cache_path,{'schema_version':1,'webtoon':{},'series':{},'unavailable':{'webtoon':{},'series':{}}})
        merged=load_live_metadata(root)
        ids=[str(i) for i in dict.fromkeys(title_ids) if str(i).isdecimal() and str(i) not in merged['webtoon']]
        ids=[i for i in ids if i not in merged.get('unavailable',{}).get('webtoon',{})][:12]
        if not ids:return {'fetched':0,'state':'ready'}
        if time.time()<cache.get('retry_after',0):return {'fetched':0,'state':'temporarily_unavailable'}
        fetched=0
        try:
            time.sleep(max(0,1.2-(time.monotonic()-_last_lookup)))
            client=client_factory()
            for ident in ids:
                try:row=parse_webtoon(client.get('/api/article/list/info',titleId=ident),ident)
                except PublicRedirect:
                    cache['unavailable']['webtoon'][ident]={'observed_at':utcnow(),'reason':'공개 접근 제한: 다른 경로로 이동하지 않음'}
                    continue
                archive_observation(root,'webtoon',row)
                cache['webtoon'][ident]=row;fetched+=1
                cache['updated_at']=utcnow();write_json(cache_path,cache)
            cache['updated_at']=utcnow();write_json(cache_path,cache)
            return {'fetched':fetched,'state':'ready'}
        except Exception as exc:
            cache['retry_after']=time.time()+3600
            cache['last_error']=f'{type(exc).__name__}: {str(exc)[:200]}'
            write_json(cache_path,cache)
            return {'fetched':fetched,'state':'temporarily_unavailable'}
        finally:_last_lookup=time.monotonic()

def fill_series_links(title_ids,root=ROOT,limit=3,webtoon_factory=PublicClient,series_factory=SeriesClient):
    """Use public contentsNo -> canonical comic resolution, never Series search.

    Every successful observation and outcome is checkpointed. Unresolved links
    are retried after six days; service failures stop with a one-hour cooldown.
    """
    from works import base_title, normalized
    global _last_lookup
    if os.environ.get('WEBTOON_RADAR_OFFLINE')=='1':return {'fetched':0,'checked':0,'state':'offline'}
    root=Path(root);cache_path=root/'.radar_cache'/'details.json'
    with _lock:
        cache=read_json(cache_path,{'schema_version':1,'webtoon':{},'series':{},'unavailable':{'webtoon':{},'series':{}}})
        cache.setdefault('series_checks',{})
        merged=load_live_metadata(root)
        catalogue={r['title_id']:r for r in read_json(root/'catalog_latest.json',{})['titles']}
        now=datetime.now(timezone.utc)
        def due(ident):
            check=merged['series_checks'].get(ident,{})
            if not check:return True
            if check.get('state')=='linked' and check.get('series_id') not in merged['series']:return True
            return (now-datetime.fromisoformat(check['observed_at'])).total_seconds()>=144*3600
        ids=[str(i) for i in dict.fromkeys(title_ids) if str(i) in catalogue and due(str(i))][:limit]
        if not ids:return {'fetched':0,'checked':0,'state':'ready'}
        if time.time()<cache.get('series_retry_after',0):return {'fetched':0,'checked':0,'state':'temporarily_unavailable'}
        fetched=checked=0;webtoon_client=series_client=None
        try:
            time.sleep(max(0,1.2-(time.monotonic()-_last_lookup)))
            for ident in ids:
                detail=merged['webtoon'].get(ident,{})
                check={'observed_at':utcnow(),'state':'unresolved'}
                try:
                    # Migrate old observations that discarded the public content ID.
                    if 'contents_no' not in detail:
                        if webtoon_client is None:webtoon_client=webtoon_factory()
                        detail=parse_webtoon(webtoon_client.get('/api/article/list/info',titleId=ident),ident)
                        archive_observation(root,'webtoon',merged['webtoon'].get(ident,{}))
                        archive_observation(root,'webtoon',detail)
                        cache['webtoon'][ident]=detail
                        write_json(cache_path,cache)
                    contents_no=detail.get('contents_no')
                    check['contents_no']=contents_no
                    if contents_no:
                        if series_client is None:series_client=series_factory()
                        row=series_client.resolve_original(contents_no)
                        if normalized(base_title(row['title']))!=normalized(base_title(catalogue[ident]['title'])):
                            check.update(state='title_mismatch',reason='공통번호 응답의 제목이 달라 자동 연결하지 않음')
                        else:
                            archive_observation(root,'series',merged['series'].get(row['title_id'],{}))
                            archive_observation(root,'series',row)
                            cache['series'][row['title_id']]=row
                            check.update(state='linked',series_id=row['title_id'],resolution_url=row['resolution_url'])
                            fetched+=1
                    else:check.update(state='no_identity',reason='공개 웹툰 정보에 공통 작품번호가 없음')
                except PublicRedirect:
                    check.update(state='public_unavailable',reason='공개 만화 작품 주소 이외로 이동 요청: 진행하지 않음')
                except urllib.error.HTTPError as exc:
                    if exc.code not in (404,410):raise
                    check.update(state='public_unavailable',reason='공개 연결 페이지를 확인할 수 없음')
                checked+=1;cache['series_checks'][ident]=check
                cache['updated_at']=utcnow();write_json(cache_path,cache)
            return {'fetched':fetched,'checked':checked,'state':'ready'}
        except Exception as exc:
            cache['series_retry_after']=time.time()+3600
            cache['series_last_error']=f'{type(exc).__name__}: {str(exc)[:200]}'
            write_json(cache_path,cache)
            return {'fetched':fetched,'checked':checked,'state':'temporarily_unavailable'}
        finally:_last_lookup=time.monotonic()
