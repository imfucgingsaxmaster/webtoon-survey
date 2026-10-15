"""Unified work discovery with public source provenance and fresh observations."""
import gzip
import io
import json
import pandas as pd
import streamlit as st
from radar import ROOT, compare, export_excel, validate_snapshot
from live_data import load_live_metadata, sync_public_observations, fill_searched_titles
from works import build_works, filter_works, query_title_ids, export_works_excel

st.set_page_config(page_title='Webtoon Radar',page_icon='📡',layout='wide')
st.markdown('<style>.stApp{background:#f6f8fa}h1{color:#123c4a}[data-testid="stMetric"]{background:white;border:1px solid #dce7eb;border-radius:12px;padding:16px}</style>',unsafe_allow_html=True)
st.caption('WEBTOON RADAR / 하나의 작품, 여러 연재처')
st.title('작품을 발견하는 새로운 기준')
st.write('네이버웹툰과 시리즈의 같은 작품을 함께 보고, 관심 수·장르·태그로 찾아보세요.')

def load_snapshots():
    data={}
    latest=ROOT/'catalog_latest.json'
    if latest.exists():
        value=json.loads(latest.read_text(encoding='utf-8-sig'));validate_snapshot(value);data[value['snapshot_id']]=value
    for path in sorted((ROOT/'history').glob('*.json.gz')):
        value=json.loads(gzip.decompress(path.read_bytes()).decode());validate_snapshot(value);data[value['snapshot_id']]=value
    return sorted(data.values(),key=lambda x:x['observed_at'],reverse=True)

sync=sync_public_observations()
snapshots=load_snapshots()
if not snapshots:
    st.info('수집된 작품 목록이 없습니다. GitHub Actions에서 수집을 실행하세요.');st.stop()
snapshot=snapshots[0]
st.sidebar.title('작품 탐색')
query=st.sidebar.text_input('작품명·작가·태그 검색',placeholder='예: 억만무림, 나노마신, 먼치킨',key='query')
metadata=load_live_metadata()
lookup_ids=query_title_ids(snapshot,query)
missing=[i for i in lookup_ids if i not in metadata['webtoon'] and i not in metadata.get('unavailable',{}).get('webtoon',{})]
if missing:
    with st.spinner('검색한 작품의 공개 상세정보를 확인하고 있습니다…'):
        lookup=fill_searched_titles(missing)
    metadata=load_live_metadata()
    if lookup['state']=='temporarily_unavailable':
        st.info('현재 공개 정보 응답을 확인할 수 없어 저장된 자료를 표시합니다. 수집 상태는 작품별 출처에서 확인할 수 있습니다.')
works=build_works(snapshot,metadata)
genres=st.sidebar.multiselect('장르',sorted({g for w in works for g in w['genres']}),key='genres')
tags=st.sidebar.multiselect('관심·다운로드·댓글·공식 태그',sorted({t for w in works for t in w['tags'] if not t.startswith('장르:')}),key='tags')
mode=st.sidebar.radio('선택한 태그 조건',['모두 포함','하나 이상 포함'],horizontal=True,key='tag_mode')
with st.sidebar.expander('연재처·작품 상태'):
    venues=st.multiselect('연재처',['네이버웹툰','네이버시리즈'],help='비워두면 모든 연재처를 함께 검색합니다.',key='venues')
    status=st.multiselect('작품 상태',['연재','휴재','완결','미표시'],key='statuses')
    group=st.selectbox('웹툰 수록 목록',['전체','월','화','수','목','금','토','일','매일+','완결목록'],key='group')
with st.sidebar.expander('숫자로 검색'):
    minimums={
        'interest':st.number_input('관심 수 최소 · 웹툰',min_value=0,step=10000,value=0,key='min_interest'),
        'downloads':st.number_input('다운로드 수 최소 · 시리즈',min_value=0,step=100000,value=0,key='min_downloads'),
        'comments':st.number_input('댓글 수 최소 · 시리즈',min_value=0,step=1000,value=0,key='min_comments')}
    st.caption('0은 조건을 적용하지 않습니다. 값이 없는 작품은 숫자 조건 검색에서 제외됩니다.')
sort_options={'관심 수 높은 순':'interest','다운로드 수 높은 순':'downloads','댓글 수 높은 순':'comments','작품명 순':'title'}
sort_label=st.sidebar.selectbox('정렬',list(sort_options),key='sort')
if st.sidebar.button('최신 데이터 가져오기'):
    result=sync_public_observations(force=True)
    if result['state']=='unavailable':st.sidebar.warning('공개 저장소에 연결하지 못했습니다. 기존 자료를 유지합니다.')
    else:st.rerun()
st.sidebar.caption('전체 수집 결과는 자동으로 갱신됩니다. 검색한 미수집 작품은 최대 12개까지 즉시 확인합니다.')

view=filter_works(works,query,tags,mode,minimums,venues)
if genres:view=[w for w in view if set(genres)&set(w['genres'])]
if status:view=[w for w in view if w.get('status') in status]
if group!='전체':
    ids={m['title_id'] for m in snapshot['memberships'] if m['group']==group}
    view=[w for w in view if w['webtoon_id'] in ids]
sort_key=sort_options[sort_label]
view=sorted(view,key=lambda w:w['title'].casefold() if sort_key=='title' else (w.get(sort_key) is None,-(w.get(sort_key) or 0),w['title']))
a,b,c,d=st.columns(4)
a.metric('통합 작품',f'{len(works):,}')
b.metric('웹툰·시리즈 함께 연재',f"{sum(len(w['venues'])>1 for w in works):,}")
c.metric('현재 검색 결과',f'{len(view):,}')
d.metric('관심 수 확인',f"{sum(w['interest'] is not None for w in works):,}")
webtoon_ids={t['title_id'] for t in snapshot['titles']}
known=len(webtoon_ids & metadata['webtoon'].keys())
restricted=len((webtoon_ids & metadata.get('unavailable',{}).get('webtoon',{}).keys())-metadata['webtoon'].keys())
st.progress((known+restricted)/len(webtoon_ids),text=f'웹툰 상세 확인 {known:,} / {len(webtoon_ids):,}개 · 공개 접근 제한 {restricted:,}개')
if known+restricted<len(webtoon_ids):
    st.caption('전체 상세정보를 순차 수집 중입니다. 아직 확인되지 않은 작품도 제목으로 검색하면 공개 정보를 바로 확인합니다.')
st.caption('관심 수는 네이버웹툰, 다운로드·댓글 수는 시리즈의 공개 집계입니다. 약 표시는 축약된 원문을 환산한 값입니다. 연재처는 수집한 공개 자료 기준입니다.')

@st.fragment(run_every='60s')
def refresh_status():
    result=sync_public_observations()
    if result.get('updated'):st.rerun(scope='app')
    if result.get('state')=='unavailable':st.caption('저장소 연결이 지연되어 보관된 자료를 표시하고 있습니다.')
    else:st.caption('공개 저장소의 새 수집 결과를 5분 간격으로 확인합니다.')
refresh_status()

search_tab,coverage_tab=st.tabs(['작품·태그 검색','수집 범위·주간 변화'])
with search_tab:
    st.subheader('검색 결과')
    if not view:st.info('선택한 조건에 맞는 작품이 없습니다. 태그나 최소값 조건을 줄여 보세요.')
    else:
        display=[{'작품명':w['title'],'연재처':' / '.join(w['venues']),'장르':', '.join(w['genres']),
                  '관심 수 · 웹툰':w['interest_display'],'다운로드 · 시리즈':w['downloads_display'],'댓글 · 시리즈':w['comments_display'],
                  '검색 태그':' · '.join(w['tags']),'확인 상태':w['collection_state'],'작가':w['author'],
                  '웹툰':w['webtoon_url'] or None,'시리즈':w['series_url'] or None} for w in view]
        st.dataframe(pd.DataFrame(display),hide_index=True,width='stretch',height=min(500,76+len(view)*35),
                     column_config={'웹툰':st.column_config.LinkColumn('웹툰',display_text='작품 페이지'),
                                    '시리즈':st.column_config.LinkColumn('시리즈',display_text='작품 페이지'),
                                    '연재처':st.column_config.TextColumn(width='medium'),
                                    '검색 태그':st.column_config.TextColumn(width='large')})
        if len(view)<=10:
            for w in view:
                with st.expander(f"{w['title']} · 연재처별 출처와 관측 시각",expanded=len(view)==1):
                    st.write(f"관심 {w['interest_display']} · 다운로드 {w['downloads_display']} · 댓글 {w['comments_display']}")
                    st.write('장르: '+(', '.join(w['genres']) or '공개 정보 확인 중'))
                    st.write(' · '.join(w['tags']) or '공개 태그 없음')
                    for source in w['sources']:
                        st.markdown(f"**{source['platform']} · {source['title']}**")
                        st.caption(f"{source['collection_state']} · 관측 UTC: {source.get('observed_at') or '아직 관측되지 않음'} · 작가: {source.get('author','')}")
                        values=[]
                        for key,label in [('interest','관심'),('downloads','다운로드'),('comments','댓글')]:
                            if source['metrics'][key]['value'] is not None:values.append(f"{label} {source[key+'_display']}")
                        if values:st.write(' / '.join(values))
                        st.link_button(source['platform']+' 작품 페이지',source['source_url'])
                    if len(w['sources'])>1:st.caption('연결 근거: '+w['link_basis']+'. 판본이 여러 개면 시리즈 연재판을 우선 표시하며, 수치를 합산하지 않습니다.')
    st.caption('같은 제목과 작가를 대조해 통합합니다. 동명 작품이나 연결 근거가 부족한 작품은 별도로 유지합니다. 값이 없는 항목은 0으로 처리하지 않습니다.')
    buffer=io.BytesIO();export_works_excel(view,buffer)
    st.download_button('통합 검색 결과 Excel 내려받기',buffer.getvalue(),'webtoon_radar_search.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
with coverage_tab:
    st.info(f"시리즈는 공개 만화 홈·일간 TOP100·이전 발견 작품 {len(metadata['series']):,}개의 범위입니다. 자동 접근이 제한된 전체 분류 목록은 수집하지 않습니다.")
    st.caption(f"웹툰 목록 관측 {snapshot['observed_at']} · 상세정보 마지막 저장 {metadata.get('updated_at','')} (UTC)")
    selected=st.selectbox('목록 관측 시각 (UTC)',range(len(snapshots)),format_func=lambda i:snapshots[i]['observed_at'])
    historical=snapshots[selected];previous=snapshots[selected+1] if selected+1<len(snapshots) else None
    changes=compare(historical,previous)
    if changes['baseline']:st.info('첫 목록 관측입니다. 이후 관측이 누적되면 진입·이탈과 목록 위치 변화를 비교합니다.')
    else:st.success(f"이전 관측 대비 목록 진입 {len(changes['added'])}개 · 목록 이탈 {len(changes['removed'])}개. 신작·서비스 종료를 의미하지 않습니다.")
    left,right=st.columns(2)
    with left:st.bar_chart(pd.DataFrame(historical['coverage']).set_index('group')['items'],color='#168a82',height=230)
    with right:st.bar_chart(pd.Series([r['status'] for r in historical['titles']]).value_counts(),color='#366a97',height=230)
    st.dataframe(pd.DataFrame(historical['coverage']),hide_index=True)
    st.write('제외: '+', '.join(historical['exclusions']))
    for name,run in metadata.get('runs',{}).items():
        st.write(('네이버웹툰' if name=='webtoon' else '네이버시리즈')+' 마지막 게시 수집: '+{'complete':'완료','partial':'일부 완료','running':'진행 중','stopped':'중단'}.get(run.get('state'),'대기'))
        if run.get('unavailable'):st.caption(f"공개 접근을 확인할 수 없어 제외: {run['unavailable']:,}개. 로그인·연령 확인으로 진행하지 않습니다.")
        for error in run.get('errors',[]):st.warning(error)
    st.caption('과거 관측 선택은 목록·위치 비교에 적용됩니다. 통합 검색은 작품별 최신 상세정보를 사용합니다. 출처별 관측 시각은 검색 결과의 상세 영역과 Excel에서 확인할 수 있습니다.')
    buffer=io.BytesIO();export_excel(historical,buffer,changes,metadata)
    st.download_button('전체 웹툰 목록 Excel 내려받기',buffer.getvalue(),'webtoon_catalogue.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    pdf=ROOT/'outputs'/'summary.pdf'
    if selected==0 and pdf.exists():st.download_button('요약 보고서 PDF',pdf.read_bytes(),'summary.pdf','application/pdf')
st.caption('첨부 조사 PDF는 과제 보고서의 참고·증거 자료입니다. 검색 데이터는 네이버의 공개 작품 정보에서 수집합니다.')

