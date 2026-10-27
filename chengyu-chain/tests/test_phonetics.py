"""只用本地词典检查读音，不调用模型或机器人。"""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from chengyu_game.phonetics import matching_transcription, sound


class PhoneticsTests(unittest.TestCase):
    def test_tones_and_only_selected_nasal_finals_are_ignored(self):
        for left, right in (('jīng', 'jin4'), ('ang2', 'ān'), ('shàng', 'shan3'),
                            ('fēng', 'fen4'), ('yīng', 'yin2'), ('en1', 'eng2')):
            with self.subTest(left=left, right=right):
                self.assertEqual(sound(left), sound(right))
        self.assertEqual(sound(' SHÌ '), 'shi')

    def test_other_initials_finals_and_umlaut_stay_distinct(self):
        for left, right in (('lan', 'nan'), ('zan', 'zhan'), ('cen', 'chen'),
                            ('sen', 'shen'), ('lu', 'lü'), ('long', 'lun'),
                            ('lai', 'lei'), ('lian', 'liang'), ('huan', 'huang')):
            with self.subTest(left=left, right=right):
                self.assertNotEqual(sound(left), sound(right))
        for spelling in ('lǜ', 'lü4', 'lu:4', 'lv4', 'lu\u0308\u0300'):
            self.assertEqual(sound(spelling), 'lv')

    def test_invalid_pinyin_is_rejected(self):
        for value in (None, 3, '', ' ', '心', 'xin1 xin1', 'jin0', 'a/b', 'x\u0308'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                sound(value)

    def test_full_same_sound_transcription_can_be_verified(self):
        for raw, idiom in (('我接一马当仙！', '一马当先'), ('新想事成', '心想事成'),
                           ('斤斤计较', '斤斤计较'), ('津津计较', '斤斤计较'),
                           ('路力同心', '戮力同心'),
                           ('我接，心 想 事 成。', '心想事成')):
            with self.subTest(raw=raw):
                self.assertTrue(matching_transcription(raw, idiom))

    def test_no_missing_characters_unrelated_corrections_or_fuzzy_initials(self):
        for raw, idiom in (('一马当', '一马当先'), ('一马当中', '一马当先'),
                           ('心想吃事成', '心想事成'), ('事平八稳', '四平八稳'),
                           ('努力同心', '戮力同心'),
                           ('吕力同心', '戮力同心')):
            with self.subTest(raw=raw, idiom=idiom):
                self.assertFalse(matching_transcription(raw, idiom))

    def test_polyphones_use_phrase_context_and_exact_text_needs_no_guess(self):
        self.assertTrue(matching_transcription('我接长幼有序', '长幼有序'))
        self.assertTrue(matching_transcription('掌幼有序', '长幼有序'))
        self.assertFalse(matching_transcription('常幼有序', '长幼有序'))

    def test_unknown_characters_and_multiple_distinct_matches_are_not_corrected(self):
        self.assertFalse(matching_transcription('新想事成还是心享事成', '心想事成'))
        self.assertFalse(matching_transcription('abc', '心想事成'))
        self.assertFalse(matching_transcription('', '心想事成'))
        self.assertFalse(matching_transcription(None, '心想事成'))
        self.assertFalse(matching_transcription('心想事成', '心想事成！'))
        with patch('chengyu_game.phonetics.lazy_pinyin', return_value=['心', '想', '事', '成']):
            self.assertFalse(matching_transcription('新想事成', '心想事成'))


if __name__ == '__main__':
    unittest.main()
