"""Fill new profile fields once; refresh only ongoing profiles on later runs."""
from __future__ import annotations
import argparse
import copy
import json
import urllib.error
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone,timedelta
from pathlib import Path
from radar import ROOT,PublicClient,utcnow
from metadata import SeriesClient,save_metadata,refresh_boundary
from publisher_enrichment import PacedClient
from works import build_works
from work_profiles import series_profile,webtoon_profile


def profile_targets(snapshot,metadata,refresh=False,refresh_since=None):
    targets=[];cutoff=refresh_boundary(refresh_since) if refresh_since else datetime.now(timezone.utc)-timedelta(days=6)
    for w in build_works(snapshot,metadata):
        if w['status']=='완결':continue
        old=metadata.get('profiles',{}).get(w['work_id'],{})
        checked=metadata.get('profile_checks',{}).get(w['work_id'],{})
        recent=any(r.get('observed_at') and datetime.fromisoformat(r['observed_at'])>=cutoff for r in (old,checked))
        if recent or (old and not refresh and not refresh_since):continue
        serial=next((s for s in w['sources'] if s['platform']=='네이버시리즈'),None)
        targets.append({'work_id':w['work_id'],'title':w['title'],'series_id':serial['title_id'] if serial else '',
                        'webtoon_id':w['webtoon_id'],'interest':w.get('interest') or 0,
                        'status':w['status'],'missing_download':bool(serial and w['downloads'] is None)})
    return sorted(targets,key=lambda t:(not t['missing_download'],t['status']=='완결',-t['interest']))


def fetch_profile(target,series,webtoon):
    attempts=[]
    for platform,client,ident in [('series',series,target.get('series_id')),('webtoon',webtoon,target.get('webtoon_id'))]:
        if not ident:continue
        if client.stopped.is_set():
            attempts.append({'platform':platform,'state':'deferred','reason':'Host collection stopped after access response'})
            continue
        try:
            if platform=='series':
                result=series_profile(client.read('/comic/detail.series',productNo=ident),ident)
            else:
                info=json.loads(client.read('/api/article/list/info',titleId=ident))
                articles=json.loads(client.read('/api/article/list',titleId=ident,page=1,sort='DESC'))
                result=webtoon_profile(info,articles,ident)
            return dict(result,attempts=attempts+[{'platform':platform,'state':'confirmed'}])
        except (urllib.error.URLError,TimeoutError,PermissionError,ValueError) as exc:
            attempts.append({'platform':platform,'state':'unavailable','reason':type(exc).__name__+': '+str(exc)[:140]})
    return {'observed_at':utcnow(),'state':'unavailable','attempts':attempts,
            'reason':'; '.join(a['reason'] for a in attempts)}


def collect_profiles(root=ROOT,limit=None,refresh=False,publish=False,platform='all',refresh_since=None):
    root=Path(root);path=root/'metadata_latest.json'
    cache=json.loads(path.read_bytes());snapshot=json.loads((root/'catalog_latest.json').read_bytes())
    targets=profile_targets(snapshot,cache,refresh,refresh_since)
    if platform!='all':targets=[t for t in targets if bool(t['series_id'])==(platform=='series')]
    if limit is not None:targets=targets[:limit]
    series=PacedClient(SeriesClient());policy=PublicClient();policy.allowed=policy.allowed|{'/api/article/list'};webtoon=PacedClient(policy)
    profiles=cache.get('profiles',{}).copy();checks=cache.get('profile_checks',{}).copy()
    checked=confirmed=unavailable=deferred=0;started=utcnow()
    def checkpoint(state):
        current=json.loads(path.read_bytes())
        current['profiles']=copy.deepcopy(profiles)
        current['profile_checks']=copy.deepcopy(checks)
        current.setdefault('runs',{})['profiles']={'started_at':started,'updated_at':utcnow(),'target':len(targets),'checked':checked,
            'confirmed':confirmed,'unavailable':unavailable,'deferred':deferred,'state':state,'refresh_since':refresh_since}
        save_metadata(current,path)
        print(json.dumps(current['runs']['profiles']),flush=True)
        if publish:
            from data_publish import publish_data
            publish_data(root,'data: update source descriptions and episode totals')
    with ThreadPoolExecutor(max_workers=3) as pool:
        for offset in range(0,len(targets),100):
            pending={pool.submit(fetch_profile,t,series,webtoon):t for t in targets[offset:offset+100]}
            for future in as_completed(pending):
                key=pending[future]['work_id'];result=future.result();old=profiles.get(key,{})
                checks[key]={k:copy.deepcopy(v) for k,v in result.items() if k in ('observed_at','state','reason','attempts')}
                if result.get('state')=='confirmed' or old.get('state')!='confirmed':profiles[key]=result
                if result.get('state')=='confirmed':confirmed+=1
                elif all(a['state']=='deferred' for a in result.get('attempts',[])):deferred+=1
                else:unavailable+=1
                checked+=1
            checkpoint(('partial' if unavailable or deferred else 'complete') if checked==len(targets) else 'running')
    if not targets:checkpoint('complete')
    if unavailable or deferred:
        print(f'::warning::Profiles: {confirmed} updated, {unavailable} unavailable, {deferred} deferred. Previous confirmed observations are preserved.',flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',default=str(ROOT));p.add_argument('--limit',type=int)
    p.add_argument('--refresh',action='store_true');p.add_argument('--publish',action='store_true')
    p.add_argument('--refresh-since',help='Use the weekly observation boundary; resume without repeating checked titles')
    p.add_argument('--platform',choices=['all','series','webtoon'],default='all');a=p.parse_args()
    collect_profiles(a.root,a.limit,a.refresh,a.publish,a.platform,a.refresh_since)
