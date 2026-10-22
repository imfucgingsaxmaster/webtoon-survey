import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import radar
import metadata
import live_data
import series_links
import tests as fixtures
from verify_refresh import refresh_report


class WeeklyRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.snapshot = radar.collect(fixtures.FakeClient())
        self.since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        self.old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        self.now = radar.utcnow()
        self.details = {'schema_version': 1, 'webtoon': {}, 'series': {}, 'series_checks': {}, 'runs': {}}
        for work in self.snapshot['titles']:
            ident = work['title_id']
            row = fixtures.MetadataTests().webtoon()
            row.update(title_id=ident, observed_at=self.old, contents_no=None)
            self.details['webtoon'][ident] = row
            self.details['series_checks'][ident] = {'state': 'no_identity', 'version': live_data.LINK_CHECK_VERSION, 'observed_at': self.old}
        live_data.write_json(self.root / 'catalog_latest.json', self.snapshot)
        live_data.write_json(self.root / 'metadata_latest.json', self.details)

    def test_recent_records_refresh_once_and_resume_across_batches(self):
        calls = []
        class Client:
            def get(inner, path, **params):
                calls.append(str(params['titleId']))
                return {'titleId': int(params['titleId']), 'titleName': 'Test', 'favoriteCount': 321, 'curationTagList': []}
        with patch.object(metadata, 'PublicClient', return_value=Client()):
            reused = metadata.collect_metadata(self.root, webtoon_limit=2, series_limit=0)
            self.assertEqual(reused['runs']['webtoon']['fetched'], 0)
            for _ in range(5):
                result = metadata.collect_metadata(self.root, webtoon_limit=2, series_limit=0, refresh_since=self.since)
            self.assertEqual(result['runs']['webtoon']['fresh'], 10)
            self.assertEqual(result['runs']['webtoon']['state'], 'complete')
            metadata.collect_metadata(self.root, webtoon_limit=2, series_limit=0, refresh_since=self.since)
        self.assertEqual(len(calls), 10)
        self.assertEqual(len(set(calls)), 10)

    def test_recent_links_rechecked_once_in_weekly_batches(self):
        self.assertEqual(series_links.pending_ids(self.snapshot, self.details), [])
        self.assertEqual(len(series_links.pending_ids(self.snapshot, self.details, self.since)), 10)
        with patch.object(live_data.time, 'sleep'):
            result = series_links.collect_links(self.root, limit=3, all_pending=True, refresh_since=self.since)
            again = series_links.collect_links(self.root, limit=3, all_pending=True, refresh_since=self.since)
        self.assertEqual(result['checked'], 10)
        self.assertEqual(again['checked'], 0)
        self.assertEqual(live_data.load_live_metadata(self.root)['runs']['series_links']['remaining'], 0)

    def test_success_label_cannot_hide_old_observations(self):
        self.details['runs']['webtoon'] = {'state': 'complete', 'fresh': 10}
        report = refresh_report(self.snapshot, self.details, self.since)
        self.assertEqual(report['state'], 'incomplete')
        self.assertEqual(report['webtoon']['not_checked'], 10)
        self.assertEqual(report['links_not_checked'], 10)
        for row in self.details['webtoon'].values():row['observed_at'] = self.now
        for row in self.details['series_checks'].values():row['observed_at'] = self.now
        self.details['series']['50'] = {'observed_at': self.old}
        self.assertEqual(refresh_report(self.snapshot, self.details, self.since)['state'], 'incomplete')
        self.details['unavailable'] = {'series': {'50': {'observed_at': self.now}}}
        report = refresh_report(self.snapshot, self.details, self.since)
        self.assertEqual(report['state'], 'complete')
        self.assertEqual(report['series']['new_observations'], 0)
        self.assertEqual(report['series']['unavailable_this_run'], 1)

    def test_naive_boundary_rejected(self):
        with self.assertRaises(ValueError):metadata.refresh_boundary('2026-10-19T00:00:00')


if __name__ == '__main__':unittest.main()
