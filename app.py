"""Cover-first search, separate completed shelf, and sourced work analytics."""
import io
import json
import math
from html import escape
import altair as alt
import pandas as pd
import streamlit as st
from radar import ROOT
from live_data import series_coverage
from works import filter_works,search_text,export_works_excel
from dashboard_data import dataset,signature,background_sync
from publishers import publisher_rankings,publisher_key,UNKNOWN
from notion_export import export_notion
from urllib.parse import urlencode
from catalog_ui import STYLES,card_grid,shelf_works
from trends import metric_history

st.set_page_config(page_title='Webtoon Radar · 작품 탐색',page_icon='📡',layout='wide',initial_sidebar_state='collapsed')
st.markdown(STYLES,unsafe_allow_html=True)
st.markdown('<div class="radar-brand">◉ WEBTOON RADAR<span>작품을 찾고, 변화를 읽다</span></div>',unsafe_allow_html=True)

def korean_time(stamp):
    return pd.Timestamp(stamp).tz_convert('Asia/Seoul').strftime('%Y.%m.%d %H:%M') if stamp else '아직 관측되지 않음'

def excel_button(rows,label='검색 결과 Excel',key='excel'):
    version=(signature(),tuple(w['work_id'] for w in rows))
    if st.button(label+' 준비',key=key+'_prepare'):
        with st.spinner('엑셀 파일을 만들고 있습니다…'):
            buffer=io.BytesIO();export_works_excel(rows,buffer)
            st.session_state[key+'_file']=(version,buffer.getvalue())
    saved=st.session_state.get(key+'_file')
    if saved and saved[0]==version:
        st.download_button(label+' 다운로드',saved[1],'webtoon_radar_search.xlsx',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',key=key)

def read_list(name):
    try:
        value=json.loads(st.query_params.get(name,'[]'))
        return value if isinstance(value,list) and all(isinstance(v,str) for v in value) else []
    except (ValueError,TypeError):return []

def number_param(name):
    try:return max(0,int(st.query_params.get(name,'0')))
    except ValueError:return 0

if not (ROOT/'catalog_latest.json').exists():st.info('작품 목록을 준비하고 있습니다.');st.stop()
snapshot,metadata,works=dataset()
selected=st.query_params.get('work','')
weekly_run=metadata.get('runs',{}).get('webtoon',{})
if weekly_run.get('refresh_since'):
    st.caption(f"최근 갱신 시작 {korean_time(weekly_run['refresh_since'])} · 연재·휴재 갱신 / 완결 작품은 마지막 자료 보관")

def show_detail(work):
    if st.button('← 검색 화면으로',key='back'):
        del st.query_params['work'];st.rerun()
    left,right=st.columns([1,2.8],gap='large')
    with left:
        if work['cover_url']:
            st.markdown(f'<img class="radar-cover" src="{escape(work["cover_url"],quote=True)}" alt="{escape(work["title"],quote=True)} 표지" referrerpolicy="no-referrer">',unsafe_allow_html=True)
        else:st.info('공개 표지가 아직 연결되지 않았습니다.')
    with right:
        st.markdown('<p class="radar-kicker">WORK INSIGHT</p>',unsafe_allow_html=True)
        st.title(work['title'])
        st.write(work['author'])
        labels=[work['status'],*work['genres'],*work['venues']]
        chips=('<span class="radar-chip new">신작</span>' if work['is_new'] else '')+''.join(f'<span class="radar-chip">{escape(x)}</span>' for x in labels)
        st.markdown(chips,unsafe_allow_html=True)
        days=' · '.join(work.get('serial_days',[]))
        st.write('연재요일: '+(('완결'+(' · 마지막 편성 '+days if days else '')) if work['status']=='완결' else (days or '공식 요일 미표시')))
        publisher=work.get('publisher')
        if publisher:
            st.markdown(f'제작사: **{escape(publisher)}** · 시리즈 출판사 기준')
            st.link_button('이 제작사의 작품 보기','?'+urlencode({'shelf':'publishers','publisher':publisher_key(publisher)}))
        elif work.get('publisher_state')=='no_series_link':
            st.caption('제작사: 공식 웹툰 정보에 시리즈 연결이 제공되지 않아 출판사를 확인할 수 없습니다.')
        elif work.get('publisher_state')=='not_listed':
            st.caption('제작사: 확인한 시리즈 작품 페이지에 출판사 표시가 없습니다.')
        else:
            st.caption('제작사: 시리즈 출판사 확인 중')
        if work['is_new']:st.caption('신작 기준: 네이버웹툰 공식 목록의 신작 표시 · '+korean_time(snapshot['observed_at']))
        a,b=st.columns(2);c,d=st.columns(2)
        a.metric('관심 수 · 웹툰',work['interest_display'])
        b.metric('조회수 · 웹툰 공개 목록',work['views_display'])
        c.metric('다운로드 · 시리즈',work['downloads_display'])
        d.metric('댓글 · 시리즈',work['comments_display'])
        links=st.columns(3)
        if work['webtoon_url']:links[0].link_button('네이버웹툰에서 보기 ↗',work['webtoon_url'],use_container_width=True)
        if work['series_url']:links[1].link_button('시리즈에서 보기 ↗',work['series_url'],use_container_width=True)
        with links[2]:excel_button([work],'작품 Excel','detail_excel')
    st.caption('관심은 네이버웹툰, 다운로드·댓글은 시리즈의 관측값입니다. 약 표시는 원문 축약값입니다. 조회수는 일부 작품의 공개 목록에만 제공되며 집계 범위는 명시되지 않았습니다.')
    if not work['series_url']:
        check=metadata.get('series_checks',{}).get(work['webtoon_id'],{})
        st.info('시리즈 연결 미확인 · '+check.get('reason','공개 공통번호로 연결을 확인하지 못했습니다.')+' 미연재를 의미하지 않습니다.')
    elif work['series_link_state']=='linked_restricted':
        st.info('시리즈 연결은 확인했습니다. 해당 시리즈 작품의 상세 정보에 로그인이 필요해 다운로드·댓글 수를 가져올 수 없습니다. 위의 ‘시리즈에서 보기’로 작품을 열 수 있습니다.')
    st.divider();st.subheader('숫자로 보는 작품의 변화')
    st.caption('실제로 수집한 시각의 값을 연결합니다. 완결 작품은 마지막 관측까지 보관하며 추가 갱신하지 않습니다.' if work['status']=='완결' else '실제로 수집한 시각의 값을 연결합니다. 매주 관측이 쌓이면 더 긴 흐름을 볼 수 있습니다.')
    history=metric_history(work,metadata,snapshot,allow_legacy=False)
    metric_labels={'관심 수':'interest','조회수':'views','시리즈 다운로드':'downloads','시리즈 댓글':'comments'}
    selected_metric=st.radio('변화 지표',list(metric_labels),horizontal=True,label_visibility='collapsed',key='trend_metric')
    key=metric_labels[selected_metric];points=history[key]
    if not points:
        st.info(f'{selected_metric}의 공개 관측값이 아직 없습니다. 값이 확인되면 이곳에 그래프가 나타납니다.')
    else:
        df=pd.DataFrame(points)
        df['관측 시각']=pd.to_datetime(df['observed_at'],utc=True).dt.tz_convert('Asia/Seoul').dt.tz_localize(None)
        df['원문']=df.apply(lambda r:('약 ' if r['approximate'] else '')+r['display'],axis=1)
        chart=alt.Chart(df).mark_line(point=alt.OverlayMarkDef(size=65),color='#15966f',strokeWidth=3).encode(
            x=alt.X('관측 시각:T',title='관측 시각 · 한국시간',axis=alt.Axis(format='%m/%d %H:%M')),
            y=alt.Y('value:Q',title=selected_metric,scale=alt.Scale(zero=False)),
            tooltip=[alt.Tooltip('관측 시각:T',format='%Y-%m-%d %H:%M'),alt.Tooltip('원문:N'),alt.Tooltip('source_url:N',title='출처')]).properties(height=300)
        st.altair_chart(chart,use_container_width=True)
        if len(points)==1:st.caption('현재 관측 1회입니다. 다음 관측 전에는 증가·감소를 계산하지 않습니다.')
        else:
            change=points[-1]['value']-points[0]['value']
            st.caption(f"실제 관측 {len(points)}회 · {korean_time(points[0]['observed_at'])} → {korean_time(points[-1]['observed_at'])} · 변화 {change:+,}"+(' (축약 표시값 기준)' if any(p['approximate'] for p in points) else ''))
        if key=='views':st.caption('공개 목록의 viewCount 중 양수로 확인된 값만 기록합니다. 다수 작품의 0은 제공 여부가 불분명하여 관측 0으로 쓰지 않습니다. 조회수와 다운로드 수는 서로 다른 지표입니다.')
        with st.expander('그래프의 관측 기록 보기'):
            st.dataframe(df[['관측 시각','원문','source_url']].rename(columns={'source_url':'출처'}),hide_index=True,use_container_width=True)
            st.download_button('관측 기록 CSV',df[['observed_at','value','display','approximate','source_url']].to_csv(index=False).encode('utf-8-sig'),'work_observations.csv','text/csv')
    with st.expander('장르·태그와 연재처별 출처',expanded=True):
        st.write(' · '.join(work['tags']) or '공개 태그 없음')
        for source in work['sources']:
            st.markdown(f"**{source['platform']} · {source['title']}**")
            st.caption(f"{source.get('author','')} · {source['collection_state']} · 관측 {korean_time(source.get('observed_at'))}")
            st.link_button('원문 확인 ↗',source['source_url'])
        st.caption('연결 근거: '+work['link_basis']+'. 플랫폼·판본 간 수치는 합산하지 않습니다.')
        if work['series_link_check']:
            st.link_button('공식 공통 작품번호 연결 근거 ↗',work['series_link_check']['resolution_url'])

def show_publishers():
    st.subheader('제작사별 작품과 다운로드')
    st.caption('네이버시리즈의 출판사 기준입니다. 확보된 작품의 대표 시리즈 판본을 한 번씩 집계합니다. 완결 작품은 보관된 마지막 값이 포함됩니다.')
    chosen=st.query_params.get('publisher')
    company_query='';order='다운로드 합계 많은 순'
    if not chosen:
        a,b=st.columns([2,1])
        company_query=a.text_input('제작사 찾기',placeholder='예: REDICE STUDIO, 네이버웹툰')
        order=b.selectbox('제작사 정렬',['다운로드 합계 많은 순','작품 수 많은 순'])
    ranks=publisher_rankings(works,'works' if order=='작품 수 많은 순' else 'downloads')
    known=[g for g in ranks if g['key']]
    group=next((g for g in ranks if (g['key'] or '__unknown__')==chosen),None)
    if group:
        if st.button('← 제작사 전체 순위'):
            del st.query_params['publisher'];st.query_params['page']=0;st.rerun()
        st.subheader(group['publisher'])
        st.write(f"작품 {group['works']:,}개 · 다운로드 합계 {group['downloads_display']} · 다운로드 확인 {group['downloads_known']:,}개")
        status=st.radio('제작사 작품 범위',['전체','연재·휴재','완결'],horizontal=True)
        ids=set(group['work_ids']);rows=[w for w in works if w['work_id'] in ids]
        if status!='전체':rows=[w for w in rows if (w['status']=='완결')==(status=='완결')]
        rows=sorted(rows,key=lambda w:(w['downloads'] is None,-(w['downloads'] or 0),w['title']))
        pages=max(1,math.ceil(len(rows)/24));page=min(number_param('page'),pages-1)
        context={'shelf':'publishers','publisher':chosen,'page':page}
        st.markdown(card_grid(rows[page*24:(page+1)*24],context),unsafe_allow_html=True)
        a,b,c=st.columns([1,3,1])
        if a.button('← 이전',disabled=page==0,key='pubprev'):st.query_params['page']=page-1;st.rerun()
        b.caption(f'{page+1} / {pages} 페이지 · {len(rows):,}개 작품')
        if c.button('다음 →',disabled=page==pages-1,key='pubnext'):st.query_params['page']=page+1;st.rerun()
        excel_button(rows,'제작사 작품 Excel','publisher_excel')
    else:
        x,y,z=st.columns(3)
        x.metric('확인된 제작사',f'{len(known):,}')
        y.metric('제작사 확인 작품',f'{sum(g["works"] for g in known):,}')
        z.metric('제작사 미확인 작품',f'{sum(g["works"] for g in ranks if not g["key"]):,}')
        from collections import Counter
        states=Counter(w.get('publisher_state','pending') for w in works if not w.get('publisher'))
        labels={'pending':'확인 대기','no_series_link':'공식 시리즈 연결 미제공','not_listed':'원문 출판사 미표시',
                'unavailable':'작품 페이지 재확인 필요','identity_review':'작품 연결 재확인 필요'}
        if states:st.caption(' · '.join(f'{labels.get(k,k)} {v:,}개' for k,v in states.items()))
        st.caption('완결 작품의 수치는 마지막 자료를 유지하며, 누락된 출판사 정보는 별도로 보완합니다.')
        publisher_run=metadata.get('runs',{}).get('publishers',{})
        if publisher_run.get('state')=='running' and publisher_run.get('target'):
            st.progress(min(1,publisher_run.get('checked',0)/publisher_run['target']),text=f"출판사 정보 확인 {publisher_run.get('checked',0):,} / {publisher_run['target']:,}")
        matching=[(i,g) for i,g in enumerate(known,1) if search_text(company_query) in search_text(g['publisher'])]
        for g in ranks:
            if not g['key'] and (not company_query or search_text(company_query) in search_text(UNKNOWN)):matching.append(('—',g))
        body=[]
        for rank,g in matching:
            url='?'+urlencode({'shelf':'publishers','publisher':g['key'] or '__unknown__','page':0})
            body.append(f'<tr><td>{rank}</td><td><a target="_self" href="{escape(url,quote=True)}">{escape(g["publisher"])}</a></td><td>{g["works"]:,}</td><td>{g["downloads_display"]}</td><td>{g["downloads_known"]:,} / {g["works"]:,}</td><td>{g["finished"]:,}</td></tr>')
        st.markdown('<div class="publisher-table"><table><thead><tr><th>순위</th><th>제작사 · 선택하면 작품 보기</th><th>작품 수</th><th>다운로드 합계</th><th>다운로드 확인</th><th>완결 보관</th></tr></thead><tbody>'+''.join(body)+'</tbody></table></div>',unsafe_allow_html=True)
    st.caption('약 표시는 원문 축약값이 포함된 합계입니다. 미확인 값은 0으로 간주하지 않고 합계에서 제외합니다. 제작사 미확인은 순위에 포함하지 않습니다. 출판사가 실제 제작 스튜디오와 다를 수 있습니다.')

def show_search():
    st.markdown('<p class="radar-kicker">FIND YOUR NEXT WEBTOON</p>',unsafe_allow_html=True)
    st.title('어떤 웹툰을 찾고 있나요?')
    st.markdown('<div class="radar-sub">표지로 만나고, 데이터로 살펴보세요. 같은 작품의 웹툰과 시리즈 정보를 한곳에서 확인합니다.</div>',unsafe_allow_html=True)
    labels=['연재·휴재','완결 보관함','제작사별'];shelves=['ongoing','finished','publishers']
    selected_shelf=st.query_params.get('shelf','ongoing')
    shelf_label=st.radio('작품 서가',labels,index=shelves.index(selected_shelf) if selected_shelf in shelves else 0,horizontal=True,key='shelf',label_visibility='collapsed')
    shelf=shelves[labels.index(shelf_label)]
    st.query_params['shelf']=shelf
    if shelf=='publishers':show_publishers();return
    pool=shelf_works(works,shelf)
    with st.form('search',border=True):
        a,b,c=st.columns([3,1.5,0.7],vertical_alignment='bottom')
        query=a.text_input('작품명·태그',value=st.query_params.get('q',''),placeholder='예: 전지적 독자시점, 호랑이 형님, 신작',key='query')
        author=b.text_input('작가',value=st.query_params.get('author',''),placeholder='작가명으로 검색',key='author')
        submitted=c.form_submit_button('검색',type='primary',use_container_width=True)
        a,b,c=st.columns([2,2,1])
        all_genres=sorted({g for w in pool for g in w['genres']})
        genres=a.multiselect('장르',all_genres,default=[g for g in read_list('genres') if g in all_genres])
        all_tags=sorted({t for w in pool for t in w['tags'] if not t.startswith('장르:')})
        tags=b.multiselect('태그 · 모두 포함',all_tags,default=[t for t in read_list('tags') if t in all_tags])
        new_only=c.checkbox('신작만 보기',value=shelf!='finished' and st.query_params.get('new')=='1',disabled=shelf=='finished',help='네이버웹툰이 공식 신작으로 표시한 작품입니다. 완결 보관함에는 적용하지 않습니다.')
        with st.expander('요일·연재처·숫자 조건'):
            d,e=st.columns(2)
            days=['전체','월','화','수','목','금','토','일','매일+']
            day=d.selectbox('연재 요일',days,index=days.index(st.query_params.get('day','전체')) if st.query_params.get('day','전체') in days else 0,key='day')
            venues=e.multiselect('연재처',['네이버웹툰','네이버시리즈'],default=[v for v in read_list('venues') if v in ('네이버웹툰','네이버시리즈')],help='같은 작품은 두 연재처를 한 카드에 표시합니다.')
            m1,m2,m3=st.columns(3)
            minimums={'interest':m1.number_input('관심 수 이상',min_value=0,value=number_param('min_interest'),step=10000),
                      'downloads':m2.number_input('다운로드 수 이상',min_value=0,value=number_param('min_downloads'),step=100000),
                      'comments':m3.number_input('댓글 수 이상',min_value=0,value=number_param('min_comments'),step=1000)}
            st.caption('0은 조건을 적용하지 않습니다. 값이 없는 작품은 숫자 조건 검색에서 제외됩니다.')
    view=filter_works(pool,query,tags,minimums=minimums,venues=venues)
    if author:view=[w for w in view if search_text(author) in search_text(w['author'])]
    if genres:view=[w for w in view if set(genres)&set(w['genres'])]
    if new_only:view=[w for w in view if w['is_new']]
    if day!='전체':
        ids={m['title_id'] for m in snapshot['memberships'] if m['group']==day};view=[w for w in view if w['webtoon_id'] in ids]
    heading,sortcol=st.columns([3,1],vertical_alignment='bottom')
    heading.subheader('완결 작품' if shelf=='finished' else ('새롭게 시작한 이야기' if new_only else '작품 둘러보기'))
    sorts=['관심 수 높은 순','다운로드 수 높은 순','댓글 수 높은 순','작품명 순']
    sort=sortcol.selectbox('정렬',sorts,index=sorts.index(st.query_params.get('sort',sorts[0])) if st.query_params.get('sort',sorts[0]) in sorts else 0,label_visibility='collapsed')
    sortkey={'관심 수 높은 순':'interest','다운로드 수 높은 순':'downloads','댓글 수 높은 순':'comments','작품명 순':'title'}[sort]
    view=sorted(view,key=lambda w:w['title'] if sortkey=='title' else (w[sortkey] is None,-(w[sortkey] or 0),w['title']))
    context={'q':query,'author':author,'shelf':shelf,'new':'1' if new_only else '0','genres':json.dumps(genres,ensure_ascii=False),'tags':json.dumps(tags,ensure_ascii=False)}
    context.update(day=day,venues=json.dumps(venues,ensure_ascii=False),sort=sort,**{'min_'+k:v for k,v in minimums.items()})
    try:page=max(0,int(st.query_params.get('page','0')))
    except ValueError:page=0
    if submitted or st.session_state.get('filter_signature',str((context,day,venues,minimums,sort)))!=str((context,day,venues,minimums,sort)):page=0
    st.session_state['filter_signature']=str((context,day,venues,minimums,sort))
    pages=max(1,math.ceil(len(view)/24));page=min(page,pages-1);context['page']=page
    st.query_params.from_dict(context)
    st.markdown(f'<div class="radar-summary"><strong>검색 결과 {len(view):,}개</strong><strong>신작 {sum(w["is_new"] for w in pool):,}개</strong>표지를 선택하면 상세 지표와 변화 그래프가 열립니다.</div>',unsafe_allow_html=True)
    if view:st.markdown(card_grid(view[page*24:(page+1)*24],context),unsafe_allow_html=True)
    else:
        st.info('현재 서가에서 조건에 맞는 작품을 찾지 못했습니다. 완결 작품은 ‘완결 보관함’에서 검색하세요.' if shelf=='ongoing' else '조건에 맞는 완결 작품이 없습니다. 제목이나 필터를 확인해 주세요.')
    prev,position,nxt=st.columns([1,3,1])
    if prev.button('← 이전',disabled=page==0,use_container_width=True):st.query_params['page']=page-1;st.rerun()
    position.markdown(f'<p style="text-align:center;color:#677b70;padding-top:8px">{page+1} / {pages} 페이지</p>',unsafe_allow_html=True)
    if nxt.button('다음 →',disabled=page>=pages-1,use_container_width=True):st.query_params['page']=page+1;st.rerun()
    excel_button(view)
    st.caption('신작 태그는 네이버웹툰 공식 new 표시입니다. 새 회차(UP) 또는 수집기에 처음 발견된 작품을 신작으로 바꾸지 않습니다. 표지는 공식 이미지 주소를 연결하며 이미지 파일을 보관하지 않습니다.')

if selected:
    work=next((w for w in works if w['work_id']==selected),None)
    if work:show_detail(work)
    else:
        st.info('현재 목록에서 해당 작품을 찾지 못했습니다.')
        if st.button('검색 화면 열기'):del st.query_params['work'];st.rerun()
else:show_search()

with st.expander('수집 현황과 자료 안내'):
    known=sum(r['title_id'] in metadata['webtoon'] for r in snapshot['titles'])
    st.write(f"네이버웹툰 {len(snapshot['titles']):,}개 · 상세 확인 {known:,}개 · 시리즈 공개 작품 {len(metadata['series']):,}개")
    coverage=series_coverage(snapshot,metadata)
    st.progress(coverage['checked']/max(coverage['total'],1),text=f"연재·휴재 시리즈 연결 조사 {coverage['checked']:,} / {coverage['total']:,}개 · 공식 연결 확인 {coverage['linked']:,}개 · 지표 접근 제한 {coverage['restricted']:,}개")
    st.caption('목록 관측 '+korean_time(snapshot['observed_at'])+' · 매주 월요일 09:17 자동 실행')
    for platform,label in [('webtoon','웹툰'),('series','시리즈')]:
        stamps=[r['observed_at'] for r in metadata.get(platform,{}).values() if r.get('observed_at')]
        if stamps:st.caption(f"{label} 상세 지표 실제 관측 범위: {korean_time(min(stamps))} ~ {korean_time(max(stamps))}")
    st.caption('연재·휴재 작품은 주간 수집에서 시리즈 연결과 지표를 갱신합니다. 완결 작품의 목록·지표·연결은 마지막 관측을 보존합니다. 검색과 상세 화면은 저장된 자료를 즉시 표시합니다. 네이버웹툰에 없는 시리즈 전체 카탈로그까지 확보했다는 뜻은 아닙니다.')
    st.caption('과거 값은 실제 관측만 보존합니다. 관심·조회수·다운로드는 서로 다른 지표이며 합산하지 않습니다. 자료 없음은 0이나 미연재를 뜻하지 않습니다.')
    st.link_button('GitHub 저장소와 실행 기록 ↗','https://github.com/imfucgingsaxmaster/webtoon-survey')
    if st.button('최신 공개 데이터 가져오기'):
        background_sync(force=True)
        st.info('최신 저장 자료를 백그라운드에서 확인합니다. 갱신되면 화면에 반영됩니다.')
    pdf=ROOT/'outputs'/'summary.pdf'
    if pdf.exists():st.download_button('수집 요약 PDF',pdf.read_bytes(),'summary.pdf','application/pdf')
st.caption('Webtoon Radar · 공개 작품 메타데이터 분석 · 표지와 작품의 권리는 각 권리자에게 있습니다.')

with st.expander('노션으로 가져오기·공유'):
    st.write('소개 페이지와 작품·제작사 순위 CSV를 함께 내려받습니다. ZIP을 풀어 노션의 가져오기로 추가한 뒤 페이지를 공유할 수 있습니다.')
    if st.button('노션 공유 파일 준비'):
        st.session_state['notion_file']=(signature(),export_notion(works,snapshot))
    ready=st.session_state.get('notion_file')
    if ready and ready[0]==signature():st.download_button('노션용 ZIP 다운로드',ready[1],'webtoon_radar_notion.zip','application/zip')
    st.caption('내보낸 시점의 사본입니다. 노션 사본은 자동 갱신되지 않으며 최신 파일은 이곳에서 다시 받을 수 있습니다.')

@st.fragment(run_every='60s')
def refresh_status():
    result=background_sync()
    if result.get('updated'):st.rerun(scope='app')
    if result.get('state')=='unavailable':st.caption('저장소 연결 지연 · 보관된 자료 표시 중')
refresh_status()
