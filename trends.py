"""Actual source observations only: no invented dates, zeros or interpolation."""
import gzip
import json
from pathlib import Path
from radar import ROOT
from history_index import load_index,build_index

def metric_history(work,metadata,snapshot,root=ROOT,allow_legacy=True):
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
        stamp=row.get('catalog_observed_at') or value['observed_at']
        points['views'][stamp]={'observed_at':stamp,'value':count,'display':f'{count:,}',
            'approximate':False,'source_url':value['source_url'],'platform':'네이버웹툰','title_id':ident}
    index=load_index(root)
    if index is None and allow_legacy:index=build_index(root,persist=False)
    for key in points:
        selected=source_ids.get(key) if key!='views' else ('네이버웹툰',work.get('webtoon_id'))
        if not selected:continue
        platform,ident=selected
        source=('webtoon' if platform=='네이버웹툰' else 'series')+':'+str(ident)
        url=(f'https://comic.naver.com/webtoon/list?titleId={ident}' if platform=='네이버웹툰'
             else f'https://series.naver.com/comic/detail.series?productNo={ident}')
        if key=='views':url=snapshot.get('source_url','https://comic.naver.com/webtoon')
        for stamp,value,display,approximate in (index or {}).get('sources',{}).get(source,{}).get(key,[]):
            points[key][stamp]={'observed_at':stamp,'value':value,'display':display,'approximate':approximate,
                               'source_url':url,'platform':platform,'title_id':ident}
    add_details(metadata);add_catalogue(snapshot)
    # Missing downloads recovered from the source profile keep their own clock.
    profile=work.get('profile',{});download=profile.get('download_check',{})
    if (profile.get('platform')=='네이버시리즈' and profile.get('observed_at') and
        download.get('value') is not None and work.get('metric_sources',{}).get('downloads',{}).get('observed_at')==profile['observed_at']):
        stamp=profile['observed_at']
        points['downloads'][stamp]={'observed_at':stamp,'value':download['value'],'display':download.get('display',''),
            'approximate':download.get('approximate',False),'source_url':profile['source_url'],
            'platform':profile['platform'],'title_id':profile['title_id']}
    return {key:[rows[t] for t in sorted(rows)] for key,rows in points.items()}

def archive_observation(root,platform,row):
    if not row.get('observed_at'):return
    folder=Path(root)/'.radar_cache'/'observations';folder.mkdir(parents=True,exist_ok=True)
    stamp=row['observed_at'].replace(':','').replace('+0000','Z')
    path=folder/f"{platform}-{row['title_id']}-{stamp}.json.gz"
    if not path.exists():path.write_bytes(gzip.compress(json.dumps({platform:{row['title_id']:row}},ensure_ascii=False).encode(),mtime=0))
