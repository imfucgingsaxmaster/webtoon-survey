"""Verify actual per-record dates, not a collector's success label."""
import argparse
import json
from datetime import datetime
from pathlib import Path
from radar import ROOT, utcnow
from metadata import refresh_boundary
from live_data import load_live_metadata, write_json


def refresh_report(snapshot, details, refresh_since):
    boundary = refresh_boundary(refresh_since)

    def recent(row):
        return bool(row and row.get('observed_at') and datetime.fromisoformat(row['observed_at']) >= boundary)

    report = {'started_at': boundary.isoformat(), 'verified_at': utcnow(),
              'catalogue_current': recent(snapshot)}
    webtoon_ids = {row['title_id'] for row in snapshot['titles']}
    series_ids = set(details.get('series', {})) | set(details.get('unavailable', {}).get('series', {}))
    for platform, ids in [('webtoon', webtoon_ids), ('series', series_ids)]:
        observed = details.get(platform, {})
        blocked = details.get('unavailable', {}).get(platform, {})
        fresh = [ident for ident in ids if recent(observed.get(ident))]
        retained = [ident for ident in ids if not recent(observed.get(ident)) and recent(blocked.get(ident))]
        missing = sorted(ids - set(fresh) - set(retained))
        report[platform] = {'target': len(ids), 'new_observations': len(fresh),
                            'unavailable_this_run': len(retained), 'not_checked': len(missing),
                            'missing_examples': missing[:10]}
    checks = details.get('series_checks', {})
    missing_links = [ident for ident in webtoon_ids if not recent(checks.get(ident)) or checks[ident].get('state') == 'parse_error']
    report['links_not_checked'] = len(missing_links)
    report['state'] = 'complete' if (report['catalogue_current'] and not missing_links and
        all(report[p]['not_checked'] == 0 for p in ('webtoon', 'series'))) else 'incomplete'
    return report


def verify(root=ROOT, refresh_since=None):
    root = Path(root)
    snapshot = json.loads((root / 'catalog_latest.json').read_text(encoding='utf-8'))
    details = load_live_metadata(root)
    report = refresh_report(snapshot, details, refresh_since)
    details['runs']['weekly_verification'] = report
    write_json(root / 'metadata_latest.json', details)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    if report['state'] != 'complete':
        raise RuntimeError('This refresh has unobserved records; do not report success')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh-since', required=True)
    verify(refresh_since=parser.parse_args().refresh_since)
