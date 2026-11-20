"""Ranking shelf: daily and seven-day changes, with transparent missing data."""
from html import escape
from urllib.parse import urlencode

import pandas as pd
import streamlit as st
from history_index import load_index
from radar import public_cover_url
from rankings import growth_rankings,local_day

def change_label(comparison):
    if comparison['delta'] is None:return '비교 자료 부족'
    return ('약 ' if comparison['approximate'] else '')+f"{comparison['delta']:+,}"

def ranking_table(result,limit):
    body=[]
    for row in result['rows'][:limit]:
        work=row['work'];comparison=row['comparison']
        href='?'+urlencode({'shelf':'rankings','rank_period':result['days'],'rank_metric':result['metric'],'work':work['work_id']})
        cover=public_cover_url(work.get('cover_url'))
        image=(f'<img src="{escape(cover,quote=True)}" alt="" loading="lazy" referrerpolicy="no-referrer" '
               'style="width:44px;height:58px;object-fit:cover;border-radius:5px;margin-right:10px;vertical-align:middle">') if cover else ''
        growth=f"{comparison['growth']:+.1f}%" if comparison['growth'] is not None else '—'
        approx='약 ' if comparison['approximate'] else ''
        start=local_stamp(comparison['start']['observed_at']);end=local_stamp(comparison['end']['observed_at'])
        body.append(f'<tr><td><strong>{row["rank"]}</strong></td>'
            f'<td><a href="{escape(href,quote=True)}" target="_self">{image}{escape(work["title"])}</a></td>'
            f'<td>{approx}{comparison["start"]["value"]:,}</td><td>{approx}{comparison["end"]["value"]:,}</td>'
            f'<td>{change_label(row["daily"])}</td><td>{change_label(row["weekly"])}</td><td>{growth}</td>'
            f'<td style="font-size:12px">{start}<br>→ {end}</td></tr>')
    return ('<div class="publisher-table"><table><thead><tr><th>순위</th><th>작품 · 상세 보기</th>'
            f'<th>{result["start_day"]:%m/%d} 수치</th><th>{result["as_of"]:%m/%d} 수치</th>'
            '<th>일일 증가량</th><th>최근 7일 증가량</th><th>선택 기간 증가율</th><th>실제 관측 시각 · 한국시간</th>'
            '</tr></thead><tbody>'+''.join(body)+'</tbody></table></div>')

def local_stamp(stamp):
    return pd.Timestamp(stamp).tz_convert('Asia/Seoul').strftime('%m/%d %H:%M')

def show_rankings(works,snapshot):
    st.subheader('지금 성장하는 작품')
    st.caption('연재·휴재 작품의 증가량 순위입니다. 같은 작품은 대표 연재처·판본을 한 번만 비교하며, 완결 작품은 제외합니다.')
    a,b,c=st.columns([1,1.5,0.7])
    period=a.radio('랭킹 기간',['최근 7일','일일'],index=1 if st.query_params.get('rank_period')=='1' else 0,horizontal=True,key='rank_period')
    metric_label=b.radio('랭킹 지표',['다운로드 증가량','관심 수 증가량'],index=1 if st.query_params.get('rank_metric')=='interest' else 0,horizontal=True,key='rank_metric')
    limit=c.selectbox('표시 작품 수',[30,50,100],key='rank_limit')
    days=1 if period=='일일' else 7;metric='interest' if metric_label=='관심 수 증가량' else 'downloads'
    st.query_params['rank_period']=str(days);st.query_params['rank_metric']=metric
    as_of=local_day(snapshot['observed_at'])
    result=growth_rankings(works,load_index(),as_of,metric,days)
    st.write(f'**비교 기준: {result["start_day"]:%Y.%m.%d} → {as_of:%Y.%m.%d} · 한국시간**')
    a,b=st.columns(2)
    a.metric('순위 집계 작품',f'{len(result["rows"]):,}개')
    b.metric('비교 자료 부족',f'{len(result["missing"]):,}개')
    st.caption('각 날짜의 마지막 실제 관측값을 비교합니다. 수집 시각에 따라 실제 시간 간격은 달라질 수 있으며, 수집 중에는 순위가 바뀝니다. 증가량이 같으면 공동 순위입니다.')
    if result['rows']:
        st.markdown(ranking_table(result,limit),unsafe_allow_html=True)
        export=[]
        for row in result['rows']:
            comp=row['comparison'];work=row['work']
            export.append({'순위':row['rank'],'작품':work['title'],'지표':metric_label,'기준일':as_of.isoformat(),
                           '비교 시작 관측':comp['start']['observed_at'],'비교 종료 관측':comp['end']['observed_at'],
                           '시작값':comp['start']['value'],'종료값':comp['end']['value'],
                           '일일 증가량':row['daily']['delta'],'최근 7일 증가량':row['weekly']['delta'],
                           '선택 기간 증가율(%)':comp['growth'],'축약 표시값 포함':comp['approximate'],
                           '출처':row['source'].get('url','')})
        st.download_button('현재 랭킹 CSV 다운로드',pd.DataFrame(export).to_csv(index=False).encode('utf-8-sig'),
                           f'webtoon_ranking_{as_of}_{metric}_{days}d.csv','text/csv')
    else:
        st.info('선택한 두 날짜의 관측값이 모두 있는 작품이 아직 없습니다. 매일 자료가 쌓이면 일일·최근 7일 랭킹에 자동 반영됩니다.')
    st.caption('다운로드는 시리즈, 관심 수는 네이버웹툰 기준입니다. 약 표시는 원문 축약값의 차이이며, 표시 변화 0이 실제 이용량 0을 뜻하지는 않습니다. 기준값이 0이면 증가율은 계산하지 않습니다.')
    if result['missing']:
        with st.expander('비교 자료가 부족한 작품'):
            st.dataframe(pd.DataFrame([{'작품':r['work']['title'],'사유':'기준일 관측 없음' if r['comparison']['state']=='missing_current' else '비교 시작일 관측 없음'}
                                       for r in result['missing']]),hide_index=True,use_container_width=True)
