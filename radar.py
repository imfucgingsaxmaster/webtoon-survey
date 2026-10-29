"""Collect the entire public Naver Webtoon catalogue; never ingest report PDFs.

Only allowlisted public list metadata is requested. No accounts, cookies,
episode bodies, images, comments, private data, or access-control bypasses.
"""
from __future__ import annotations
import argparse
import copy
import gzip
import hashlib
import json
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = 'https://comic.naver.com'
UA = 'WebtoonRadar/0.3 (+https://github.com/imfucgingsaxmaster/webtoon-survey)'
WEEKDAYS = {'MONDAY':'월','TUESDAY':'화','WEDNESDAY':'수','THURSDAY':'목',
            'FRIDAY':'금','SATURDAY':'토','SUNDAY':'일'}
ALLOWED = {'/api/webtoon/titlelist/weekday', '/api/webtoon/titlelist/finished', '/api/article/list/info'}


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def public_cover_url(value):
    """Keep only official HTTPS image references; never download cover binaries."""
    if not isinstance(value,str):return ''
    parsed=urllib.parse.urlsplit(value)
    if parsed.scheme=='https' and parsed.hostname and parsed.hostname.endswith('.pstatic.net') and not parsed.username and not parsed.password:
        return value
    return ''


class PublicRedirect(PermissionError):
    """A title left the allowlisted public endpoint, often to require login."""
    def __init__(self, message, target=''):
        super().__init__(message)
        self.target = target


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        raise PublicRedirect('Redirect blocked; public title metadata unavailable', newurl)


class PublicClient:
    base = BASE
    allowed = ALLOWED
    landing = '/webtoon'

    def __init__(self, delay=1.2):
        self.delay = max(1.0, delay)
        self.last_request = 0.0
        self.robots = urllib.robotparser.RobotFileParser()
        raw = self._read(self.base + '/robots.txt').decode('utf-8')
        self.robots.parse(raw.splitlines())
        self.robots_hash = hashlib.sha256(raw.encode()).hexdigest()
        if not self.robots.can_fetch(UA, self.base + self.landing):
            raise PermissionError('Public catalogue is disallowed by robots.txt')

    def _read(self, url):
        time.sleep(max(0, self.delay - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json,text/plain'})
        # No automatic retry on authentication, rate limits or other access errors.
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=40) as response:
            if urllib.parse.urlsplit(response.url).netloc != urllib.parse.urlsplit(self.base).netloc:
                raise PermissionError('Unexpected redirect; collection stopped')
            return response.read(12_000_001)

    def get(self, path, **params):
        if path not in self.allowed:
            raise PermissionError('Endpoint is not on the metadata allowlist')
        url = self.base + path + '?' + urllib.parse.urlencode(params)
        if not self.robots.can_fetch(UA, url):
            raise PermissionError('Endpoint is disallowed by robots.txt')
        raw = self._read(url)
        if len(raw) > 12_000_000:
            raise ValueError('Unexpectedly large response')
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError('Unexpected response schema')
        return result


def normalize(item):
    ident = item.get('titleId')
    title = item.get('titleName')
    if not isinstance(ident, int) or ident < 1 or not isinstance(title, str) or not title.strip():
        raise ValueError('Missing stable title ID or title name')
    score = item.get('starScore')
    raw_views=item.get('viewCount')
    # Most official rows use zero without displaying a count. Do not call this
    # a measured zero or infer lifetime reach from an undocumented scope.
    public_views=raw_views if type(raw_views) is int and raw_views>0 else None
    if score is not None and (isinstance(score, bool) or not isinstance(score, (int,float)) or not 0 <= score <= 10):
        raise ValueError('Invalid public rating')
    return {'platform':'네이버웹툰', 'title_id':str(ident), 'title':title.strip(),
            'cover_url':public_cover_url(item.get('thumbnailUrl')),
            'public_views':public_views,
            'is_new':item.get('new') is True,'new_basis':'네이버웹툰 공식 신작 표시' if item.get('new') is True else '',
            'author':str(item.get('author') or ''), 'rating':score,
            'status':'완결' if item.get('finish') else ('휴재' if item.get('rest') else '연재'),
            'url':f'{BASE}/webtoon/list?titleId={ident}'}


def collect(client):
    started = utcnow()
    titles, memberships, coverage = {}, [], []

    def add(items, group, start=0):
        if not isinstance(items, list) or not items:
            raise ValueError(f'Empty or invalid catalogue group: {group}')
        seen = set()
        for index, item in enumerate(items, start+1):
            record = normalize(item)
            key = record['title_id']
            if key in seen:
                raise ValueError(f'Duplicate ID inside {group}: {key}')
            seen.add(key)
            # Preserve finished/rest flags across overlapping catalogue groups.
            previous = titles.get(key)
            if previous and previous['status'] == '완결':
                record['status'] = '완결'
            titles[key] = record
            memberships.append({'title_id':key,'group':group,'position':index})

    weekday = client.get('/api/webtoon/titlelist/weekday', order='user').get('titleListMap')
    if not isinstance(weekday, dict) or not set(WEEKDAYS).issubset(weekday):
        raise ValueError('One or more weekday lists are missing')
    for key, label in WEEKDAYS.items():
        add(weekday[key], label)
        coverage.append({'group':label,'items':len(weekday[key]),'expected':len(weekday[key]),'pages':1})
    daily = client.get('/api/webtoon/titlelist/weekday', week='dailyPlus', order='user').get('titleList')
    add(daily, '매일+')
    coverage.append({'group':'매일+','items':len(daily),'expected':len(daily),'pages':1})

    page, expected, pages, finished_ids, finished_count = 1, None, None, set(), 0
    while True:
        response = client.get('/api/webtoon/titlelist/finished', order='USER', page=page)
        info, items = response.get('pageInfo', {}), response.get('titleList')
        if page == 1:
            expected, pages = info.get('totalRows'), info.get('totalPages')
            if not isinstance(expected,int) or not isinstance(pages,int) or not 1 <= pages <= 1000:
                raise ValueError('Missing pagination totals')
        if info.get('totalRows') != expected or info.get('totalPages') != pages or info.get('page') != page:
            raise ValueError('Catalogue changed during pagination; retry a later run')
        if not isinstance(items,list) or not items:
            raise ValueError('Missing finished catalogue page')
        ids = {str(x.get('titleId')) for x in items}
        if finished_ids & ids:
            raise ValueError('Duplicate finished title across pages; snapshot rejected')
        finished_ids.update(ids)
        add(items, '완결목록', finished_count)
        finished_count += len(items)
        if page % 10 == 0 or page == pages:
            print(f'Finished catalogue {page}/{pages} pages, {finished_count}/{expected} rows', flush=True)
        if page == pages:
            break
        if info.get('nextPage') != page + 1:
            raise ValueError('Broken pagination sequence')
        page += 1
    if finished_count != expected:
        raise ValueError('Incomplete catalogue; no snapshot will be published')
    coverage.append({'group':'완결목록','items':finished_count,'expected':expected,'pages':pages})
    ended = utcnow()
    return {'schema_version':2,'snapshot_id':ended.replace(':','').replace('+0000','Z'),
            'started_at':started,'observed_at':ended,'scope':'네이버웹툰 정식 공개 카탈로그: 요일별·매일+·완결 목록',
            'source_url':BASE+'/webtoon','robots_sha256':client.robots_hash,
            'collection_status':'complete',
            'series_status':'대기: 전체 분류 목록 robots.txt 제한. 허가된 피드 확보 후 연결.',
            'exclusions':['베스트도전·도전','판매 종료·비공개·앱 전용 등 공개 목록에 없는 작품',
                          '회차 본문·이미지·댓글·개인정보·비공개 지표'],
            'coverage':coverage,'titles':sorted(titles.values(),key=lambda x:int(x['title_id'])),
            'memberships':memberships}


def validate_snapshot(snapshot):
    if snapshot.get('schema_version') != 2 or snapshot.get('collection_status') != 'complete':
        raise ValueError('Expected a complete live catalogue snapshot, not a research document')
    datetime.fromisoformat(snapshot['observed_at'])
    ids = [x['title_id'] for x in snapshot['titles']]
    if not ids or len(set(ids)) != len(ids):
        raise ValueError('Empty or duplicate title IDs')
    idset, keys = set(ids), set()
    for item in snapshot['memberships']:
        key = (item['title_id'], item['group'])
        if key in keys or item['title_id'] not in idset or item['position'] < 1:
            raise ValueError('Invalid or duplicate catalogue membership')
        keys.add(key)
    for group in snapshot['coverage']:
        actual = sum(x['group']==group['group'] for x in snapshot['memberships'])
        if group['expected'] != group['items'] or actual != group['items']:
            raise ValueError('Coverage mismatch')


def collect_active_catalogue(client, previous):
    """Read only active lists; preserve completed rows and their observation dates."""
    validate_snapshot(previous)
    started=utcnow();titles={r['title_id']:copy.deepcopy(r) for r in previous['titles']}
    frozen={i for i,r in titles.items() if r['status']=='완결'}
    for row in titles.values():row.setdefault('catalog_observed_at',previous['observed_at'])
    observed=set();memberships=[]
    weekday=client.get('/api/webtoon/titlelist/weekday',order='user').get('titleListMap')
    if not isinstance(weekday,dict) or not set(WEEKDAYS)<=set(weekday):raise ValueError('Missing weekday list')
    groups=[(label,weekday[key]) for key,label in WEEKDAYS.items()]
    groups.append(('매일+',client.get('/api/webtoon/titlelist/weekday',week='dailyPlus',order='user').get('titleList')))
    for group,items in groups:
        if not isinstance(items,list) or not items:raise ValueError('Missing active list')
        seen=set()
        for position,item in enumerate(items,1):
            row=normalize(item);ident=row['title_id']
            if ident in seen:raise ValueError('Duplicate active title')
            seen.add(ident)
            if ident in frozen:continue
            row['catalog_observed_at']=utcnow();titles[ident]=row;observed.add(ident)
            memberships.append({'title_id':ident,'group':group,'position':position})
    memberships.extend(copy.deepcopy(m) for m in previous['memberships'] if m['title_id'] not in observed)
    counts=Counter(m['group'] for m in memberships)
    ended=utcnow();result=copy.deepcopy(previous)
    result.update(snapshot_id=ended.replace(':','').replace('+0000','Z'),started_at=started,observed_at=ended,
                  scope='연재·휴재 목록 갱신 + 완결 작품 보관',robots_sha256=client.robots_hash,
                  frozen_completed=len(frozen),titles=sorted(titles.values(),key=lambda r:int(r['title_id'])),
                  memberships=memberships,coverage=[{'group':g,'items':n,'expected':n,'pages':0 if g=='완결목록' else 1,
                  'mode':'보관된 완결 목록' if g=='완결목록' else '현재 목록과 보관 항목'} for g,n in counts.items()])
    validate_snapshot(result)
    return result


@contextmanager
def connect(path):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript('''
    CREATE TABLE IF NOT EXISTS catalogue_snapshots(
      snapshot_id TEXT PRIMARY KEY, observed_at TEXT NOT NULL, digest TEXT NOT NULL, payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS catalogue_titles(
      snapshot_id TEXT, title_id TEXT, platform TEXT, title TEXT, author TEXT, rating REAL, status TEXT, url TEXT,
      PRIMARY KEY(snapshot_id,title_id));
    CREATE TABLE IF NOT EXISTS catalogue_memberships(
      snapshot_id TEXT, title_id TEXT, group_name TEXT, position INTEGER,
      PRIMARY KEY(snapshot_id,title_id,group_name));
    ''')
    try:
        with con:
            yield con
    finally:
        con.close()


def ingest(snapshot, db):
    validate_snapshot(snapshot)
    payload = json.dumps(snapshot,ensure_ascii=False,sort_keys=True)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    with connect(db) as con:
        old = con.execute('SELECT digest FROM catalogue_snapshots WHERE snapshot_id=?',(snapshot['snapshot_id'],)).fetchone()
        if old:
            if old['digest'] != digest:
                raise ValueError('Snapshot ID already exists with different content')
            return False
        con.execute('INSERT INTO catalogue_snapshots VALUES(?,?,?,?)',
                    (snapshot['snapshot_id'],snapshot['observed_at'],digest,payload))
        con.executemany('INSERT INTO catalogue_titles VALUES(?,?,?,?,?,?,?,?)',
            [(snapshot['snapshot_id'],*[t[k] for k in ('title_id','platform','title','author','rating','status','url')]) for t in snapshot['titles']])
        con.executemany('INSERT INTO catalogue_memberships VALUES(?,?,?,?)',
            [(snapshot['snapshot_id'],m['title_id'],m['group'],m['position']) for m in snapshot['memberships']])
    return True


def flattened(snapshot):
    groups = {}
    for m in snapshot['memberships']:
        groups.setdefault(m['title_id'],[]).append(m['group'])
    return [dict(t,groups=', '.join(groups.get(t['title_id'],[]))) for t in snapshot['titles']]


def compare(current, previous):
    if previous is None:
        return {'baseline':True,'added':[],'removed':[],'position_changes':[]}
    current_ids = {x['title_id'] for x in current['titles']}
    old_ids = {x['title_id'] for x in previous['titles']}
    old_positions = {(x['title_id'],x['group']):x['position'] for x in previous['memberships']}
    changes = [dict(x,change=old_positions[(x['title_id'],x['group'])]-x['position'])
               for x in current['memberships'] if (x['title_id'],x['group']) in old_positions]
    return {'baseline':False,'previous_observed_at':previous['observed_at'],
            'added':sorted(current_ids-old_ids),'removed':sorted(old_ids-current_ids),
            'position_changes':changes}


def export_excel(snapshot, output, changes, metadata=None):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.worksheet.table import Table, TableStyleInfo
    wb = Workbook()
    summary = wb.active
    summary.title = '수집 요약'
    for row in [('Webtoon Radar','공개 카탈로그 전수 조사'),('관측 시각 UTC',snapshot['observed_at']),
                ('고유 작품 수',len(snapshot['titles'])),('목록 항목 수',len(snapshot['memberships'])),
                ('범위',snapshot['scope']),('네이버시리즈','공개 홈·일간 TOP100·이전 발견 작품 (상세 지표 Excel 참고)' if metadata else snapshot['series_status']),
                ('비교', '첫 관측: 증감 산출 대기' if changes['baseline'] else '이전 저장 관측과 비교'),
                ('자료 원칙','첨부 조사 PDF는 데이터셋으로 사용하지 않음'),
                ('지표 주의','목록 순서는 해당 그룹의 인기순 정렬 위치. 전체 통합 순위·조회수·매출 아님')]:
        summary.append(row)
    ws = wb.create_sheet('전체 작품')
    keys = ('title_id','title','author','status','rating','groups','url')
    ws.append(('작품 ID','작품명','작가','상태','공개 별점','목록','출처 URL'))
    for record in flattened(snapshot):
        ws.append([record[k] for k in keys])
    tab = Table(displayName='Catalogue',ref=ws.dimensions)
    tab.tableStyleInfo = TableStyleInfo(name='TableStyleMedium2',showRowStripes=True)
    ws.add_table(tab)
    positions = wb.create_sheet('목록별 위치')
    positions.append(('작품 ID','목록','인기순 위치'))
    for item in snapshot['memberships']:
        positions.append((item['title_id'],item['group'],item['position']))
    coverage = wb.create_sheet('범위 검증')
    coverage.append(('목록','수집 항목','응답 항목','페이지','검증'))
    for i,c in enumerate(snapshot['coverage'],2):
        coverage.append((c['group'],c['items'],c['expected'],c['pages'],f'=IF(B{i}=C{i},"일치","확인")'))
    for sheet in wb:
        sheet.freeze_panes = 'A2'
        sheet.sheet_view.showGridLines = False
        for row in sheet:
            for cell in row:
                cell.font = Font(name='맑은 고딕',size=10)
                if isinstance(cell.value,str) and cell.value[:1] in ('=','+','-','@') and not (sheet.title=='범위 검증' and cell.column==5):
                    cell.data_type = 's'  # protect spreadsheet formula injection in titles/authors
        for cell in sheet[1]:
            cell.fill = PatternFill('solid',fgColor='123C4A')
            cell.font = Font(name='맑은 고딕',bold=True,color='FFFFFF')
        for col in sheet.columns:
            sheet.column_dimensions[col[0].column_letter].width = min(65,max(14,max(len(str(c.value or '')) for c in col[:80])+3))
        sheet.row_dimensions[1].height = 26
    summary.column_dimensions['B'].width=95
    for row in summary:
        row[1].alignment=Alignment(wrap_text=True,vertical='center')
        summary.row_dimensions[row[0].row].height=32
    wb.save(output)


def korean_font():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    for path in ('C:/Windows/Fonts/malgun.ttf','/usr/share/fonts/truetype/nanum/NanumGothic.ttf'):
        if Path(path).exists():
            pdfmetrics.registerFont(TTFont('Korean',path)); return 'Korean'
    pdfmetrics.registerFont(UnicodeCIDFont('HYSMyeongJo-Medium')); return 'HYSMyeongJo-Medium'


def export_pdf(snapshot, path, changes, metadata=None):
    from reportlab.pdfgen import canvas
    from reportlab.lib.colors import HexColor
    font=korean_font(); c=canvas.Canvas(str(path),pagesize=(595.28,841.89))
    c.setTitle('Webtoon Radar 공개 카탈로그 요약')
    c.setFillColor(HexColor('#123C4A')); c.rect(0,690,596,152,fill=1,stroke=0)
    c.setFillColor(HexColor('#FFFFFF')); c.setFont(font,26);c.drawString(42,785,'Webtoon Radar')
    c.setFont(font,13);c.drawString(42,752,'네이버웹툰 공개 카탈로그 전체 조사')
    c.setFont(font,10);c.drawString(42,720,snapshot['observed_at']+' | UTC')
    c.setFillColor(HexColor('#172B32'));c.setFont(font,22)
    c.drawString(42,646,f"고유 작품 {len(snapshot['titles']):,}개")
    c.setFont(font,11);c.drawString(42,616,f"목록 항목 {len(snapshot['memberships']):,}개 · 작품 ID 기준 중복 제거")
    y=574
    for group in snapshot['coverage']:
        c.drawString(42,y,f"{group['group']}  {group['items']:,}개  |  {group['pages']}페이지 확인"); y-=25
    y-=10
    for line in ['수집 범위: 요일별·매일+·완결의 공개된 정식 작품 목록.',
                 '첨부 조사 PDF는 보고서의 참고자료이며 프로젝트 데이터가 아닙니다.',
                 '인기순 위치는 목록 내부 순서이며 조회수·매출·통합 순위가 아닙니다.',
                 '첫 관측입니다. 추세는 이후 관측이 누적되면 비교합니다.' if changes['baseline'] else '이전 관측과 작품 진입·이탈 및 목록 위치 변화를 비교합니다.',
                 (f"상세 지표: 웹툰 {len(metadata.get('webtoon',{})):,}개 · 시리즈 {len(metadata.get('series',{})):,}개 (관측별 시각 별도)." if metadata else '네이버시리즈: 상세 공개 지표 수집 전입니다.'),
                 '관심 수는 네이버웹툰, 다운로드·댓글 수는 시리즈 공개 표시값입니다.',
                 '베스트도전·도전, 비공개·판매 종료·앱 전용 작품은 포함하지 않습니다.']:
        c.setFont(font,9);c.drawString(42,y,line);y-=24
    c.setFont(font,9);c.drawString(42,45,'출처: comic.naver.com / series.naver.com | Webtoon Radar v0.3')
    c.save()


def run(offline=False, root=ROOT):
    root=Path(root); output=root/'outputs'; output.mkdir(exist_ok=True)
    latest=root/'catalog_latest.json'; history=root/'history'; history.mkdir(exist_ok=True)
    if offline:
        snapshot=json.loads(latest.read_text(encoding='utf-8'))
    else:
        snapshot=collect_active_catalogue(PublicClient(),json.loads(latest.read_text(encoding='utf-8'))) if latest.exists() else collect(PublicClient())
    validate_snapshot(snapshot)
    previous=None
    db=output/'catalogue.sqlite'
    for file in sorted(history.glob('*.json.gz')):
        old=json.loads(gzip.decompress(file.read_bytes()).decode())
        ingest(old,db)
        if old['observed_at']<snapshot['observed_at'] and (previous is None or old['observed_at']>previous['observed_at']): previous=old
    ingest(snapshot,db)
    changes=compare(snapshot,previous)
    encoded=json.dumps(snapshot,ensure_ascii=False,separators=(',',':')).encode()
    if not offline:
        temp=latest.with_suffix('.tmp');temp.write_bytes(encoded);temp.replace(latest)
        (history/(snapshot['snapshot_id']+'.json.gz')).write_bytes(gzip.compress(encoded,mtime=0))
    summary={'observed_at':snapshot['observed_at'],'unique_titles':len(snapshot['titles']),
             'list_entries':len(snapshot['memberships']),'status_counts':dict(Counter(t['status'] for t in snapshot['titles'])),
             'coverage':snapshot['coverage'],'series_status':snapshot['series_status'],
             'baseline':changes['baseline'],'added':len(changes['added']),'removed':len(changes['removed'])}
    from metadata import export_metadata
    from live_data import load_live_metadata
    from works import build_works, export_works_excel
    metadata=load_live_metadata(root)
    summary['detail_coverage']={key:len(metadata.get(key,{})) for key in ('webtoon','series')}
    summary['detail_runs']=metadata.get('runs',{})
    summary['series_status']='공개 홈·일간 TOP100·이전 발견 작품. 전체 카탈로그 아님.'
    for file in sorted((root/'metadata_history').glob('*.json.gz')):
        export_metadata(json.loads(gzip.decompress(file.read_bytes()).decode()),db)
    export_metadata(metadata,db)
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'changes.json').write_text(json.dumps(changes,ensure_ascii=False,indent=2),encoding='utf-8')
    export_excel(snapshot,output/'webtoon_radar.xlsx',changes,metadata)
    export_works_excel(build_works(snapshot,metadata),output/'webtoon_radar_search.xlsx')
    export_pdf(snapshot,output/'summary.pdf',changes,metadata)
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
    return snapshot


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline',action='store_true',help='Analyze an existing live catalogue snapshot without network access')
    args=parser.parse_args()
    run(offline=args.offline)
