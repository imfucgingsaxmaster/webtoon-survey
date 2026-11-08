"""Exercise the detail page with mixed collector/browser observation clocks."""
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from streamlit.testing.v1 import AppTest


class TrendChartTests(unittest.TestCase):
    def test_all_metrics_keep_mixed_iso_observations_and_korean_times(self):
        stamps = [
            '2026-10-17T05:58:03+00:00',
            '2026-10-29T23:00:16+00:00',
            '2026-11-05T21:11:34.339Z',
            '2026-11-06T07:12:00.123456+09:00',
        ]
        points = [dict(observed_at=stamp, value=value, display=str(value),
                       approximate=False, source_url='https://series.naver.com/')
                  for stamp, value in zip(stamps, [100, 120, 135, 140])]
        history = {key: points for key in ('interest', 'views', 'downloads', 'comments')}
        expected_times = [
            pd.Timestamp('2026-10-17 14:58:03'),
            pd.Timestamp('2026-10-30 08:00:16'),
            pd.Timestamp('2026-11-06 06:11:34.339'),
            pd.Timestamp('2026-11-06 07:12:00.123456'),
        ]
        with patch.dict(os.environ, {'WEBTOON_RADAR_OFFLINE': '1'}), \
             patch('dashboard_data.background_sync', return_value={'state': 'offline', 'updated': False}), \
             patch('trends.metric_history', return_value=history):
            app = AppTest.from_file(str(Path(__file__).with_name('app.py')), default_timeout=30)
            app.query_params['work'] = 'webtoon:855130'
            app.run()
            self.assertFalse(app.exception, str(app.exception))
            for label in ('관심 수', '조회수', '시리즈 다운로드', '시리즈 댓글'):
                with self.subTest(metric=label):
                    app.radio(key='trend_metric').set_value(label).run()
                    self.assertFalse(app.exception, str(app.exception))
                    table = app.dataframe[0].value
                    self.assertEqual(table['관측 시각'].tolist(), expected_times)
                    self.assertEqual(table['원문'].tolist(), ['100', '120', '135', '140'])
                    self.assertTrue(any('실제 관측 4회' in c.value and '변화 +40' in c.value
                                        for c in app.caption))


if __name__ == '__main__':
    unittest.main()
