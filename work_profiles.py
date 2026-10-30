"""Source descriptions and episode totals, independent of archived metrics."""
from __future__ import annotations
import re
from bs4 import BeautifulSoup
from radar import utcnow


def clean_description(value):
    if not isinstance(value,str):return ''
    value=value.replace('\r\n','\n').replace('\xa0',' ')
    value=re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]','',value)
    return '\n'.join(line.strip() for line in value.splitlines() if line.strip())


def series_profile(html,ident):
    # Reuse the identity check instead of treating arbitrary HTML as a work.
    from metadata import parse_series
    row=parse_series(html,ident)
    soup=BeautifulSoup(html,'html.parser')
    blocks=soup.select('.end_dsc ._synopsis') or soup.select('.end_dsc')
    descriptions=[]
    for block in blocks:
        for control in block.select('a,button,script,style'):control.decompose()
        for br in block.find_all('br'):br.replace_with('\n')
        descriptions.append(clean_description(block.get_text('',strip=False)))
    description=max(descriptions,key=len,default='')
    total=soup.select_one('.end_total_episode')
    label=total.get_text(' ',strip=True) if total else ''
    match=re.search(r'총\s*([\d,]+)\s*(화|권)',label)
    return {'title':row['title'],'platform':'네이버시리즈','title_id':str(ident),
            'description':description,'episode_count':int(match[1].replace(',','')) if match else None,
            'episode_unit':match[2] if match else '', 'episode_label':label,
            'source_url':row['source_url'],'observed_at':row['observed_at'],'state':'confirmed',
            'download_check':dict(row['metrics']['downloads'],state='displayed' if row['metrics']['downloads']['value'] is not None else 'not_displayed')}


def webtoon_profile(info,articles,ident):
    if str(info.get('titleId'))!=str(ident) or str(articles.get('titleId'))!=str(ident):
        raise ValueError('Webtoon profile identity mismatch')
    total=articles.get('totalCount')
    if type(total) is not int or total<0:raise ValueError('Invalid public episode total')
    rows=articles.get('articleList',[])
    latest=next(iter(rows),{})
    return {'title':info['titleName'],'platform':'네이버웹툰','title_id':str(ident),
            'description':clean_description(info.get('synopsis')),'episode_count':total,
            'episode_unit':'개','episode_label':f'공개 목록 총 {total:,}개',
            'latest_episode':str(latest.get('subtitle','')),
            'source_url':f'https://comic.naver.com/webtoon/list?titleId={ident}',
            'observed_at':utcnow(),'state':'confirmed'}


def estimated_revenue(downloads,approximate=False):
    # 320 KRW * 0.9 is exactly 288; never introduce float rounding or infer zero.
    if downloads is None:return {'value':None,'display':'원문 다운로드 수 확인 필요'}
    if type(downloads) is not int or downloads<0:raise ValueError('Invalid downloads')
    value=downloads*288
    return {'value':value,'display':('약 ' if approximate else '')+f'{value:,}원'}


def author_names(work):
    from works import normalized
    result={}
    for source in work.get('sources',[]):
        for name in re.split(r'\s*/\s*|\s*,\s*',source.get('author','')):
            name=re.sub(r'(?<=[가-힣])\s*\([\u3400-\u9fff]+\)','',name).strip()
            key=normalized(name)
            if key:result.setdefault(key,name)
    return list(result.values())


def works_by_author(works,name):
    from works import normalized
    key=normalized(name)
    return [w for w in works if key and key in {normalized(a) for a in author_names(w)}]
