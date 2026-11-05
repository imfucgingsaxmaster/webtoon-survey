import unittest
from works import filter_works
from catalog_ui import card_grid

class GenreFilterTests(unittest.TestCase):
    def setUp(self):
        self.rows=[dict(work_id=str(i),title=str(i),author='',aliases=[],genres=g,
            tags=['태그:성장'],venues=['네이버웹툰'],interest=100)
            for i,g in enumerate([['판타지'],['액션'],['판타지','액션'],['드라마'],[]])]
    def test_all_requires_every_selected_genre(self):
        self.assertEqual([w['work_id'] for w in filter_works(self.rows,genres=['판타지','액션'],genre_mode='all')],['2'])
        self.assertEqual([w['work_id'] for w in filter_works(self.rows,genres=['판타지','액션'],genre_mode='any')],['0','1','2'])
    def test_empty_single_duplicate_and_other_conditions(self):
        for mode in ('all','any'):
            self.assertEqual(filter_works(self.rows,genres=[],genre_mode=mode),self.rows)
            self.assertEqual([w['work_id'] for w in filter_works(self.rows,genres=['판타지','판타지'],genre_mode=mode)],['0','2'])
        self.assertEqual(filter_works(self.rows,genres=['판타지','액션'],genre_mode='all',minimums={'interest':101}),[])
        self.assertEqual(filter_works(self.rows,genres=['판타지','액션'],genre_mode='all',tags=['태그:없는태그']),[])

if __name__=='__main__':unittest.main()
