"""选歌、播放时限、错误反馈与清理。"""
from pathlib import Path
from contextlib import contextmanager
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
AudioError = ValueError
import asyncio
import os
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from helpers import load_entry


class MusicAdapterTests(unittest.TestCase):
    def setUp(self):
        self.skill = load_entry(Path(__file__).resolve().parents[1], 'src/skill.py')
        global AudioError
        AudioError = self.skill.AudioError
        self.args = {'artist': '周杰伦', 'song': ''}
        self.config, self.runtime = {}, Path('/unused')
        self.cancelled = threading.Event()
        self.calls = []
        self.prepare_error = self.cleanup_error = None
        self.client = ModuleType(self.skill.__name__ + '.music.qqmusic_client')
        self.client.SkillError = type('SkillError', (Exception,), {})
        self.client.SkillNetworkError = type('SkillNetworkError', (self.client.SkillError,), {})
        self.client.load_credential = lambda: object()
        async def select(args):
            return SimpleNamespace(interval=120), 'https://example.com/song'
        self.client.select_song = select
        def play(source, **options):
            try:
                self.calls.append('prepare')
                self.check()
                if self.prepare_error:
                    raise self.prepare_error
                options['before_play']()
                self.calls.append('play')
                for callback in ('on_started', 'on_tick'):
                    if options[callback]:
                        options[callback]()
                self.check()
                self.calls.append('ended')
                return {'limited': bool(options['max_seconds'] and options['max_seconds'] < options['expected_seconds'])}
            finally:
                self.calls.append('cleanup')
                if self.cleanup_error:
                    raise self.cleanup_error
        self.robot = Mock(play_music=Mock(side_effect=play))
        self.robot.session.return_value = contextmanager(lambda: (yield self.robot))()
        self.robot.effects.side_effect = lambda enabled: contextmanager(lambda: (yield None))()
        module = self.skill
        factory = patch.object(module, 'get_robot', return_value=self.robot)
        factory.start()
        self.addCleanup(factory.stop)

    def run_skill(self):
        with patch.dict('sys.modules', {self.client.__name__: self.client}):
            return self.skill.play(**self.args, config=self.config, runtime=self.runtime, check=self.check)

    def check(self):
        if self.cancelled.is_set():
            raise RuntimeError('任务已取消')

    def test_cli_business_receives_source_duration_and_limit(self):
        self.config = {'play_seconds': 10}
        result = self.run_skill()
        self.assertEqual(result, {'status': 'success', 'message': '已播放 10 秒，歌曲已停止。'})
        args, kwargs = self.robot.play_music.call_args
        self.assertEqual(args, ('https://example.com/song',))
        self.assertEqual((kwargs['max_seconds'], kwargs['expected_seconds']), (10, 120))
        self.assertEqual(self.calls, ['prepare', 'play', 'ended', 'cleanup'])

    def test_settings_are_plain_business_data(self):
        self.assertEqual(self.skill.settings({'play_seconds': 30})['play_seconds'], 30)
        self.assertEqual(self.run_skill()['status'], 'success')

    def test_cleanup_failure_cannot_report_success(self):
        self.cleanup_error = AudioError('清理未确认')
        self.assertEqual(self.run_skill(), {'status': 'failed', 'message': '清理未确认'})

    def test_missing_login_does_not_start_player(self):
        self.client.load_credential = lambda: None
        self.assertEqual(self.run_skill()['status'], 'auth_required')
        self.robot.play_music.assert_not_called()

    def test_search_failure_is_explained_before_effects_or_playback(self):
        async def select(args):
            raise self.client.SkillError('没有找到匹配的歌手，请确认歌手名。')
        self.client.select_song = select
        result = self.run_skill()
        self.assertEqual(result['status'], 'failed')
        self.assertIn('没有找到匹配的歌手', result['message'])
        self.assertEqual(self.calls, [])

    def test_search_timeout_has_specific_message(self):
        async def select(args):
            raise TimeoutError('https://example.invalid/?token=secret')
        self.client.select_song = select
        result = self.run_skill()
        self.assertEqual(result, {'status': 'failed', 'message': '网络连接出现了问题，请重新连接网络。',
                                  'error_code': 'network_unavailable'})
        self.assertNotIn('secret', result['message'])
        self.assertEqual(self.calls, [])

    def test_qq_network_failure_has_portable_business_result(self):
        async def select(args):
            raise self.client.SkillNetworkError('private signed URL')
        self.client.select_song = select
        self.assertEqual(self.run_skill(), {'status': 'failed',
                         'message': '网络连接出现了问题，请重新连接网络。',
                         'error_code': 'network_unavailable'})
        self.assertEqual(self.calls, [])

    def test_playback_timeout_is_not_reported_as_network_failure(self):
        self.prepare_error = TimeoutError('private signed URL')
        result = self.run_skill()
        self.assertNotIn('error_code', result)
        self.assertEqual(result['message'], '歌曲播放超时，请重试。')

    def test_short_song_ends_naturally_before_configured_limit(self):
        self.config = {'play_seconds': 600}
        self.assertEqual(self.run_skill()['message'], '歌曲播放完毕。')

    def effects(self):
        self.config = {'music_effects': True}
        effects = Mock()
        effects.prepare.side_effect = lambda: self.calls.append('prepare_stand')
        effects.begin.side_effect = lambda: self.calls.append('effects_begin')
        effects.tick.side_effect = lambda: self.calls.append('effects_tick')
        module = ModuleType(self.skill.__name__ + '.effects.session')
        @contextmanager
        def session(enabled):
            try:
                yield effects
            finally:
                self.calls.append('effects_restore')
        self.robot.effects.side_effect = session
        return module, effects

    def test_effects_start_after_playback_and_restore_after_cleanup(self):
        module, effects = self.effects()
        with patch.dict('sys.modules', {module.__name__: module}):
            self.assertEqual(self.run_skill()['status'], 'success')
        self.assertEqual(self.calls, ['prepare', 'prepare_stand', 'play', 'effects_begin',
                                     'effects_tick', 'ended', 'cleanup', 'effects_restore'])
        effects.begin.assert_called_once()

    def test_failed_buffer_never_starts_standing(self):
        module, effects = self.effects()
        self.prepare_error = AudioError('缓冲失败')
        with patch.dict('sys.modules', {module.__name__: module}):
            self.assertEqual(self.run_skill()['status'], 'failed')
        effects.prepare.assert_not_called()

    def test_unconfirmed_stand_never_starts_music(self):
        module, effects = self.effects()
        effects.prepare.side_effect = AudioError('起身未确认')
        with patch.dict('sys.modules', {module.__name__: module}):
            self.assertEqual(self.run_skill()['message'], '起身未确认')
        self.assertNotIn('play', self.calls)
        effects.begin.assert_not_called()
        self.assertEqual(self.calls[-2:], ['cleanup', 'effects_restore'])

    def test_failed_effects_still_stop_song_and_restore(self):
        module, effects = self.effects()
        effects.begin.side_effect = AudioError('舞蹈请求未确认')
        with patch.dict('sys.modules', {module.__name__: module}):
            self.assertEqual(self.run_skill()['status'], 'failed')
        self.assertEqual(self.calls[-2:], ['cleanup', 'effects_restore'])

    def test_cancellation_during_music_cleans_song_and_effects(self):
        module, effects = self.effects()
        effects.tick.side_effect = self.cancelled.set
        with patch.dict('sys.modules', {module.__name__: module}):
            with self.assertRaisesRegex(RuntimeError, '取消'):
                self.run_skill()
        self.assertEqual(self.calls[-2:], ['cleanup', 'effects_restore'])


ROOT = Path(__file__).resolve().parents[1]


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.module = load_entry(ROOT, 'src/music/qqmusic_client.py')
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        environment = patch.dict(os.environ, DIANGETAI_DATA=temporary.name)
        environment.start()
        self.addCleanup(environment.stop)
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

    def test_expired_resource_and_unrefreshable_login_require_configuration(self):
        class CredentialExpiredError(Exception):
            pass
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        modules = {
            'qqmusic_api': SimpleNamespace(Client=lambda **kwargs: client),
            'qqmusic_api.core.exceptions': SimpleNamespace(CredentialExpiredError=CredentialExpiredError,
                                                           NetworkError=type('NetworkError', (Exception,), {})),
            'niquests.exceptions': SimpleNamespace(ConnectionError=ConnectionError, Timeout=TimeoutError),
        }
        with patch.dict('sys.modules', modules),\
                patch.object(self.module, 'device_path', return_value='/private-test/device.json'),\
                patch.object(self.module, 'load_credential', return_value=object()),\
                patch.object(self.module, 'refresh_login', new=AsyncMock(return_value=False)) as refresh,\
                patch.object(self.module, 'search', new=AsyncMock(return_value=[self.song])),\
                patch.object(self.module, 'song_url', new=AsyncMock(
                    side_effect=CredentialExpiredError('private signed URL'))):
            with self.assertRaisesRegex(self.module.SkillError, 'QQ 音乐登录及刷新凭据已失效') as error:
                asyncio.run(self.module.select_song({'artist': '', 'song': '甲乙丙丁'}))
        refresh.assert_awaited_once()
        self.assertIn('python3 main.py configure', str(error.exception))
        self.assertNotIn('private', str(error.exception))
        client.__aexit__.assert_awaited_once()

    def select_error(self, error):
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        modules = {
            'qqmusic_api': SimpleNamespace(Client=lambda **kwargs: client),
            'qqmusic_api.core.exceptions': SimpleNamespace(
                CredentialExpiredError=type('CredentialExpiredError', (Exception,), {}),
                NetworkError=self.network_error),
            'niquests.exceptions': SimpleNamespace(ConnectionError=ConnectionError, Timeout=TimeoutError),
        }
        with patch.dict('sys.modules', modules), \
                patch.object(self.module, 'device_path', return_value='/private-test/device.json'), \
                patch.object(self.module, 'load_credential', return_value=object()), \
                patch.object(self.module, 'search', new=AsyncMock(side_effect=error)):
            asyncio.run(self.module.select_song({'artist': '', 'song': '甲乙丙丁'}))

    def test_sdk_and_transport_failures_become_skill_network_error(self):
        self.network_error = type('NetworkError', (Exception,), {})
        for error in (self.network_error('private signed URL'), ConnectionError('private signed URL'),
                      TimeoutError('private signed URL')):
            with self.subTest(error=type(error).__name__):
                with self.assertRaises(self.module.SkillNetworkError) as caught:
                    self.select_error(error)
                self.assertEqual(str(caught.exception), '网络连接出现了问题，请重新连接网络。')

    def test_http_and_business_errors_are_not_relabelled_as_network(self):
        self.network_error = type('NetworkError', (Exception,), {})
        class HTTPError(Exception):
            pass
        for error in (HTTPError('HTTP 401'), HTTPError('HTTP 403'), HTTPError('HTTP 429'),
                      self.module.SkillError('歌曲资源权限不足'), ValueError('invalid response')):
            with self.subTest(error=str(error)):
                with self.assertRaises(type(error)) as caught:
                    self.select_error(error)
                self.assertIs(caught.exception, error)

    def test_sdk_wrapper_does_not_relabel_http_or_configuration_errors(self):
        self.network_error = type('NetworkError', (Exception,), {})
        for cause in (ValueError('invalid URL'), type('HTTPError', (Exception,), {})('HTTP 403')):
            with self.subTest(cause=type(cause).__name__):
                error = self.network_error('private signed URL')
                error.__cause__ = cause
                with self.assertRaises(self.network_error) as caught:
                    self.select_error(error)
                self.assertIs(caught.exception, error)
