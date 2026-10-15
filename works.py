"""Work-level search with conservative title/creator linking and source provenance."""
from __future__ import annotations
import copy
import re
import unicodedata
from collections import defaultdict
from metadata import build_rows, empty_metrics, search_tags

FORMAT_LABELS={'독점','단행본','컬러연재','연재','완결','네이버웹툰'}

def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKC',str(text)).casefold() if c.isalnum())

def base_title(title):
    def keep(match):
        return '' if match.group(1).strip() in FORMAT_LABELS else match.group(0)
    return re.sub(r'\s+',' ',re.sub(r'\[([^\]]+)\]',keep,title)).strip()

def creators(author):
    # A Han-character reading alongside a Korean pen name is an annotation.
    author=re.sub(r'(?<=[가-힣])\s*\([\u3400-\u9fff]+\)','',author or '')
    return {normalized(a) for a in re.split(r'\s*/\s*|\s*,\s*',author) if normalized(a)}

def same_creators(a,b):
    left,right=creators(a.get('author','')),creators(b.get('author',''))
    # An original novelist shared by different adaptations is insufficient.
    return bool(left and right) and (left==right or len(left & right)>=2)

def is_collected_edition(row):
    return '[단행본]' in row['title'] or bool(re.search(r'\[\d+권 세트\]',row['title']))

def build_works(snapshot,metadata):
    source_rows=build_rows(snapshot,metadata)
    webtoons=[r for r in source_rows if r['platform']=='네이버웹툰']
    series=[r for r in source_rows if r['platform']=='네이버시리즈']
    groups={f"webtoon:{r['title_id']}":[r] for r in webtoons}
    index=defaultdict(list)
    for row in webtoons:index[normalized(base_title(row['title']))].append(row)
    unmatched=[]
    for row in series:
        candidates=[w for w in index[normalized(base_title(row['title']))] if same_creators(w,row)]
        if len(candidates)==1:groups[f"webtoon:{candidates[0]['title_id']}"].append(row)
        else:
            row['link_review']='동명·작가 일치 후보가 여러 개여서 별도 표시' if len(candidates)>1 else ''
            unmatched.append(row)
    # Series editions may share one work only when title and all creators agree.
    editions={}
    for row in unmatched:
        key=(normalized(base_title(row['title'])),tuple(sorted(creators(row.get('author','')))))
        if not key[1] or row.get('link_review'):key=(*key,row['title_id'])
        work_id=editions.setdefault(key,f"series:{row['title_id']}")
        groups.setdefault(work_id,[]).append(row)
    works=[]
    for work_id,sources in groups.items():
        sources=sorted(sources,key=lambda r:(r['platform']!='네이버웹툰',is_collected_edition(r),int(r['title_id'])))
        webtoon=next((r for r in sources if r['platform']=='네이버웹툰'),None)
        serial=next((r for r in sources if r['platform']=='네이버시리즈'),None)
        primary=webtoon or serial
        metrics=empty_metrics();provenance={}
        for key,source in [('interest',webtoon),('downloads',serial),('comments',serial)]:
            if source:
                metrics[key]=copy.deepcopy(source['metrics'][key])
                provenance[key]={'platform':source['platform'],'title_id':source['title_id'],
                                 'url':source['source_url'],'observed_at':source.get('observed_at','')}
        venues=list(dict.fromkeys(r['platform'] for r in sources))
        source_states=[]
        for source in sources:
            key='webtoon' if source['platform']=='네이버웹툰' else 'series'
            blocked=metadata.get('unavailable',{}).get(key,{}).get(source['title_id'])
            if blocked and blocked['observed_at']>source.get('observed_at',''):
                source['collection_state']='공개 접근 제한'
            elif source.get('observed_at'):source['collection_state']='확인 완료'
            else:source['collection_state']='상세 수집 중'
            source_states.append(source['collection_state'])
        work={'work_id':work_id,'title':primary['title'] if webtoon else base_title(primary['title']),
              'author':primary.get('author',''),'status':primary.get('status','미표시'),
              'rating':primary.get('rating'),'venues':venues,'sources':sources,
              'aliases':list(dict.fromkeys(r['title'] for r in sources)),
              'genres':sorted({g for r in sources for g in r.get('genres',[])}),
              'official_tags':sorted({t for r in sources for t in r.get('official_tags',[])}),
              'metrics':metrics,'metric_sources':provenance,
              'webtoon_id':webtoon['title_id'] if webtoon else '',
              'webtoon_url':webtoon['source_url'] if webtoon else '',
              'series_url':serial['source_url'] if serial else '',
              'link_basis':'제목 정규화·작가 일치' if len(sources)>1 else '단일 연재처',
              'collection_state':'확인 완료' if all(s=='확인 완료' for s in source_states) else ('공개 접근 제한' if '공개 접근 제한' in source_states else '상세 수집 중')}
        work['tags']=search_tags(work)
        for key,metric in metrics.items():
            work[key]=metric['value']
            if metric['value'] is not None:
                label=('약 ' if metric.get('approximate') else '')+metric['display']
            elif key not in provenance:label='해당 연재처 없음'
            else:
                src=webtoon if key=='interest' else serial
                label='미제공' if src.get('observed_at') else src['collection_state']
            work[key+'_display']=label
        works.append(work)
    return sorted(works,key=lambda r:(normalized(r['title']),r['work_id']))

def filter_works(works,query='',tags=(),mode='모두 포함',minimums=None,venues=()):
    wanted=set(tags);query=query.strip().casefold();selected=set(venues)
    result=[]
    for work in works:
        if selected and not selected & set(work['venues']):continue
        if wanted and not (wanted<=set(work['tags']) if mode=='모두 포함' else bool(wanted & set(work['tags']))):continue
        haystack=' '.join([work['title'],work.get('author',''),*work['aliases'],*work['tags']]).casefold()
        if query and query not in haystack:continue
        if any(work[k] is None or work[k]<v for k,v in (minimums or {}).items() if v>0):continue
        result.append(work)
    return result

def query_title_ids(snapshot,query,limit=12):
    """Only a title/creator query can trigger a bounded public detail lookup."""
    query=query.strip().casefold()
    if len(query)<2:return []
    found=[r for r in snapshot['titles'] if query in (r['title']+' '+r['author']).casefold()]
    found.sort(key=lambda r:(r['title'].casefold()!=query,len(r['title']),int(r['title_id'])))
    return [r['title_id'] for r in found[:limit]]

def export_works_excel(works,path):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb=Workbook();ws=wb.active;ws.title='작품 통합 검색'
    ws.append(['작품 ID','작품명','연재처','작가','장르','관심 수(웹툰)','다운로드 수(시리즈)','댓글 수(시리즈)',
               '관심 원문','다운로드 원문','댓글 원문','검색 태그','확인 상태','웹툰 링크','시리즈 링크','연결 근거'])
    detail=wb.create_sheet('연재처별 원자료')
    detail.append(['통합 작품 ID','연재처','원본 작품 ID','작품명·판본','작가','장르','관심 수','다운로드 수','댓글 수','관측 UTC','출처'])
    for w in works:
        ws.append([w['work_id'],w['title'],' / '.join(w['venues']),w['author'],', '.join(w['genres']),
                   w['interest'],w['downloads'],w['comments'],w['interest_display'],w['downloads_display'],w['comments_display'],
                   ', '.join(w['tags']),w['collection_state'],w['webtoon_url'],w['series_url'],w['link_basis']])
        for s in w['sources']:
            detail.append([w['work_id'],s['platform'],s['title_id'],s['title'],s.get('author',''),', '.join(s.get('genres',[])),
                           *[s['metrics'][k]['value'] for k in ('interest','downloads','comments')],s.get('observed_at',''),s['source_url']])
    note=wb.create_sheet('지표 안내')
    for line in ['동일 제목에 작가가 모두 일치하거나 2명 이상 일치하는 경우에만 통합합니다. 후보가 여러 작품이면 별도 표시합니다.',
                 '관심은 웹툰, 다운로드·댓글은 시리즈 대표 연재판의 값입니다. 서로 다른 플랫폼·판본의 수치는 합산하지 않습니다.',
                 '시리즈에 여러 판본이 있으면 연재판을 우선하며, 모든 판본의 원자료와 출처는 별도 시트에 보존합니다.',
                 '빈 숫자는 해당 연재처 없음·미제공·수집 중·공개 접근 제한이며 0이 아닙니다.',
                 '만·억 등 축약된 원문은 근삿값입니다. 태그·최소값 검색도 표시값 환산 기준입니다.']:
        note.append([line])
    for sheet in wb:
        sheet.freeze_panes='A2';sheet.auto_filter.ref=sheet.dimensions
        for row in sheet:
            for cell in row:
                if isinstance(cell.value,str) and cell.value[:1] in '=+-@':cell.data_type='s'
        for cell in sheet[1]:cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='123C4A')
        for col in sheet.columns:sheet.column_dimensions[col[0].column_letter].width=27
    note.column_dimensions['A'].width=120
    wb.save(path)
