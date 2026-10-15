"""Run: streamlit run app.py"""
from pathlib import Path
import tempfile
import pandas as pd
import streamlit as st
from radar import ROOT, PLATFORMS, DAYS, ingest, load_rows, summarize, export_excel, export_pdf

st.set_page_config(page_title='Webtoon Radar', page_icon='📊', layout='wide')
st.title('Webtoon Radar')
st.caption('네이버웹툰 · 네이버시리즈 | 조사 자료 탐색 및 분석')
db = ROOT/'outputs/radar.sqlite'
ingest(ROOT/'research.csv',ROOT/'sources.json',db)
all_rows = load_rows(db)
st.info('제공받은 조사 목록을 분석합니다. 원문 관측일과 정확한 순위는 미기재이며, 실시간 데이터가 아닙니다.')
with st.sidebar:
    st.header('조사 자료 필터')
    platform = st.selectbox('플랫폼',['전체',*PLATFORMS])
    snapshots = sorted({r['snapshot_id'] for r in all_rows})
    snapshot = st.selectbox('자료 묶음',snapshots)
    query = st.text_input('작품명 검색')
    genre = st.selectbox('장르',['전체',*sorted({r['genre'] for r in all_rows})])
    day = st.selectbox('연재 요일',['전체',*DAYS[:-1]])
    st.divider()
    st.caption('예정: 매주 월요일 09:17 한국시간\nGitHub Actions에서 등록 자료 재분석')
rows = [r for r in all_rows if r['snapshot_id']==snapshot
        and (platform=='전체' or r['platform']==platform)
        and (not query or query.casefold() in r['title'].casefold())
        and (genre=='전체' or r['genre']==genre)
        and (day=='전체' or r['weekday']==day)]
s = summarize(rows)
for col,label,value in zip(st.columns(4),['조사 항목','고유 작품','확인된 순위','네이버시리즈 항목'],
                           [s['records'],s['unique_titles'],s['ranked_records'],s['platform_counts'][PLATFORMS[1]]]):
    col.metric(label,value)
st.caption('조사 항목은 작품·요일별로 집계하며, 고유 작품은 플랫폼과 작품명으로 중복을 제거합니다.')
if not rows:
    st.warning('조건에 맞는 자료가 없습니다. 네이버시리즈 실측 자료는 아직 등록되지 않았습니다.' if platform==PLATFORMS[1] else '조건에 맞는 자료가 없습니다.')
else:
    c1,c2 = st.columns([1,1.25])
    with c1:
        st.subheader('장르별 조사 항목 수')
        st.bar_chart(pd.DataFrame(list(s['genre_counts'].items()),columns=['장르','항목 수']).set_index('장르'),color='#147D92')
    with c2:
        st.subheader('요일별 장르 분포')
        frame = pd.DataFrame(rows)
        pivot = pd.crosstab(frame['weekday'],frame['genre']).reindex([d for d in DAYS if d in set(frame['weekday'])])
        st.dataframe(pivot,width='stretch')
    st.subheader('작품 조사 목록')
    view = pd.DataFrame(rows)[['platform','title','weekday','genre','source_page','source_position']]
    view.columns=['플랫폼','작품명','요일','장르','원문 쪽','기재 순서']
    st.dataframe(view,hide_index=True,width='stretch')
    st.caption('기재 순서는 PDF에 적힌 위치이며 공식 인기 순위로 간주하지 않습니다.')
    with tempfile.TemporaryDirectory() as tmp:
        xlsx=Path(tmp)/'filtered.xlsx'; pdf=Path(tmp)/'filtered.pdf'
        export_excel(rows,xlsx); export_pdf(rows,pdf)
        a,b=st.columns(2)
        a.download_button('현재 결과 Excel 다운로드',xlsx.read_bytes(),'webtoon_radar.xlsx',mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        b.download_button('현재 결과 요약 보고서 다운로드',pdf.read_bytes(),'webtoon_radar_summary.pdf',mime='application/pdf')
with st.expander('자료의 범위와 추가 방법'):
    st.write('출처: 윤승준·김예은 제공 PDF 4~6쪽. 날짜·조회수·매출·관심 수는 입력되어 있지 않습니다.')
    st.write('허용된 연구 자료 또는 이용 허락을 확보한 내보내기 파일만 등록합니다. sources.json에 이용 근거를 기록한 뒤 CSV 양식에 추가합니다.')
    st.write('네이버웹툰·네이버시리즈 웹페이지 자동 수집 기능은 포함하지 않습니다. 공식 API나 계약된 자료가 확보되면 별도 연결을 검토합니다.')
