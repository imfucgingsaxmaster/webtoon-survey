"""Calendar-day growth from actual observations of the same source edition."""
from datetime import date, datetime, timedelta, timezone

KST=timezone(timedelta(hours=9))
METRICS=('downloads','interest')

def local_day(stamp):
    return datetime.fromisoformat(stamp.replace('Z','+00:00')).astimezone(KST).date()

def daily_points(points):
    result={}
    for stamp,value,display,approximate in points:
        if type(value) is not int or value<0:continue
        moment=datetime.fromisoformat(stamp.replace('Z','+00:00'))
        if moment.tzinfo is None:continue
        day=moment.astimezone(KST).date()
        if day not in result or moment>result[day]['moment']:
            result[day]={'moment':moment,'observed_at':stamp,'value':value,
                         'display':display,'approximate':bool(approximate)}
    return result

def compare_days(points,as_of,days):
    start=points.get(as_of-timedelta(days=days));end=points.get(as_of)
    if start is None or end is None:
        return {'state':'missing_current' if end is None else 'missing_baseline','delta':None,
                'start':start,'end':end,'growth':None,'approximate':False}
    delta=end['value']-start['value']
    return {'state':'ready','delta':delta,'start':start,'end':end,
            'growth':delta/start['value']*100 if start['value'] else None,
            'approximate':start['approximate'] or end['approximate']}

def growth_rankings(works,index,as_of,metric='downloads',days=7):
    if metric not in METRICS or days not in (1,7):raise ValueError('Unsupported ranking')
    if isinstance(as_of,str):as_of=date.fromisoformat(as_of)
    rows=[];missing=[]
    for work in works:
        if work.get('status')=='완결':continue
        source=work.get('metric_sources',{}).get(metric,{})
        points=work_points(work,index,metric)
        daily=compare_days(points,as_of,1);weekly=compare_days(points,as_of,7)
        comparison=daily if days==1 else weekly
        row={'work':work,'daily':daily,'weekly':weekly,'comparison':comparison,'source':source}
        (rows if comparison['state']=='ready' else missing).append(row)
    rows.sort(key=lambda r:(-r['comparison']['delta'],r['work']['title'],r['work']['work_id']))
    previous=None;rank=0
    for position,row in enumerate(rows,1):
        value=row['comparison']['delta']
        if value!=previous:rank=position
        row['rank']=rank;previous=value
    return {'as_of':as_of,'start_day':as_of-timedelta(days=days),'metric':metric,'days':days,
            'rows':rows,'missing':missing}

def work_points(work,index,metric):
    source=work.get('metric_sources',{}).get(metric,{})
    platform={'네이버웹툰':'webtoon','네이버시리즈':'series'}.get(source.get('platform'))
    ident=source.get('title_id')
    raw=list((index or {}).get('sources',{}).get(f'{platform}:{ident}',{}).get(metric,[])) if platform and ident else []
    current=work.get('metrics',{}).get(metric,{})
    if platform and ident and source.get('observed_at') and current.get('value') is not None:
        raw.append([source['observed_at'],current['value'],current.get('display',''),current.get('approximate',False)])
    return daily_points(raw)

def available_dates(works,index,latest,metric,days):
    ready=set()
    for work in works:
        if work.get('status')=='완결':continue
        points=work_points(work,index,metric)
        ready.update(day for day in points if day<=latest and day-timedelta(days=days) in points)
    return sorted(ready,reverse=True)
