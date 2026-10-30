"""Fill new profile fields once; refresh only ongoing profiles on later runs."""
from __future__ import annotations
import argparse
import copy
import json
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone,timedelta
from pathlib import Path
from radar import ROOT,PublicClient,utcnow
from metadata import SeriesClient,save_metadata
from publisher_enrichment import PacedClient
from works import build_works
from work_profiles import series_profile,webtoon_profile


def profile_targets(snapshot,metadata,refresh=False):
    targets=[];cutoff=(datetime.now(timezone.utc)-timedelta(days=6)).isoformat()
    for w in build_works(snapshot,metadata):
        old=metadata.get('profiles',{}).get(w['work_id'],{})
        if old and ((w['status']=='완결' and old.get('state')=='confirmed') or not refresh or old.get('observed_at','')>=cutoff):continue
        serial=next((s for s in w['sources'] if s['platform']=='네이버시리즈'),None)
        targets.append({'work_id':w['work_id'],'title':w['title'],'series_id':serial['title_id'] if serial else '',
                        'webtoon_id':w['webtoon_id'],'interest':w.get('interest') or 0,
                        'status':w['status'],'missing_download':bool(serial and w['downloads'] is None)})
    return sorted(targets,key=lambda t:(not t['missing_download'],t['status']=='완결',-t['interest']))


def fetch_profile(target,series,webtoon):
    result={'observed_at':utcnow(),'state':'unavailable'}
    try:
        if target.get('series_id'):
            ident=target['series_id']
            return series_profile(series.read('/comic/detail.series',productNo=ident),ident)
        ident=target['webtoon_id']
        info=json.loads(webtoon.read('/api/article/list/info',titleId=ident))
        articles=json.loads(webtoon.read('/api/article/list',titleId=ident,page=1,sort='DESC'))
        return webtoon_profile(info,articles,ident)
    except Exception as exc:
        result['reason']=type(exc).__name__+': '+str(exc)[:140]
        return result


def collect_profiles(root=ROOT,limit=None,refresh=False,publish=False):
    root=Path(root);path=root/'metadata_latest.json'
    cache=json.loads(path.read_bytes());snapshot=json.loads((root/'catalog_latest.json').read_bytes())
    targets=profile_targets(snapshot,cache,refresh)
    if limit is not None:targets=targets[:limit]
    series=PacedClient(SeriesClient());policy=PublicClient();policy.allowed=policy.allowed|{'/api/article/list'};webtoon=PacedClient(policy)
    profiles=cache.get('profiles',{}).copy();checked=0;started=utcnow()
    def checkpoint(state):
        current=json.loads(path.read_bytes())
        current['profiles']=copy.deepcopy(profiles)
        current.setdefault('runs',{})['profiles']={'started_at':started,'updated_at':utcnow(),'target':len(targets),'checked':checked,'state':state}
        save_metadata(current,path)
        print(json.dumps(current['runs']['profiles']),flush=True)
        if publish:
            import subprocess
            def git(*args):return subprocess.run(['git',*args],cwd=root,check=True)
            git('add','metadata_latest.json')
            if subprocess.run(['git','diff','--cached','--quiet'],cwd=root).returncode==1:
                git('commit','-m','data: update source descriptions and episode totals');git('pull','--rebase','origin','main');git('push','origin','HEAD:main')
    with ThreadPoolExecutor(max_workers=3) as pool:
        for offset in range(0,len(targets),100):
            if series.stopped.is_set() or webtoon.stopped.is_set():break
            pending={pool.submit(fetch_profile,t,series,webtoon):t for t in targets[offset:offset+100]}
            for future in as_completed(pending):
                key=pending[future]['work_id'];result=future.result();old=profiles.get(key,{})
                if result.get('state')=='confirmed' or old.get('state')!='confirmed':profiles[key]=result
                checked+=1
            checkpoint('complete' if checked==len(targets) else 'running')
    if checked<len(targets):checkpoint('paused');raise RuntimeError('Profile collection paused after access response')
    if not targets:checkpoint('complete')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',default=str(ROOT));p.add_argument('--limit',type=int)
    p.add_argument('--refresh',action='store_true');p.add_argument('--publish',action='store_true');a=p.parse_args()
    collect_profiles(a.root,a.limit,a.refresh,a.publish)
