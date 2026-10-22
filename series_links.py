"""Resume public Webtoon-to-Series resolution and publish durable checkpoints."""
import argparse
import gzip
import json
import subprocess
from pathlib import Path
from radar import ROOT,utcnow
from live_data import fill_series_links,load_live_metadata,write_json,series_coverage,series_check_due

def pending_ids(snapshot,details,refresh_since=None):
    ids=[r['title_id'] for r in snapshot['titles'] if series_check_due(details['series_checks'].get(r['title_id']),details,refresh_since=refresh_since)]
    ids.sort(key=lambda i:(bool(details['series_checks'].get(i)),
                          details['series_checks'].get(i,{}).get('observed_at',''),
                          -(details['webtoon'].get(i,{}).get('metrics',{}).get('interest',{}).get('value') or 0)))
    return ids

def save_checkpoint(root,snapshot,started,state,refresh_since=None):
    merged=load_live_metadata(root)
    coverage=series_coverage(snapshot,merged,refresh_since)
    merged['runs']['series_links']=dict(coverage,started_at=started,updated_at=utcnow(),state=state)
    if refresh_since:merged['runs']['series_links']['refresh_since']=refresh_since
    write_json(root/'metadata_latest.json',merged)
    folder=root/'metadata_history';folder.mkdir(exist_ok=True)
    stamp=utcnow().replace(':','').replace('+0000','Z')
    (folder/(stamp+'.json.gz')).write_bytes(gzip.compress(json.dumps(merged,ensure_ascii=False,separators=(',',':')).encode(),mtime=0))
    return coverage

def publish_checkpoint(root):
    def git(*args,**kwargs):return subprocess.run(['git',*args],cwd=root,check=True,**kwargs)
    git('add','metadata_latest.json','metadata_history/')
    changed=subprocess.run(['git','diff','--cached','--quiet'],cwd=root).returncode
    if changed==1:
        git('commit','-m','data: publish Series link checkpoint')
        git('pull','--rebase','origin','main')
        git('push','origin','HEAD:main')
    elif changed:raise RuntimeError('Unable to inspect staged checkpoint')

def collect_links(root=ROOT,limit=200,all_pending=False,publish=False,refresh_since=None):
    root=Path(root)
    snapshot=json.loads((root/'catalog_latest.json').read_text(encoding='utf-8'))
    # Freeze the queue so failed titles becoming due during a long run do not
    # repeatedly displace the remaining unchecked titles.
    ids=pending_ids(snapshot,load_live_metadata(root),refresh_since)
    if not all_pending:ids=ids[:limit]
    started=utcnow();total_checked=total_fetched=0
    print(json.dumps({'state':'running','target':len(ids),'batch_size':limit}),flush=True)
    for offset in range(0,len(ids),max(1,limit)):
        result={'state':'temporarily_unavailable','checked':0,'fetched':0}
        try:
            options={'refresh_since':refresh_since} if refresh_since else {}
            result=fill_series_links(ids[offset:offset+limit],root,limit=limit,**options)
            total_checked+=result['checked'];total_fetched+=result['fetched']
        finally:
            state='paused' if result['state']=='temporarily_unavailable' else ('complete' if offset+limit>=len(ids) else 'running')
            coverage=save_checkpoint(root,snapshot,started,state,refresh_since)
            if publish:publish_checkpoint(root)
            print(json.dumps(dict(result,coverage=coverage),ensure_ascii=False),flush=True)
        if result['state']=='temporarily_unavailable':raise RuntimeError('Public Series linking paused; observations preserved')
    if not ids:save_checkpoint(root,snapshot,started,'complete',refresh_since)
    return {'state':'ready','checked':total_checked,'fetched':total_fetched}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit',type=int,default=200,help='Checkpoint batch size, or one-pass limit without --all')
    parser.add_argument('--all',action='store_true',help='Resolve every due catalogue title in resumable batches')
    parser.add_argument('--publish',action='store_true',help='Commit and push checkpoints from an authenticated Git checkout')
    parser.add_argument('--refresh-since',help='Recheck all links older than this run boundary')
    args=parser.parse_args()
    if args.limit<1:parser.error('Limit must be positive')
    collect_links(limit=args.limit,all_pending=args.all,publish=args.publish,refresh_since=args.refresh_since)
