"""Safe, responsive cover-card presentation and mutually exclusive shelves."""
from html import escape
from urllib.parse import urlencode
from radar import public_cover_url

STYLES='''<style>
.stApp{background:#f7f8f5;color:#172b27}
.block-container{max-width:1440px;padding:1rem 2.5rem 3rem}
[data-testid="stVerticalBlock"]{gap:.65rem}
h1,h2,h3{color:#132e26;letter-spacing:-.04em}h1{font-size:2rem!important;padding-top:0!important}
header[data-testid="stHeader"]{background:transparent}
.radar-brand{font-size:15px;letter-spacing:.12em;font-weight:850;border-bottom:1px solid #dce4de;padding-bottom:12px;margin-bottom:12px}
.radar-brand span{float:right;font-size:11px;letter-spacing:.08em;color:#73837a;padding-top:4px}
.radar-kicker{color:#168465;font-size:11px;font-weight:750;letter-spacing:.15em;margin:0}
.radar-sub{color:#6e7c75;line-height:1.5;margin-bottom:4px}
[data-testid="stForm"]{background:white;border:1px solid #dce4de;border-radius:12px;padding:14px}
[data-testid="stBaseButton-primary"]{background:#168465;border-color:#168465}
[data-testid="stMetric"]{background:white;border:1px solid #e0e7e1;border-radius:10px;padding:16px}
[data-testid="stMetricValue"]{font-size:1.7rem}
.radar-summary{color:#64786e;font-size:13px;border-top:1px solid #dce4de;padding:18px 0 4px}
.radar-summary strong{color:#173c2f;margin-right:22px}
.radar-grid{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:24px 18px;margin:16px 0 32px}
a.radar-card{display:block;min-width:0;text-decoration:none!important;color:#172b27!important;border-radius:10px;outline-offset:5px;transition:transform .18s}
a.radar-card:hover{transform:translateY(-4px)}a.radar-card:focus-visible{outline:3px solid #159c72}
.radar-art{position:relative;aspect-ratio:3/4;background:#e5ebe5;border-radius:9px;overflow:hidden}
.radar-art img{display:block;width:100%;height:100%;object-fit:cover}
.radar-no-cover{height:100%;display:flex;align-items:center;justify-content:center;color:#75867b;font-size:13px}
.radar-badge{position:absolute;left:9px;top:9px;background:#0c9c65;color:white;padding:4px 8px;font-size:11px;font-weight:800;border-radius:4px;box-shadow:0 1px 6px #0002}
.radar-finished{background:#263e34}
.radar-name{font-size:16px;line-height:1.35;font-weight:750;margin:11px 0 5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.radar-author{font-size:12px;color:#78867e;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.radar-venues{font-size:11px;font-weight:650;color:#168465;margin:7px 0 6px}
.radar-count{font-size:12px;color:#405b4c}
.radar-cover{display:block;width:100%;max-height:430px;object-fit:cover;border-radius:12px}
.radar-chip{display:inline-block;padding:5px 10px;margin:0 6px 6px 0;background:#e7efe8;border-radius:5px;font-size:12px;color:#3a6251}
.radar-chip.new{background:#0c9c65;color:white;font-weight:750}
.publisher-table{overflow:auto;max-height:650px;margin:16px 0 24px;border:1px solid #dce4de;border-radius:10px;background:white}
.publisher-table table{width:100%;border-collapse:collapse;font-size:14px;white-space:nowrap}
.publisher-table th{position:sticky;top:0;background:#e7efe8;text-align:left;color:#244d3d}
.publisher-table td,.publisher-table th{padding:13px 16px;border-bottom:1px solid #e6ece7}
.publisher-table a{color:#08734e;font-weight:650}.publisher-table tr:hover{background:#f2f7f1}
@media(max-width:1100px){.radar-grid{grid-template-columns:repeat(4,minmax(0,1fr))}.block-container{padding:1.6rem}}
@media(max-width:650px){.radar-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:20px 12px}.radar-brand span{display:none}h1{font-size:1.8rem!important}.block-container{padding:1rem}.radar-name{font-size:14px}}
</style>'''

def shelf_works(works,shelf='ongoing'):
    return [w for w in works if (w['status']=='완결')==(shelf=='finished')]

def card_grid(works,context=None):
    cards=[]
    for w in works:
        href='?'+urlencode(dict(context or {},work=w['work_id']))
        cover=public_cover_url(w.get('cover_url'))
        art=f'<img src="{escape(cover,quote=True)}" alt="{escape(w["title"],quote=True)} 표지" loading="lazy" referrerpolicy="no-referrer">' if cover else '<div class="radar-no-cover">표지 준비 중</div>'
        badge='<span class="radar-badge">신작</span>' if w.get('is_new') else ('<span class="radar-badge radar-finished">완결</span>' if w['status']=='완결' else '')
        venues=' · '.join('웹툰' if v=='네이버웹툰' else '시리즈' for v in w['venues'])
        count='관심 '+w['interest_display'] if w['interest'] is not None else ('다운로드 '+w['downloads_display'] if w['downloads'] is not None else w['collection_state'])
        cards.append(f'<a class="radar-card" href="{escape(href,quote=True)}" target="_self" aria-label="{escape(w["title"],quote=True)} 상세 분석"><div class="radar-art">{art}{badge}</div><div class="radar-name">{escape(w["title"])}</div><div class="radar-author">{escape(w["author"])}</div><div class="radar-venues">{escape(venues)}</div><div class="radar-count">{escape(count)}</div></a>')
    return '<div class="radar-grid">'+''.join(cards)+'</div>'
