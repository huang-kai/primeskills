"""歌手/歌名分开检索：错误分类不静默换歌，提示用户明确名称。"""
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from primebuddy.skill_manager import load_entry

ROOT = Path(__file__).resolve().parents[1]


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.module = load_entry(ROOT, 'src/qqmusic_client.py')
        self.song = SimpleNamespace(name='甲乙丙丁', title='甲乙丙丁',
                                   singer=[SimpleNamespace(name='测试歌手')])
        self.search = AsyncMock(return_value=SimpleNamespace(song=[self.song]))
        self.client = SimpleNamespace(search=SimpleNamespace(search_by_type=self.search))

    def query(self, name, kind):
        with patch.dict('sys.modules', {'qqmusic_api': SimpleNamespace(Client=object)}):
            return asyncio.run(self.module.search(self.module.SearchQuery(name, kind), client=self.client))

    def test_title_misclassified_as_artist_has_explicit_hint(self):
        with self.assertRaisesRegex(self.module.SkillError, '没有找到匹配的歌手，但找到了同名歌曲'):
            self.query('甲乙丙丁', 'artist')
        self.search.assert_awaited_once()

    def test_explicit_title_matches_without_changing_artist(self):
        self.assertEqual(self.query('甲乙丙丁', 'song'), [self.song])

    def test_absent_artist_and_title_have_distinct_messages(self):
        for kind, noun in (('artist', '歌手'), ('song', '歌曲')):
            with self.assertRaisesRegex(self.module.SkillError, '没有找到匹配的' + noun):
                self.query('不存在的名称', kind)
