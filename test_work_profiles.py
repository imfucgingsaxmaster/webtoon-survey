import copy
import unittest
from unittest.mock import patch
import radar,metadata,works,live_data,trends
from work_profiles import series_profile,webtoon_profile,estimated_revenue,works_by_author
from profile_collection import profile_targets
import tests as fixtures


class ProfileTests(unittest.TestCase):
    def test_full_description_not_collapsed_teaser_and_book_unit(self):
        html='''<div id="content"><div class="end_head"><h2>작품</h2>
        <a id="spiButton2" data-url="https://series.naver.com/comic/detail.series?productNo=1"></a></div>
        <div class="end_dsc"><div class="_synopsis">설명이 잘린..<a>더보기</a></div>
        <div class="_synopsis" style="display:none">전체&nbsp;설명<br/>두 번째 줄 &lt;안내&gt;<a>접기</a></div></div>
        <h5 class="end_total_episode">총 <strong>12</strong>권</h5></div>'''
        row=series_profile(html,'1')
        self.assertEqual(row['description'],'전체 설명\n두 번째 줄 <안내>')
        self.assertEqual((row['episode_count'],row['episode_unit']),(12,'권'))
        self.assertIsNone(row['download_check']['value'])
        self.assertEqual(row['download_check']['state'],'not_displayed')

    def test_episode_total_is_not_internal_article_number(self):
        row=webtoon_profile({'titleId':1,'titleName':'작품','synopsis':'공식 소개'},
            {'titleId':1,'totalCount':10,'articleList':[{'no':83,'subtitle':'9화 (마지막화)'}]},'1')
        self.assertEqual(row['episode_count'],10)
        self.assertEqual(row['latest_episode'],'9화 (마지막화)')
        with self.assertRaises(ValueError):webtoon_profile({'titleId':2},{'titleId':1},1)

    def test_revenue_uses_exact_user_formula_and_does_not_infer_zero(self):
        self.assertEqual(estimated_revenue(1_000_000)['value'],288_000_000)
        self.assertEqual(estimated_revenue(2100,True)['display'],'약 604,800원')
        self.assertEqual(estimated_revenue(0)['display'],'0원')
        self.assertIsNone(estimated_revenue(None)['value'])
        for v in (-1,True,1.5):
            with self.assertRaises(ValueError):estimated_revenue(v)

    def test_author_membership_is_exact_and_includes_other_venue_creators(self):
        a={'sources':[{'author':'글작가 / 그림작가'},{'author':'원작자'}]}
        b={'sources':[{'author':'글작가2'}]}
        self.assertEqual(works_by_author([a,b],'글작가'),[a])
        self.assertEqual(works_by_author([a,b],'원작자'),[a])
        self.assertEqual(works_by_author([a,b],''),[])

    def test_profile_fills_only_missing_download_and_has_own_history_clock(self):
        snap=radar.collect(fixtures.FakeClient());snap['titles'][0].update(title='같은 제목',author='글작가 / 그림작가')
        serial=fixtures.MetadataTests().series();serial['author']='글작가 / 그림작가'
        profile={'platform':'네이버시리즈','title_id':'1','state':'confirmed','observed_at':'2026-10-30T00:00:00+00:00',
                 'source_url':serial['source_url'],'download_check':{'value':2100,'display':'2.1천','approximate':True,'state':'displayed'}}
        cache={'series':{'1':serial},'profiles':{'webtoon:1':profile}}
        known=next(w for w in works.build_works(snap,cache) if w['work_id']=='webtoon:1')
        self.assertEqual(known['downloads'],286830000)
        serial['metrics']['downloads']=metadata.parse_count('')
        original=copy.deepcopy(cache)
        filled=next(w for w in works.build_works(snap,cache) if w['work_id']=='webtoon:1')
        self.assertEqual(filled['downloads'],2100);self.assertEqual(filled['estimated_revenue'],604800)
        self.assertEqual(cache,original)
        with patch.object(trends,'load_index',return_value=None):
            points=trends.metric_history(filled,cache,snap,allow_legacy=False)['downloads']
        self.assertEqual(points[0]['observed_at'],profile['observed_at'])
        profile['title_id']='99'
        rejected=next(w for w in works.build_works(snap,cache) if w['work_id']=='webtoon:1')
        self.assertIsNone(rejected['downloads'])

    def test_completed_profiles_freeze_and_failed_lookup_cannot_erase_good_profile(self):
        snap=radar.collect(fixtures.FakeClient());snap['titles'][0]['status']='완결'
        profile={'state':'confirmed','observed_at':'2026-01-01T00:00:00+00:00'}
        cache={'profiles':{'webtoon:1':profile}}
        ids={r['work_id'] for r in profile_targets(snap,cache,refresh=True)}
        self.assertNotIn('webtoon:1',ids)
        failed={'profiles':{'webtoon:1':{'state':'unavailable','observed_at':'2026-10-30T00:00:00+00:00'}}}
        self.assertEqual(live_data.merge_metadata(cache,failed)['profiles']['webtoon:1'],profile)


if __name__=='__main__':unittest.main()
