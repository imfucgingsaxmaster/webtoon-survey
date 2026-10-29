"""Rank official Series publishers, counting a unified work exactly once."""
import unicodedata

UNKNOWN='제작사 미확인'

def publisher_key(value):
    return ' '.join(unicodedata.normalize('NFKC',value or '').split()).casefold()

def publisher_rankings(works,sort='downloads'):
    groups={};seen=set()
    for work in works:
        if work['work_id'] in seen:continue
        seen.add(work['work_id'])
        name=' '.join((work.get('publisher') or '').split())
        key=publisher_key(name)
        group=groups.setdefault(key,{'key':key,'publisher':name or UNKNOWN,'works':0,'ongoing':0,
            'finished':0,'downloads':0,'downloads_known':0,'approximate':False,'work_ids':[]})
        group['works']+=1;group['finished' if work.get('status')=='완결' else 'ongoing']+=1
        group['work_ids'].append(work['work_id'])
        if work.get('downloads') is not None:
            group['downloads']+=work['downloads'];group['downloads_known']+=1
            group['approximate']|=bool(work.get('metrics',{}).get('downloads',{}).get('approximate'))
    for group in groups.values():
        if not group['downloads_known']:group['downloads']=None
        group['downloads_display']=('약 ' if group['approximate'] else '')+f"{group['downloads']:,}" if group['downloads'] is not None else '관측값 없음'
    # Unknown publishers remain available, but never masquerade as a company.
    return sorted(groups.values(),key=lambda g:(not g['key'],
        -(g['works'] if sort=='works' else (g['downloads'] or 0)),-g['works'],g['publisher']))
