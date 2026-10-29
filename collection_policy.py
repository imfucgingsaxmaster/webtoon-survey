"""Completed works are an archive, never a refresh queue."""

def frozen_ids(snapshot, metadata):
    webtoon={r['title_id'] for r in snapshot['titles'] if r.get('status')=='완결'}
    contents={metadata.get('webtoon',{}).get(i,{}).get('contents_no') for i in webtoon}-{None,''}
    series={i for i,r in metadata.get('series',{}).items()
            if r.get('status')=='완결' or r.get('original_product_id') in contents}
    series.update(c['series_id'] for i,c in metadata.get('series_checks',{}).items()
                  if i in webtoon and c.get('series_id'))
    # Include alternate editions linked by the same conservative rules as the UI.
    from works import normalized,base_title,same_creators
    from collections import defaultdict
    by_title=defaultdict(list)
    for row in snapshot['titles']:
        if row['title_id'] in webtoon:by_title[normalized(base_title(row['title']))].append(row)
    for ident,row in metadata.get('series',{}).items():
        candidates=by_title[normalized(base_title(row.get('title','')))]
        if any(same_creators(w,row) for w in candidates):series.add(ident)
    return {'webtoon':webtoon,'series':series}

def active_webtoon_ids(snapshot):
    return {r['title_id'] for r in snapshot['titles'] if r.get('status')!='완결'}
