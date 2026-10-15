"""Read-only exploration of public observations. Research PDFs are never inputs."""
import gzip
import io
import json
import pandas as pd
import streamlit as st
from radar import ROOT, compare, export_excel, validate_snapshot
from metadata import load_metadata, build_rows, filter_rows, export_search_excel

st.set_page_config(page_title='Webtoon Radar',page_icon='📡',layout='wide')
st.markdown('<style>.stApp{background:#f6f8fa}h1{color:#123c4a}[data-testid="stMetric"]{background:white;border:1px solid #dce7eb;border-radius:12px;padding:16px}</style>',unsafe_allow_html=True)
st.caption('WEBTOON RADAR / PUBLIC CATALOGUE OBSERVATORY')
st.title('작품을 발견하는 새로운 기준')
st.write('네이버웹툰 전체 공개 작품 목록에서 관심 수·장르·공식 태그로 찾아보세요.')

@st.cache_data(ttl=60)
def load_snapshots():
    data={}
    latest=ROOT/'catalog_latest.json'
    if latest.exists():
        value=json.loads(latest.read_text(encoding='utf-8'));validate_snapshot(value);data[value['snapshot_id']]=value
    for path in sorted((ROOT/'history').glob('*.json.gz')):
        value=json.loads(gzip.decompress(path.read_bytes()).decode());validate_snapshot(value);data[value['snapshot_id']]=value
    return sorted(data.values(),key=lambda x:x['observed_at'],reverse=True)

snapshots=load_snapshots()
if not snapshots:
    st.info('수집된 작품 목록이 없습니다. GitHub Actions에서 수집을 실행하세요.');st.stop()
snapshot=snapshots[0];metadata=load_metadata()
all_rows=build_rows(snapshot,metadata)
st.sidebar.title('작품 탐색')
platform=st.sidebar.selectbox('플랫폼',['네이버웹툰','네이버시리즈','전체'])
rows=[r for r in all_rows if platform=='전체' or r['platform']==platform]
query=st.sidebar.text_input('작품명·작가·태그 검색',placeholder='예: 나노마신, 먼치킨')
genres=st.sidebar.multiselect('장르',sorted({g for r in rows for g in r['genres']}))
tags=st.sidebar.multiselect('관심·다운로드·댓글·공식 태그',sorted({t for r in rows for t in r['tags'] if not t.startswith('장르:')}))
mode=st.sidebar.radio('선택한 태그 조건',['모두 포함','하나 이상 포함'],horizontal=True)
status=st.sidebar.multiselect('작품 상태',['연재','휴재','완결','미표시'])
group=st.sidebar.selectbox('웹툰 수록 목록',['전체','월','화','수','목','금','토','일','매일+','완결목록'],disabled=platform=='네이버시리즈')
minimums={}
with st.sidebar.expander('숫자로 검색'):
    if platform!='네이버시리즈':minimums['interest']=st.number_input('관심 수 최소',min_value=0,step=10000,value=0)
    if platform!='네이버웹툰':
        minimums['downloads']=st.number_input('다운로드 수 최소',min_value=0,step=100000,value=0)
        minimums['comments']=st.number_input('댓글 수 최소',min_value=0,step=1000,value=0)
    st.caption('0은 조건을 적용하지 않습니다. 미확인 값은 숫자 조건 검색에서 제외됩니다.')
sort_options={'관심 수 높은 순':'interest','다운로드 수 높은 순':'downloads','댓글 수 높은 순':'comments','작품명 순':'title'}
sort_label=st.sidebar.selectbox('정렬',list(sort_options),index=1 if platform=='네이버시리즈' else 0)
if st.sidebar.button('최신 저장 결과 다시 읽기'):st.cache_data.clear();st.rerun()
view=filter_rows(rows,query,tags,mode,minimums)
if genres:view=[r for r in view if set(genres)&set(r['genres'])]
if status:view=[r for r in view if r.get('status') in status]
if group!='전체' and platform!='네이버시리즈':
    ids={m['title_id'] for m in snapshot['memberships'] if m['group']==group}
    view=[r for r in view if r['platform']=='네이버웹툰' and r['title_id'] in ids]
sort_key=sort_options[sort_label]
view=sorted(view,key=lambda r:r['title'].casefold() if sort_key=='title' else (r.get(sort_key) is None,-(r.get(sort_key) or 0),r['title']))
known=sum(bool(r.get('observed_at')) for r in rows)
a,b,c,d=st.columns(4)
a.metric('플랫폼 작품 목록',f'{len(rows):,}');b.metric('상세정보 확인',f'{known:,}')
c.metric('현재 검색 결과',f'{len(view):,}');d.metric('관심 수 확인',f"{sum(r['interest'] is not None for r in rows):,}")
st.caption(f"웹툰 목록 관측 {snapshot['observed_at']} · 상세정보 마지막 저장 {metadata.get('updated_at','대기')} (UTC)")
if platform!='네이버시리즈':
    webtoon_known=sum(bool(metadata.get('webtoon',{}).get(t['title_id'])) for t in snapshot['titles'])
    st.progress(webtoon_known/len(snapshot['titles']),text=f"웹툰 상세정보 {webtoon_known:,} / {len(snapshot['titles']):,}개 확인 · 미수집 작품도 전체 목록에 유지")
if platform!='네이버웹툰':st.info('시리즈는 공개 만화 홈·일간 TOP100·이전 발견 작품 범위입니다. 전체 분류 목록은 자동 접근 제한으로 수집하지 않습니다.')
st.caption('관심 수는 네이버웹툰 작품 페이지의 공개 집계입니다. 시리즈 다운로드·댓글 수는 해당 플랫폼 표시값이며, 축약 표시는 ‘약’으로 구분합니다.')
search_tab,coverage_tab=st.tabs(['작품·태그 검색','수집 범위·주간 변화'])
with search_tab:
    st.subheader('검색 결과')
    if not view:st.info('선택한 조건에 맞는 작품이 없습니다. 태그나 최소값 조건을 줄여 보세요.')
    else:
        display=[]
        for r in view:
            display.append({'플랫폼':r['platform'],'작품명':r['title'],'작가':r.get('author',''),'장르':', '.join(r['genres']),
                            '관심 수':r['interest_display'],'다운로드 수':r['downloads_display'],'댓글 수':r['comments_display'],
                            '검색 태그':' · '.join(r['tags']),'상세 관측 UTC':r.get('observed_at') or '수집 대기','원문':r['source_url']})
        columns=['플랫폼','작품명','장르']
        if platform!='네이버시리즈':columns+=['관심 수']
        if platform!='네이버웹툰':columns+=['다운로드 수','댓글 수']
        columns+=['검색 태그','상세 관측 UTC','작가','원문']
        st.dataframe(pd.DataFrame(display)[columns],hide_index=True,width='stretch',height=460,
                     column_config={'원문':st.column_config.LinkColumn('원문',display_text='작품 페이지'),'검색 태그':st.column_config.TextColumn(width='large')})
        if len(view)<=10:
            for r in view:
                with st.expander(f"{r['title']} · {r['platform']}"):
                    st.write('장르: '+(', '.join(r['genres']) or '미확인'))
                    st.write(f"관심 {r['interest_display']} · 다운로드 {r['downloads_display']} · 댓글 {r['comments_display']}")
                    st.write(' / '.join(r['tags']) or '상세정보 수집 대기')
                    st.link_button('공개 작품 페이지',r['source_url'])
    st.caption('미확인은 0이 아닙니다. 지표 태그와 숫자 필터는 화면 표시값을 환산한 값으로 계산합니다. 플랫폼이 다른 작품은 제목이 같아도 합치지 않습니다.')
    buffer=io.BytesIO();export_search_excel(view,buffer)
    st.download_button('검색 결과 Excel 내려받기',buffer.getvalue(),'webtoon_radar_search.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
with coverage_tab:
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
        st.write(('네이버웹툰' if name=='webtoon' else '네이버시리즈')+' 상세 수집: '+{'complete':'완료','partial':'일부 완료','running':'진행 중','stopped':'중단'}.get(run.get('state'),'대기'))
        if run.get('unavailable'):st.caption(f"공개 접근을 확인할 수 없어 제외: {run['unavailable']:,}개. 로그인·연령 확인으로 진행하지 않습니다.")
        for error in run.get('errors',[]):st.warning(error)
    st.caption('과거 관측 선택은 목록·위치 비교에 적용됩니다. 작품·태그 검색은 가장 최근에 저장된 작품별 상세정보를 사용하며 각 관측 시각을 표시합니다.')
    buffer=io.BytesIO();export_excel(historical,buffer,changes,metadata)
    st.download_button('전체 웹툰 목록 Excel 내려받기',buffer.getvalue(),'webtoon_catalogue.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    pdf=ROOT/'outputs'/'summary.pdf'
    if selected==0 and pdf.exists():st.download_button('요약 보고서 PDF',pdf.read_bytes(),'summary.pdf','application/pdf')
st.caption('첨부 조사 PDF는 과제 보고서의 참고·증거 자료입니다. 검색 데이터는 네이버의 공개 작품 정보에서 수집합니다.')
