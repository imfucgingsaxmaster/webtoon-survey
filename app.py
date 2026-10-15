"""Read-only dashboard for actual catalogue observations. PDFs are not inputs."""
import gzip
import io
import json
import pandas as pd
import streamlit as st
from radar import ROOT, compare, export_excel, flattened, validate_snapshot

st.set_page_config(page_title='Webtoon Radar',page_icon='📡',layout='wide')
st.markdown('<style>.stApp{background:#f6f8fa}h1{color:#123c4a}[data-testid="stMetric"]{background:white;border:1px solid #dce7eb;border-radius:12px;padding:16px}</style>',unsafe_allow_html=True)
st.caption('WEBTOON RADAR / PUBLIC CATALOGUE OBSERVATORY')
st.title('네이버웹툰 전체 작품 조사')
st.write('요일별 연재 · 매일+ · 완결 목록을 작품 ID 기준으로 통합합니다.')

@st.cache_data(ttl=300)
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
st.sidebar.title('작품 탐색')
platform=st.sidebar.selectbox('플랫폼',['네이버웹툰','네이버시리즈'])
if platform=='네이버시리즈':
    st.warning('수집 대기: 시리즈 전체 분류 목록은 robots.txt에서 자동 접근을 제한합니다. 허가된 피드 연결이 필요합니다.');st.stop()
selected=st.sidebar.selectbox('관측 시각 (UTC)',range(len(snapshots)),format_func=lambda i:snapshots[i]['observed_at'])
snapshot=snapshots[selected]
previous=snapshots[selected+1] if selected+1<len(snapshots) else None
changes=compare(snapshot,previous)
df=pd.DataFrame(flattened(snapshot));view=df.copy()
query=st.sidebar.text_input('작품명 또는 작가 검색')
status=st.sidebar.multiselect('작품 상태',['연재','휴재','완결'])
group=st.sidebar.selectbox('목록',['전체','월','화','수','목','금','토','일','매일+','완결목록'])
if query:view=view[view.title.str.contains(query,case=False,regex=False)|view.author.str.contains(query,case=False,regex=False)]
if status:view=view[view.status.isin(status)]
if group!='전체':view=view[view.title_id.isin({m['title_id'] for m in snapshot['memberships'] if m['group']==group})]
st.caption(f"관측 {snapshot['observed_at']} · 공개 정식 작품 카탈로그 · 수집 완료")
a,b,c,d=st.columns(4)
a.metric('전체 고유 작품',f'{len(df):,}');b.metric('목록 항목',f"{len(snapshot['memberships']):,}")
c.metric('현재 검색 결과',f'{len(view):,}');d.metric('저장된 관측',len(snapshots))
if changes['baseline']:st.info('첫 관측입니다. 매주 결과가 누적되면 작품 진입·이탈과 목록 내 위치 변화를 비교합니다.')
else:st.success(f"이전 관측 대비 목록 진입 {len(changes['added'])}개 · 목록 이탈 {len(changes['removed'])}개. 신작·서비스 종료를 의미하지 않습니다.")
left,right=st.columns(2)
with left:
    st.subheader('목록별 수록 항목');st.bar_chart(pd.DataFrame(snapshot['coverage']).set_index('group')['items'],color='#168a82',height=230)
with right:
    st.subheader('전체 작품 상태');st.bar_chart(df.status.value_counts(),color='#366a97',height=230)
st.subheader('작품 목록')
st.caption('공개 별점은 이용자 평가값입니다. 조회수·매출로 해석하지 않으며 장르를 제목으로 추정하지 않습니다.')
display=view.rename(columns={'title_id':'작품 ID','title':'작품명','author':'작가','status':'상태','rating':'공개 별점','groups':'수록 목록','url':'원문'})
st.dataframe(display[['작품 ID','작품명','작가','상태','공개 별점','수록 목록','원문']],hide_index=True,width='stretch',height=420,column_config={'원문':st.column_config.LinkColumn('원문',display_text='작품 정보'),'공개 별점':st.column_config.NumberColumn(format='%.2f')})
with st.expander('인기순 목록 위치 · 수집 범위 검증'):
    st.write('위치는 해당 목록 내부 인기순 정렬 순서이며 플랫폼 전체 통합 순위가 아닙니다.')
    pos=pd.DataFrame(snapshot['memberships']);pos['작품명']=pos.title_id.map(dict(zip(df.title_id,df.title)))
    if group!='전체':pos=pos[pos.group==group]
    st.dataframe(pos.rename(columns={'title_id':'작품 ID','group':'목록','position':'위치'}),hide_index=True)
    st.dataframe(pd.DataFrame(snapshot['coverage']),hide_index=True)
    st.write('제외: '+', '.join(snapshot['exclusions']))
    st.caption('요일별·매일+는 응답 배열 전체, 완결은 모든 페이지와 응답 총계를 대조합니다. 공개 목록 밖의 작품은 확인할 수 없습니다.')
buffer=io.BytesIO();export_excel(snapshot,buffer,changes)
st.download_button('전체 작품 Excel 내려받기',buffer.getvalue(),'webtoon_radar.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
pdf=ROOT/'outputs'/'summary.pdf'
if selected==0 and pdf.exists():st.download_button('요약 보고서 PDF',pdf.read_bytes(),'summary.pdf','application/pdf')
st.caption('출처: 네이버웹툰 공개 목록. 조사 PDF는 과제 보고서의 참고·증거 자료이며 이 대시보드의 입력 데이터가 아닙니다.')
