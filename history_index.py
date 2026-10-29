"""Incremental, deduplicated per-work history; archives are read only by the builder."""
import argparse
import gzip
import hashlib
import json
from functools import lru_cache
from pathlib import Path
from radar import ROOT, utcnow

NAME='history_index.json.gz'

@lru_cache(maxsize=2)
def _read_index(path,version):
    return json.loads(gzip.decompress(Path(path).read_bytes()))

def load_index(root=ROOT):
    path=Path(root)/NAME
    if not path.exists():return None
    stat=path.stat()
    return _read_index(str(path),(stat.st_mtime_ns,stat.st_size))

def build_index(root=ROOT,persist=True):
    root=Path(root);previous=load_index(root) or {'schema_version':1,'sources':{},'indexed_files':{}}
    sources={source:{metric:{p[0]:p for p in points} for metric,points in metrics.items()}
             for source,metrics in previous['sources'].items()}
    ledger=dict(previous['indexed_files']);read_count=0
    def point(source,metric,stamp,value,display,approximate=False):
        if value is None or not stamp:return
        sources.setdefault(source,{}).setdefault(metric,{})[stamp]=[stamp,value,display,bool(approximate)]
    def details(value):
        for platform in ('webtoon','series'):
            for ident,row in value.get(platform,{}).items():
                for metric,data in row.get('metrics',{}).items():
                    point(f'{platform}:{ident}',metric,row.get('observed_at'),data.get('value'),data.get('display',''),data.get('approximate',False))
    def catalogue(value):
        for row in value.get('titles',[]):
            count=row.get('public_views')
            if type(count) is int and count>0:
                point('webtoon:'+row['title_id'],'views',row.get('catalog_observed_at') or value['observed_at'],count,f'{count:,}')
    for folder,reader in [('metadata_history',details),('history',catalogue),('.radar_cache/observations',details)]:
        for path in sorted((root/folder).glob('*.json.gz')):
            key=path.relative_to(root).as_posix();size=path.stat().st_size
            if ledger.get(key)==size:continue
            reader(json.loads(gzip.decompress(path.read_bytes())));ledger[key]=size;read_count+=1
    for name,reader in [('metadata_latest.json',details),('detail_seeds.json',details),('catalog_latest.json',catalogue)]:
        if (root/name).exists():reader(json.loads((root/name).read_bytes()))
    result={'schema_version':1,'built_at':utcnow(),'indexed_files':ledger,
            'sources':{s:{m:[points[k] for k in sorted(points)] for m,points in metrics.items()} for s,metrics in sources.items()}}
    if persist:
        raw=gzip.compress(json.dumps(result,ensure_ascii=False,separators=(',',':')).encode(),mtime=0)
        temp=(root/NAME).with_suffix('.tmp');temp.write_bytes(raw);temp.replace(root/NAME)
        metadata=root/'metadata_latest.json'
        if metadata.exists():
            value=json.loads(metadata.read_bytes())
            value['history_index']={'path':NAME,'sha256':hashlib.sha256(raw).hexdigest(),'built_at':result['built_at']}
            temp=metadata.with_suffix('.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,separators=(',',':')),encoding='utf-8');temp.replace(metadata)
        print(json.dumps({'history_files_read':read_count,'source_count':len(sources),'index_bytes':len(raw)}),flush=True)
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',default=str(ROOT))
    build_index(parser.parse_args().root)
