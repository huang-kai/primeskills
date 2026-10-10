from pathlib import Path
from contextlib import contextmanager
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from primebuddy.engine import Context
from primebuddy.registry import Registry


class MusicAdapterTests(unittest.TestCase):
    def setUp(self):
        self.skill = Registry(Path(__file__).resolve().parents[2]).load('diangetai')
        self.args = {'artist': '周杰伦', 'song': ''}
        self.context = Context(simulate=False, runtime=Path('/unused'))
        self.calls = []
        calls = self.calls
        self.client = ModuleType(self.skill.__name__ + '.qqmusic_client')
        self.client.SkillError = type('SkillError', (Exception,), {})
        self.client.load_credential = lambda: object()
        async def select(args):
            return SimpleNamespace(interval=2), 'unused'
        self.client.select_song = select
        self.stream = ModuleType(self.skill.__name__ + '.stream_audio')
        class Player:
            def __init__(self, *args, **kwargs):
                self.error, self.ended, self.started = False, False, None
                self.end_reason = 'natural'
                self.proc = SimpleNamespace(poll=lambda: None)
            def wait_ready(self, checkpoint):
                checkpoint()
                calls.append('ready')
            def send(self, value):
                calls.append(value)
            def update(self):
                self.started = 1
                self.ended = True
                calls.append('ended')
            def stop(self):
                calls.append('cleanup')
        self.stream.RemoteSong = Player

    def run_skill(self):
        with patch.dict('sys.modules', {self.skill.__name__ + '.qqmusic_client': self.client,
                                       self.skill.__name__ + '.stream_audio': self.stream}):
            return self.skill.run('play', self.args, self.context)

    def test_success_only_after_natural_end_and_cleanup(self):
        result = self.run_skill()
        self.assertEqual(result['status'], 'success')
        self.assertEqual(self.calls, ['ready', 'play', 'ended', 'cleanup'])

    def test_cleanup_failure_cannot_report_success(self):
        def stop(player):
            self.calls.append('cleanup_failed')
            raise RuntimeError('关闭未确认')
        with patch.object(self.stream.RemoteSong, 'stop', stop):
            with self.assertRaises(RuntimeError):
                self.run_skill()
        self.assertEqual(self.calls[-1], 'cleanup_failed')

    def test_cancel_during_prebuffer_cleans_player_without_play(self):
        def ready(player, checkpoint):
            self.context.cancelled.set()
            checkpoint()
        with patch.object(self.stream.RemoteSong, 'wait_ready', ready):
            with self.assertRaises(RuntimeError):
                self.run_skill()
        self.assertEqual(self.calls, ['cleanup'])

    def test_missing_login_does_not_start_player(self):
        self.client.load_credential = lambda: None
        self.assertEqual(self.run_skill()['status'], 'auth_required')
        self.assertEqual(self.calls, [])

    def test_search_failure_is_explained_before_effects_or_playback(self):
        from primebuddy.engine import execute
        registry = Registry(Path(__file__).resolve().parents[2])
        registry.modules['diangetai'] = self.skill
        async def select(args):
            raise self.client.SkillError('没有找到匹配的歌手，请确认歌手名。')
        self.client.select_song = select
        proposal = {'reply': '', 'tasks': [{'id': 'music', 'skill': 'diangetai', 'action': 'play',
                    'args': {'artist': '甲乙丙丁', 'song': ''}, 'after': []}]}
        spoken = []
        with patch.dict('sys.modules', {self.client.__name__: self.client, self.stream.__name__: self.stream}):
            result = execute(proposal, registry, self.context, spoken.append)
        self.assertEqual(result['music']['message'], '没有找到匹配的歌手，请确认歌手名。')
        self.assertEqual(result['music']['status'], 'failed')
        self.assertEqual(self.calls, [])
        self.assertNotIn('结果未确认', spoken[-1])

    def test_search_timeout_has_specific_message(self):
        async def select(args):
            raise TimeoutError('https://example.invalid/?token=secret')
        self.client.select_song = select
        result = self.run_skill()
        self.assertIn('超时', result['message'])
        self.assertNotIn('secret', result['message'])
        self.assertEqual(self.calls, [])

    def test_duration_starts_at_confirmed_playback_not_request(self):
        self.context.skill_configs = {'diangetai': {'play_seconds': 2}}
        clock = [0]
        def ready(player, checkpoint):
            clock[0] = 50  # 缓冲耗时不计入歌曲播放。
        def update(player):
            clock[0] += 1
            if clock[0] == 53:
                player.started = 53
            self.calls.append(clock[0])
        with patch.object(self.stream.RemoteSong, 'wait_ready', ready), \
                patch.object(self.stream.RemoteSong, 'update', update), \
                patch.object(self.skill.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(self.skill.time, 'sleep'):
            result = self.run_skill()
        self.assertIn('已播放 2 秒', result['message'])
        self.assertEqual(self.calls, ['play', 51, 52, 53, 54, 55, 'cleanup'])

    def test_short_song_ends_naturally_before_configured_limit(self):
        self.context.skill_configs = {'diangetai': {'play_seconds': 60}}
        self.assertEqual(self.run_skill()['message'], '歌曲播放完毕。')

    def effects(self):
        effects = Mock(original_light_state=None)
        def begin():
            effects.original_light_state = (1, 2, 3, 4, 5)
            self.calls.append('effects_begin')
        effects.begin_music.side_effect = begin
        effects.prepare_stand.side_effect = lambda: self.calls.append('prepare_stand')
        effects.tick.side_effect = lambda: self.calls.append('effects_tick')
        module = ModuleType(self.skill.__name__ + '.effects_session')
        @contextmanager
        def session(context, enabled):
            self.assertTrue(enabled)
            try:
                yield effects
            finally:
                self.calls.append('effects_restore')
        module.effects_session = session
        return module, effects

    def test_effects_start_only_after_song_started_and_restore_after_stop(self):
        self.context.skill_configs = {'diangetai': {'music_effects': True}}
        module, effects = self.effects()
        frames = iter((None, 1, 1))
        def update(player):
            player.started = next(frames)
            player.ended = 'effects_begin' in self.calls
            self.calls.append('frame')
        with patch.dict('sys.modules', {module.__name__: module}), \
                patch.object(self.stream.RemoteSong, 'update', update), patch.object(self.skill.time, 'sleep'):
            self.assertEqual(self.run_skill()['status'], 'success')
        self.assertEqual(self.calls, ['ready', 'prepare_stand', 'play', 'frame', 'frame', 'effects_begin',
                                     'effects_tick', 'frame', 'cleanup', 'effects_restore'])
        effects.begin_music.assert_called_once()

    def test_failed_buffer_never_starts_standing(self):
        from primebuddy.voice_support.robot_audio import AudioError
        self.context.skill_configs = {'diangetai': {'music_effects': True}}
        module, effects = self.effects()
        with patch.dict('sys.modules', {module.__name__: module}), \
                patch.object(self.stream.RemoteSong, 'wait_ready', side_effect=AudioError('缓冲失败')):
            self.assertEqual(self.run_skill()['status'], 'failed')
        effects.prepare_stand.assert_not_called()
        self.assertNotIn('play', self.calls)

    def test_unconfirmed_stand_never_starts_music(self):
        from primebuddy.voice_support.robot_audio import AudioError
        self.context.skill_configs = {'diangetai': {'music_effects': True}}
        module, effects = self.effects()
        effects.prepare_stand.side_effect = AudioError('起身未确认')
        with patch.dict('sys.modules', {module.__name__: module}):
            self.assertEqual(self.run_skill()['message'], '起身未确认')
        self.assertNotIn('play', self.calls)
        effects.begin_music.assert_not_called()
        self.assertEqual(self.calls[-2:], ['cleanup', 'effects_restore'])

    def test_failed_effects_still_stop_song_and_restore(self):
        from primebuddy.voice_support.robot_audio import AudioError
        self.context.skill_configs = {'diangetai': {'music_effects': True}}
        module, effects = self.effects()
        effects.begin_music.side_effect = AudioError('舞蹈请求未确认')
        def update(player):
            player.started = 1
        with patch.dict('sys.modules', {module.__name__: module}), \
                patch.object(self.stream.RemoteSong, 'update', update):
            result = self.run_skill()
        self.assertEqual(result, {'status': 'failed', 'message': '舞蹈请求未确认'})
        self.assertEqual(self.calls[-2:], ['cleanup', 'effects_restore'])

    def test_cancellation_during_music_cleans_song_and_effects(self):
        self.context.skill_configs = {'diangetai': {'music_effects': True}}
        module, effects = self.effects()
        effects.tick.side_effect = self.context.cancelled.set
        def update(player):
            player.started = 1
        with patch.dict('sys.modules', {module.__name__: module}), \
                patch.object(self.stream.RemoteSong, 'update', update), patch.object(self.skill.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, '取消'):
                self.run_skill()
        self.assertEqual(self.calls[-2:], ['cleanup', 'effects_restore'])
