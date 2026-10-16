"""Resume bounded public Webtoon-to-Series resolution for weekly collection."""
import argparse
import gzip
import json
from pathlib import Path
from radar import ROOT,utcnow
from live_data import fill_series_links,load_live_metadata,write_json

def collect_links(root=ROOT,limit=200):
    root=Path(root)
    snapshot=json.loads((root/'catalog_latest.json').read_text(encoding='utf-8'))
    details=load_live_metadata(root)
    # Unchecked first; prioritize public interest, then oldest link checks.
    ids=[r['title_id'] for r in snapshot['titles']]
    ids.sort(key=lambda i:(bool(details['series_checks'].get(i)),
                          details['series_checks'].get(i,{}).get('observed_at',''),
                          -(details['webtoon'].get(i,{}).get('metrics',{}).get('interest',{}).get('value') or 0)))
    try:
        result=fill_series_links(ids,root,limit=limit)
    finally:
        # Preserve checkpoints even if a later request is blocked.
        merged=load_live_metadata(root)
        write_json(root/'metadata_latest.json',merged)
        folder=root/'metadata_history';folder.mkdir(exist_ok=True)
        stamp=utcnow().replace(':','').replace('+0000','Z')
        (folder/(stamp+'.json.gz')).write_bytes(gzip.compress(json.dumps(merged,ensure_ascii=False,separators=(',',':')).encode(),mtime=0))
    print(json.dumps(result,ensure_ascii=False),flush=True)
    if result['state']=='temporarily_unavailable':raise RuntimeError('Public Series linking paused; observations preserved')
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit',type=int,default=200)
    args=parser.parse_args()
    if args.limit<0:parser.error('Limit must be nonnegative')
    collect_links(limit=args.limit)
