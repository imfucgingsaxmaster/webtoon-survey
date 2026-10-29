"""Portable Notion Markdown and CSV import package from the same dashboard data."""
import csv
import io
import zipfile
from datetime import datetime
from pathlib import Path
from publishers import publisher_rankings,UNKNOWN

DASHBOARD='https://webtoon-survey-muhdfbebtwejruqvugroi6.streamlit.app/'
REPOSITORY='https://github.com/imfucgingsaxmaster/webtoon-survey'

def safe_cell(value):
    if isinstance(value,str) and value.lstrip()[:1] in ('=','+','-','@'):
        return "'"+value
    return value

def csv_bytes(headers,rows):
    buffer=io.StringIO(newline='');writer=csv.writer(buffer);writer.writerow(headers)
    for row in rows:writer.writerow([safe_cell(x) for x in row])
    return buffer.getvalue().encode('utf-8-sig')

def package_files(works,snapshot):
    rankings=publisher_rankings(works)
    known=[g for g in rankings if g['key']]
    headers=['작품명','작품 ID','제작사(시리즈 출판사)','상태','연재요일','연재처','작가','장르','신작',
             '관심 수','다운로드 수','댓글 수','다운로드 원문','다운로드 근삿값',
             '웹툰 관측 UTC','시리즈 관측 UTC','웹툰 링크','시리즈 링크','대시보드 링크','갱신 정책']
    rows=[]
    from urllib.parse import urlencode
    for w in works:
        rows.append([w['title'],w['work_id'],w.get('publisher') or UNKNOWN,w['status'],' / '.join(w.get('serial_days',[])),
            ' / '.join(w['venues']),w['author'],', '.join(w['genres']),'신작' if w['is_new'] else '',
            w['interest'],w['downloads'],w['comments'],w['downloads_display'],bool(w['metrics']['downloads'].get('approximate')),
            w['metric_sources'].get('interest',{}).get('observed_at',''),w['metric_sources'].get('downloads',{}).get('observed_at',''),
            w['webtoon_url'],w['series_url'],DASHBOARD+'?'+urlencode({'work':w['work_id']}),
            '완결 보관 · 갱신 제외' if w['status']=='완결' else '매주 월요일 갱신'])
    rank_headers=['제작사(시리즈 출판사)','작품 수','연재·휴재','완결','다운로드 합계','다운로드 확인 작품 수','근삿값 포함']
    rank_rows=[[g['publisher'],g['works'],g['ongoing'],g['finished'],g['downloads'],g['downloads_known'],g['approximate']] for g in rankings]
    finished=sum(w['status']=='완결' for w in works)
    observed=snapshot['observed_at']
    table='\n'.join(f"| {i} | {g['publisher'].replace('|','/')} | {g['works']:,} | {g['downloads_display']} | {g['downloads_known']:,} |" for i,g in enumerate(known[:20],1))
    text=f'''# Webtoon Radar

[공개 대시보드]({DASHBOARD}) · [GitHub 프로젝트]({REPOSITORY})

## 자료 현황

- 목록 관측 UTC: {observed}
- 통합 작품 {len(works):,}개, 연재·휴재 {len(works)-finished:,}개, 완결 보관 {finished:,}개
- 제작사 확인 {sum(g['works'] for g in known):,}개 / {len(works):,}개, {len(known):,}개 제작사
- 연재·휴재는 매주 월요일 09:17 한국시간에 갱신합니다. 완결 작품의 수치는 마지막 관측을 보존하며, 누락 출판사만 별도 보완합니다.

## 제작사별 다운로드 순위

네이버시리즈 작품 페이지의 ‘출판사’를 제작사 분류로 사용합니다. 회사 전체 실적이 아니라 현재 확보한 작품의 합계입니다.

| 순위 | 제작사 | 작품 수 | 다운로드 합계 | 다운로드 확인 작품 |
| --- | --- | ---: | ---: | ---: |
{table}

같은 작품의 웹툰·시리즈와 여러 판본은 중복 합산하지 않습니다. 시리즈 대표 연재판의 다운로드를 한 번만 더합니다. ‘약’은 만·억 단위 축약값이 포함되었다는 뜻입니다. 값이 없는 작품은 합계에서 제외하고 확인 작품 수를 함께 표시합니다. 완결 보관 작품은 과거 마지막 값입니다. 제작사 미확인은 별도 그룹으로 보존하며 순위에 넣지 않습니다.

## 작품 검색과 자료 활용

대시보드에서 표지를 선택하면 관심 수·다운로드·댓글과 실제 관측 변화 그래프, 연재요일, 공식 작품 링크를 볼 수 있습니다. ‘제작사별’에서 작품 수 또는 다운로드 합계를 기준으로 정렬하고 작품 목록을 열 수 있습니다.

## 노션으로 가져오기

이 ZIP을 풀어 `Webtoon Radar.md`를 노션에 Markdown으로 가져옵니다. `작품.csv`와 `제작사별_순위.csv`는 각각 CSV로 가져오면 정렬·필터가 가능한 데이터베이스가 됩니다. 가져온 페이지에서 공유 범위를 선택해 링크를 공유하세요. [노션 공식 가져오기 안내](https://www.notion.com/help/import-data-into-notion)

이 패키지는 내보낸 시점의 자료입니다. 노션 사본은 자동으로 바뀌지 않습니다. 최신 자료는 공개 대시보드에서 다시 내려받을 수 있습니다. 숫자 빈칸은 0을 뜻하지 않으며 관측 시각과 출처는 작품 CSV에 포함되어 있습니다.
'''
    return {'Webtoon Radar.md':text.encode('utf-8'),'작품.csv':csv_bytes(headers,rows),'제작사별_순위.csv':csv_bytes(rank_headers,rank_rows)}

def export_notion(works,snapshot):
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,raw in package_files(works,snapshot).items():archive.writestr(name,raw)
    return buffer.getvalue()

if __name__=='__main__':
    from dashboard_data import dataset
    from radar import ROOT
    snapshot,metadata,works=dataset()
    target=ROOT/'outputs'/'webtoon_radar_notion.zip';target.parent.mkdir(exist_ok=True)
    target.write_bytes(export_notion(works,snapshot));print(target)
