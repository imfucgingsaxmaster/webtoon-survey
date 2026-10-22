"""Cover-first search, separate completed shelf, and sourced work analytics."""
import gzip
import io
import json
import math
from html import escape
import altair as alt
import pandas as pd
import streamlit as st
from radar import ROOT,compare,validate_snapshot
from live_data import load_live_metadata,sync_public_observations,fill_searched_titles,fill_series_links,series_coverage
from works import build_works,filter_works,search_text,export_works_excel,query_title_ids
from catalog_ui import STYLES,card_grid,shelf_works
from trends import metric_history

st.set_page_config(page_title='Webtoon Radar · 작품 탐색',page_icon='📡',layout='wide',initial_sidebar_state='collapsed')
st.markdown(STYLES,unsafe_allow_html=True)
st.markdown('<div class="radar-brand">◉ WEBTOON RADAR<span>작품을 찾고, 변화를 읽다</span></div>',unsafe_allow_html=True)

def load_snapshots():
    data={}
    for path in sorted((ROOT/'history').glob('*.json.gz')):
        value=json.loads(gzip.decompress(path.read_bytes()));validate_snapshot(value);data[value['snapshot_id']]=value
    latest=ROOT/'catalog_latest.json'
    if latest.exists():
        value=json.loads(latest.read_text(encoding='utf-8-sig'));validate_snapshot(value);data[value['snapshot_id']]=value
    return sorted(data.values(),key=lambda r:r['observed_at'],reverse=True)

def korean_time(stamp):
    return pd.Timestamp(stamp).tz_convert('Asia/Seoul').strftime('%Y.%m.%d %H:%M') if stamp else '아직 관측되지 않음'

def excel_button(rows,label='검색 결과 Excel',key='excel'):
    buffer=io.BytesIO();export_works_excel(rows,buffer)
    st.download_button(label,buffer.getvalue(),'webtoon_radar_search.xlsx',
                       'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',key=key)

def read_list(name):
    try:
        value=json.loads(st.query_params.get(name,'[]'))
        return value if isinstance(value,list) and all(isinstance(v,str) for v in value) else []
    except (ValueError,TypeError):return []

def number_param(name):
    try:return max(0,int(st.query_params.get(name,'0')))
    except ValueError:return 0

sync=sync_public_observations()
snapshots=load_snapshots()
if not snapshots:st.info('작품 목록을 준비하고 있습니다.');st.stop()
snapshot=snapshots[0];metadata=load_live_metadata()
selected=st.query_params.get('work','')
if selected.startswith('webtoon:'):
    ident=selected.split(':',1)[1]
    if ident in {r['title_id'] for r in snapshot['titles']}:
        with st.spinner('작품의 공개 지표와 시리즈 연결을 확인하고 있습니다…'):
            fill_searched_titles([ident])
            lookup=fill_series_links([ident],limit=1)
        metadata=load_live_metadata()
        if lookup['state']=='temporarily_unavailable':st.info('공개 정보 응답이 지연되어 저장된 관측을 표시합니다.')
works=build_works(snapshot,metadata)
weekly_run=metadata.get('runs',{}).get('webtoon',{})
if weekly_run.get('refresh_since'):
    st.caption(f"최근 전체 갱신 시작 {korean_time(weekly_run['refresh_since'])} · 이번 실행의 웹툰 지표 새 관측 {weekly_run.get('fresh',0):,} / {len(snapshot['titles']):,}개")

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
    st.caption('실제로 수집한 시각의 값을 연결합니다. 앞으로 매주 관측이 쌓이면 더 긴 흐름을 볼 수 있습니다.')
    history=metric_history(work,metadata,snapshot)
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

def show_search():
    global metadata,works
    st.markdown('<p class="radar-kicker">FIND YOUR NEXT WEBTOON</p>',unsafe_allow_html=True)
    st.title('어떤 웹툰을 찾고 있나요?')
    st.markdown('<div class="radar-sub">표지로 만나고, 데이터로 살펴보세요. 같은 작품의 웹툰과 시리즈 정보를 한곳에서 확인합니다.</div>',unsafe_allow_html=True)
    shelf_label=st.radio('작품 서가',['연재·휴재','완결 보관함'],index=1 if st.query_params.get('shelf')=='finished' else 0,horizontal=True,key='shelf',label_visibility='collapsed')
    shelf='finished' if shelf_label=='완결 보관함' else 'ongoing'
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
    if query and submitted:
        ids=query_title_ids(snapshot,query,limit=3)
        if ids:
            with st.spinner('검색한 작품의 시리즈 연결을 확인하고 있습니다…'):
                lookup=fill_series_links(ids,limit=3)
            metadata=load_live_metadata();works=build_works(snapshot,metadata);pool=shelf_works(works,shelf)
            if lookup['state']=='temporarily_unavailable':st.info('시리즈 응답이 지연되어 저장된 연결을 표시합니다. 자동 수집에서 이어서 확인합니다.')
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
    st.progress(coverage['checked']/coverage['total'],text=f"시리즈 연결 조사 {coverage['checked']:,} / {coverage['total']:,}개 · 공식 연결 확인 {coverage['linked']:,}개 · 지표 접근 제한 {coverage['restricted']:,}개")
    st.caption('목록 관측 '+korean_time(snapshot['observed_at'])+' · 매주 월요일 09:17 자동 실행')
    for platform,label in [('webtoon','웹툰'),('series','시리즈')]:
        stamps=[r['observed_at'] for r in metadata.get(platform,{}).values() if r.get('observed_at')]
        if stamps:st.caption(f"{label} 상세 지표 실제 관측 범위: {korean_time(min(stamps))} ~ {korean_time(max(stamps))}")
    st.caption('시리즈는 공개 공통 작품번호로 전체 웹툰 목록을 순회해 연결하고, 중간 결과도 검색에 반영합니다. 작품명 검색·상세 화면에서도 미확인 연결을 보완합니다. 로그인 등으로 상세 지표가 제한되어도 공식 작품 링크는 유지합니다. 네이버웹툰에 없는 시리즈 전체 카탈로그까지 확보했다는 뜻은 아닙니다.')
    st.caption('과거 값은 실제 관측만 보존합니다. 관심·조회수·다운로드는 서로 다른 지표이며 합산하지 않습니다. 자료 없음은 0이나 미연재를 뜻하지 않습니다.')
    st.link_button('GitHub 저장소와 실행 기록 ↗','https://github.com/imfucgingsaxmaster/webtoon-survey')
    if st.button('최신 공개 데이터 가져오기'):
        result=sync_public_observations(force=True)
        if result['state']=='unavailable':st.warning('공개 저장소 연결이 지연되어 기존 자료를 유지합니다.')
        else:st.rerun()
    previous=snapshots[1] if len(snapshots)>1 else None
    changes=compare(snapshot,previous)
    if not changes['baseline']:st.caption(f"이전 목록 대비 진입 {len(changes['added'])}개 · 이탈 {len(changes['removed'])}개. 신작·서비스 종료와는 다른 기준입니다.")
    pdf=ROOT/'outputs'/'summary.pdf'
    if pdf.exists():st.download_button('수집 요약 PDF',pdf.read_bytes(),'summary.pdf','application/pdf')
st.caption('Webtoon Radar · 공개 작품 메타데이터 분석 · 표지와 작품의 권리는 각 권리자에게 있습니다.')

@st.fragment(run_every='60s')
def refresh_status():
    result=sync_public_observations()
    if result.get('updated'):st.rerun(scope='app')
    if result.get('state')=='unavailable':st.caption('저장소 연결 지연 · 보관된 자료 표시 중')
refresh_status()
