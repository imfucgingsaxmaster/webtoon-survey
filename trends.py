"""Actual source observations only: no invented dates, zeros or interpolation."""
import gzip
import json
from pathlib import Path
from radar import ROOT

def metric_history(work,metadata,snapshot,root=ROOT):
    root=Path(root);points={key:{} for key in ('interest','views','downloads','comments')}
    source_ids={key:(p['platform'],p['title_id']) for key,p in work['metric_sources'].items()}
    def add_details(value):
        for key in ('interest','downloads','comments'):
            selected=source_ids.get(key)
            if not selected:continue
            platform,ident=selected
            row=value.get('webtoon' if platform=='네이버웹툰' else 'series',{}).get(ident,{})
            metric=row.get('metrics',{}).get(key,{})
            if metric.get('value') is None or not row.get('observed_at'):continue
            stamp=row['observed_at']
            points[key][stamp]={'observed_at':stamp,'value':metric['value'],
                'display':metric.get('display',''),'approximate':metric.get('approximate',False),
                'source_url':row.get('source_url',''),'platform':platform,'title_id':ident}
    def add_catalogue(value):
        ident=work.get('webtoon_id')
        if not ident:return
        row=next((r for r in value.get('titles',[]) if r['title_id']==ident),{})
        count=row.get('public_views')
        if type(count) is not int or count<=0:return
        stamp=value['observed_at']
        points['views'][stamp]={'observed_at':stamp,'value':count,'display':f'{count:,}',
            'approximate':False,'source_url':value['source_url'],'platform':'네이버웹툰','title_id':ident}
    for folder,reader in [('metadata_history',add_details),('history',add_catalogue),('.radar_cache/observations',add_details)]:
        for path in sorted((root/folder).glob('*.json.gz')):
            reader(json.loads(gzip.decompress(path.read_bytes())))
    add_details(metadata);add_catalogue(snapshot)
    return {key:[rows[t] for t in sorted(rows)] for key,rows in points.items()}

def archive_observation(root,platform,row):
    if not row.get('observed_at'):return
    folder=Path(root)/'.radar_cache'/'observations';folder.mkdir(parents=True,exist_ok=True)
    stamp=row['observed_at'].replace(':','').replace('+0000','Z')
    path=folder/f"{platform}-{row['title_id']}-{stamp}.json.gz"
    if not path.exists():path.write_bytes(gzip.compress(json.dumps({platform:{row['title_id']:row}},ensure_ascii=False).encode(),mtime=0))
