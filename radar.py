"""Webtoon Radar: authorized CSV imports, immutable snapshots, local analytics.

No platform scraping, private endpoints, cookies, or credentials are used.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import re
import sqlite3
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PLATFORMS = ('네이버웹툰', '네이버시리즈')
DAYS = ('월', '화', '수', '목', '금', '토', '일', '미상')
FIELDS = ('source_id', 'snapshot_id', 'observed_on', 'platform', 'title', 'weekday',
          'genre', 'rank', 'source_position', 'source_page', 'source_ref')

def connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript('''
    CREATE TABLE IF NOT EXISTS snapshots (
      source_id TEXT, snapshot_id TEXT, digest TEXT NOT NULL, imported_at TEXT NOT NULL,
      PRIMARY KEY(source_id, snapshot_id));
    CREATE TABLE IF NOT EXISTS observations (
      source_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, observed_on TEXT,
      platform TEXT NOT NULL, title TEXT NOT NULL, title_key TEXT NOT NULL,
      weekday TEXT NOT NULL, genre TEXT NOT NULL, rank INTEGER,
      source_position INTEGER NOT NULL, source_page INTEGER NOT NULL, source_ref TEXT NOT NULL,
      PRIMARY KEY(source_id, snapshot_id, platform, title_key, weekday));
    ''')
    return con

def title_key(value):
    return re.sub(r'\s+', '', value).casefold()

def validate_rows(rows, registry):
    seen = set()
    validated = []
    for n, raw in enumerate(rows, 2):
        r = {k: (raw.get(k) or '').strip() for k in FIELDS}
        source = registry.get(r['source_id'])
        if not source or source.get('approved') is not True or not source.get('permission_basis'):
            raise ValueError(f'CSV {n}행: 승인되지 않은 데이터 출처')
        if source.get('kind') not in ('user_supplied_research', 'licensed_export'):
            raise ValueError(f'CSV {n}행: 지원하지 않는 수집 방식')
        if not r['title'] or not r['snapshot_id'] or not r['source_ref'] or not r['genre']:
            raise ValueError(f'CSV {n}행: 필수 항목 누락')
        if r['platform'] not in PLATFORMS or r['platform'] not in source['platforms']:
            raise ValueError(f'CSV {n}행: 허용되지 않은 플랫폼')
        if r['weekday'] not in DAYS:
            raise ValueError(f'CSV {n}행: 잘못된 요일')
        if r['observed_on']:
            d = date.fromisoformat(r['observed_on'])
            if d > date.today():
                raise ValueError(f'CSV {n}행: 미래 관측일')
        r['observed_on'] = r['observed_on'] or None
        for field in ('rank', 'source_position', 'source_page'):
            value = r[field]
            if field == 'rank' and not value:
                r[field] = None
            elif not value.isdigit() or int(value) < 1:
                raise ValueError(f'CSV {n}행: {field}는 양의 정수여야 함')
            else:
                r[field] = int(value)
        r['title_key'] = title_key(r['title'])
        key = tuple(r[k] for k in ('source_id', 'snapshot_id', 'platform', 'title_key', 'weekday'))
        if key in seen:
            raise ValueError(f'CSV {n}행: 같은 스냅샷의 중복 작품·요일')
        seen.add(key)
        validated.append(r)
    return validated

def ingest(csv_path, registry_path, db_path):
    registry = json.loads(Path(registry_path).read_text(encoding='utf-8'))
    with Path(csv_path).open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        if set(reader.fieldnames or ()) != set(FIELDS):
            raise ValueError('CSV 열 이름이 입력 양식과 다릅니다.')
        rows = validate_rows(list(reader), registry)
    groups = {}
    for r in rows:
        groups.setdefault((r['source_id'], r['snapshot_id']), []).append(r)
    con = connect(db_path)
    added = 0
    try:
        with con:
            for (source, snapshot), group in groups.items():
                canonical = sorted(group, key=lambda r: (r['platform'], r['title_key'], r['weekday']))
                digest = hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                prior = con.execute('SELECT digest FROM snapshots WHERE source_id=? AND snapshot_id=?', (source, snapshot)).fetchone()
                if prior:
                    if prior['digest'] != digest:
                        raise ValueError('기존 스냅샷 내용이 변경되었습니다. 수정본에는 새 snapshot_id를 사용하세요.')
                    continue
                con.execute('INSERT INTO snapshots VALUES (?,?,?,?)', (source, snapshot, digest, datetime.now(timezone.utc).isoformat()))
                cols = FIELDS + ('title_key',)
                con.executemany(f"INSERT INTO observations ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                                [tuple(r[k] for k in cols) for r in group])
                added += len(group)
    finally:
        con.close()
    return added

def load_rows(db_path):
    con = connect(db_path)
    rows = [dict(x) for x in con.execute('SELECT * FROM observations ORDER BY source_id,snapshot_id,source_page,source_position')]
    con.close()
    return rows

def summarize(rows):
    return {'records': len(rows), 'unique_titles': len({(r['platform'], r['title_key']) for r in rows}),
            'genre_counts': dict(Counter(r['genre'] for r in rows).most_common()),
            'platform_counts': {p: sum(r['platform'] == p for r in rows) for p in PLATFORMS},
            'undated_records': sum(not r['observed_on'] for r in rows),
            'ranked_records': sum(r['rank'] is not None for r in rows)}

def export_excel(rows, output):
    # Python runtime export for GitHub Actions and Streamlit downloads.
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.worksheet.table import Table, TableStyleInfo
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    summary = wb.active
    summary.title = '요약'
    raw = wb.create_sheet('조사목록')
    summary.append(['Webtoon Radar 조사 목록 분석'])
    summary.append(['관측일 미기재 자료는 최신 순위로 해석하지 않음'])
    summary.append(['분석 단위', '작품·요일별 조사 항목'])
    summary.append(['조사 항목 수', len(rows)])
    summary.append(['고유 작품 수', summarize(rows)['unique_titles']])
    summary.append(['네이버시리즈 항목 수', sum(r['platform'] == PLATFORMS[1] for r in rows)])
    summary.append([])
    summary.append(['장르', '조사 항목 수', '비중'])
    headers = ['플랫폼', '작품명', '요일', '장르', '관측일', '확인된 순위', '자료 ID', '원문 페이지', '원문 기재 순서', '출처']
    raw.append(headers)
    for r in rows:
        vals = [r['platform'],r['title'],r['weekday'],r['genre'],r['observed_on'],r['rank'],r['snapshot_id'],r['source_page'],r['source_position'],r['source_ref']]
        raw.append(vals)
        # Untrusted source strings remain strings, including leading '='.
        for c in raw[raw.max_row]:
            if isinstance(c.value, str):
                c.data_type = 's'
    end = max(len(rows) + 1, 2)
    for i, genre in enumerate(summarize(rows)['genre_counts'], 9):
        summary.append([genre, f'=COUNTIF(조사목록!D2:D{end},A{i})', f'=IFERROR(B{i}/$B$4,0)'])
        summary.cell(i, 3).number_format = '0.0%'
    for sh in wb:
        sh.sheet_view.showGridLines = False
        for row in sh:
            for c in row:
                c.font = Font(name='맑은 고딕', size=11)
                c.alignment = Alignment(vertical='center')
        for c in sh[1]:
            c.font = Font(name='맑은 고딕', size=11, color='FFFFFF', bold=True)
            c.fill = PatternFill('solid', fgColor='16354D')
        sh.row_dimensions[1].height = 30
    for i, width in enumerate([20,48,10,16,16,16,28,16,20,80], 1):
        raw.column_dimensions[get_column_letter(i)].width = width
    raw.freeze_panes = 'C2'
    if rows:
        table = Table(displayName='ResearchRows', ref=f'A1:J{len(rows)+1}')
        table.tableStyleInfo = TableStyleInfo(name='TableStyleMedium2', showRowStripes=True)
        raw.add_table(table)
    summary.column_dimensions['A'].width = 57
    summary.column_dimensions['B'].width = 24
    summary.column_dimensions['C'].width = 16
    wb.save(output)

def korean_font():
    import os
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    candidates = [os.environ.get('RADAR_FONT', ''), 'C:/Windows/Fonts/malgun.ttf', '/usr/share/fonts/truetype/nanum/NanumGothic.ttf']
    for p in candidates:
        if p and Path(p).exists():
            pdfmetrics.registerFont(TTFont('RadarKorean', p))
            return 'RadarKorean'
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    pdfmetrics.registerFont(UnicodeCIDFont('HYSMyeongJo-Medium'))
    return 'HYSMyeongJo-Medium'

def export_pdf(rows, output):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib import colors
    font = korean_font()
    s = summarize(rows)
    style = ParagraphStyle('body',fontName=font,fontSize=11,leading=18,wordWrap='CJK')
    story = [Paragraph('Webtoon Radar 조사 요약',ParagraphStyle('title',parent=style,fontSize=21,leading=28)), Spacer(1,18)]
    for text in [f"조사 항목 {s['records']}건 · 고유 작품 {s['unique_titles']}개",
                 '분석 단위는 작품과 요일의 조합입니다. 같은 작품이 다른 요일에 등장하면 각각 집계합니다.',
                 f"관측일 미상 {s['undated_records']}건. 네이버시리즈 {s['platform_counts'][PLATFORMS[1]]}건.",
                 '현재 자료는 제출된 조사 목록입니다. 실시간 인기 순위, 전체 시장 점유율, 매출을 뜻하지 않습니다.']:
        story += [Paragraph(text,style), Spacer(1,10)]
    table = Table([['장르','조사 항목','비중']] + [[g,str(n),f'{n / s["records"]:.1%}'] for g,n in s['genre_counts'].items()],colWidths=[220,100,100])
    table.setStyle(TableStyle([('FONTNAME',(0,0),(-1,-1),font),('FONTSIZE',(0,0),(-1,-1),11),('BOTTOMPADDING',(0,0),(-1,-1),10),('TOPPADDING',(0,0),(-1,-1),10),('BACKGROUND',(0,0),(-1,0),colors.HexColor('#EAF1F5'))]))
    story += [table,Spacer(1,20),Paragraph('출처: 윤승준·김예은 제공 웹툰 트렌드 조사 PDF 4~6쪽. 자료 수록·시스템 검증일 2026년 10월 16일. 원문 관측일은 미기재.',style)]
    SimpleDocTemplate(str(output),pagesize=A4,rightMargin=42,leftMargin=42,topMargin=45,bottomMargin=45).build(story)

def run(csv_path=ROOT/'research.csv', registry_path=ROOT/'sources.json', db_path=ROOT/'outputs/radar.sqlite', out=ROOT/'outputs'):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    added = ingest(csv_path, registry_path, db_path)
    rows = load_rows(db_path)
    summary = summarize(rows)
    summary.update({'new_records': added, 'generated_at': datetime.now(timezone.utc).isoformat(),
                    'data_status': 'user_supplied_research_not_live_rankings'})
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'records.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    export_excel(rows,out/'webtoon_radar.xlsx')
    export_pdf(rows,out/'weekly_summary.pdf')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    return summary

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--input',type=Path,default=ROOT/'research.csv')
    parser.add_argument('--registry',type=Path,default=ROOT/'sources.json')
    parser.add_argument('--db',type=Path,default=ROOT/'outputs/radar.sqlite')
    parser.add_argument('--out',type=Path,default=ROOT/'outputs')
    args = parser.parse_args()
    run(args.input,args.registry,args.db,args.out)
