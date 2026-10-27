"""前台应用的交接、清理与恢复；ROS、原厂 Agent 和动作均为假对象。"""
from contextlib import contextmanager
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from chengyu_game import runtime
from chengyu_game.support import AudioError, Cancelled, SetupError, exclusive, write_json


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data, self.directory = self.root / 'data', self.root / 'runtime'
        self.data.mkdir()
        self.directory.mkdir()
        self.session_record = self.directory / 'session.json'
        self.trace = []
        self.unknown = None
        self.frontend_held = False
        self.checkpoint = Mock()
        self.result = {'status': 'success', 'message': '整局已结束。'}

        def cleanup(name):
            self.assertTrue(self.session_record.exists(), '全部清理完成前不能删除会话记录')
            self.assertTrue(runtime.status(self.directory)['running'])
            self.trace.append(name + '.close')
            if name == self.unknown:
                raise AudioError(name + ' 释放未确认')
            if name in ('media', 'effects'):
                (self.directory / (name + '.json')).unlink(missing_ok=True)

        self.robot = SimpleNamespace(close=Mock(side_effect=lambda: cleanup('robot')))
        self.media = SimpleNamespace(close=Mock(side_effect=lambda: cleanup('media')), recover=Mock())
        self.effects = SimpleNamespace(start=Mock(), close=Mock(side_effect=lambda: cleanup('effects')), recover=Mock(),
                                       prepare_for_game=Mock(return_value={'status': 'disabled'}))
        self.voice = SimpleNamespace(start=Mock(), close=Mock(side_effect=lambda: cleanup('voice')))
        self.session = SimpleNamespace(run=Mock(side_effect=self.run_session), say=Mock(), start_notice='')

        def robot_factory(directory, checkpoint):
            self.robot.checkpoint = checkpoint
            return self.robot

        @contextmanager
        def frontend():
            self.assertFalse(self.frontend_held)
            self.frontend_held = True
            self.trace.append('frontend.acquire')
            try:
                yield
            finally:
                self.trace.append('frontend.release')
                self.frontend_held = False

        @contextmanager
        def agent(directory):
            self.assertTrue(self.frontend_held)
            self.trace.append('agent.acquire')
            write_json(directory / 'agent_mode.json', {'fixture': True})
            try:
                yield
            finally:
                self.assertTrue(self.frontend_held)
                self.assertTrue(self.session_record.exists())
                self.trace.append('agent.restore')
                (directory / 'agent_mode.json').unlink()

        def install(name, **options):
            patcher = patch(name, **options)
            value = patcher.start()
            self.addCleanup(patcher.stop)
            return value

        self.preflight = install('chengyu_game.runtime.check_play', return_value={'status': 'success'})
        install('chengyu_game.runtime.Models', return_value=Mock())
        self.session_factory = install('chengyu_game.runtime.Session', return_value=self.session)
        install('chengyu_game.robot.device.detect_device', return_value=SimpleNamespace(series='T1', sku='T1-2X'))
        self.parent = install('chengyu_game.runtime.os.getppid', return_value=42)
        self.robot_factory = install('chengyu_game.robot.transport.Robot', side_effect=robot_factory)
        self.media_factory = install('chengyu_game.robot.native_media.NativeMedia', return_value=self.media)
        install('chengyu_game.robot.celebration.Celebration', return_value=self.effects)
        install('chengyu_game.voice.VoiceInput', return_value=self.voice)
        self.agent_mode = install('chengyu_game.robot.native_agent.read_agent_mode', return_value='only_voice')
        install('chengyu_game.robot.native_agent.agent_mode', side_effect=AssertionError('hosted cannot use subprocess mode RPC'))
        self.agent = install('chengyu_game.robot.native_agent.voice_only_agent', side_effect=agent)
        self.frontend = install('chengyu_game.runtime.frontend_lock', side_effect=frontend)

    def run_session(self):
        self.trace.append('session.run')
        self.assertTrue(self.session_record.exists())
        self.assertTrue(runtime.status(self.directory)['running'])
        write_json(self.directory / 'media.json', {'fixture': 'playing'})
        write_json(self.directory / 'effects.json', {'fixture': 'celebrating'})
        return self.result

    def play(self, **options):
        return runtime.play({}, self.data, self.directory, self.checkpoint, **options)

    def test_hosted_uses_only_read_only_agent_check_and_waits_for_all_cleanup(self):
        self.assertEqual(self.play(hosted=True), self.result)
        self.agent_mode.assert_called_once_with(self.robot)
        self.agent.assert_not_called()
        self.frontend.assert_not_called()
        self.assertEqual(self.trace, ['session.run', 'voice.close', 'effects.close', 'media.close', 'robot.close'])
        self.assertFalse(self.session_record.exists())
        self.assertTrue(runtime.status(self.directory)['resources_released'])

    def test_unavailable_motion_gives_child_friendly_notice_and_still_runs_voice_game(self):
        self.effects.prepare_for_game.return_value = {'status': 'unavailable', 'reason': '尚未验收'}
        self.assertEqual(self.play(hosted=True, motion_enabled=True), self.result)
        self.assertIs(self.effects.prepare_for_game.call_args.kwargs['motion_enabled'], True)
        self.assertIn('先玩语音', self.session.start_notice)

    def test_game_start_plays_stand_notice_before_posture_preparation_finishes(self):
        self.session.say.side_effect = lambda text: self.trace.append('notice.finished')
        def prepare(*, motion_enabled, before_stand):
            self.assertTrue(motion_enabled)
            before_stand()
            self.trace.append('stand.confirmed')
            return {'status': 'ready'}
        self.effects.prepare_for_game.side_effect = prepare
        self.play(hosted=True, motion_enabled=True)
        self.session.say.assert_called_once_with('请确认四周安全，我要站起来了。')
        self.assertEqual(self.trace[:3], ['notice.finished', 'stand.confirmed', 'session.run'])

    def test_speech_is_passed_to_session_with_fixed_motion_notice_and_single_cache(self):
        self.effects.prepare_for_game.return_value = {'status': 'unavailable', 'reason': '尚未验收'}
        speech = {'correct': '接对啦！我接{next_idiom}。'}
        result = runtime.play({'speech': speech}, self.data, self.directory, self.checkpoint, hosted=True)
        self.assertEqual(result, self.result)
        self.assertEqual(self.session.start_notice, '动作暂时用不了，我们先玩语音接龙吧。')
        self.assertEqual(self.session_factory.call_args.kwargs['speech'], speech)
        self.assertEqual(self.session_factory.call_args.args[-1], self.data / 'ending.wav')

    def test_unknown_posture_preparation_never_starts_game_or_discards_its_record(self):
        def unknown(**kwargs):
            write_json(self.directory / 'effects.json', {'fixture': 'unknown'})
            raise AudioError('站立结果未确认')
        self.effects.prepare_for_game.side_effect = unknown
        self.unknown = 'effects'
        with self.assertRaisesRegex(AudioError, '释放未确认'):
            self.play(hosted=True, motion_enabled=True)
        self.session.run.assert_not_called()
        self.assertFalse(runtime.status(self.directory)['resources_released'])

    def test_hosted_does_not_change_an_unprepared_agent_mode(self):
        self.agent_mode.return_value = 'normal'
        with self.assertRaisesRegex(AudioError, 'only_voice'):
            self.play(hosted=True)
        self.agent_mode.assert_called_once_with(self.robot)
        self.agent.assert_not_called()
        self.assertEqual(self.trace, ['robot.close'])
        self.media_factory.assert_not_called()
        self.session_factory.assert_not_called()
        self.assertFalse(self.session_record.exists())

    def test_failed_hosted_mode_read_closes_robot_without_starting_session(self):
        self.agent_mode.side_effect = AudioError('只读模式查询失败')
        with self.assertRaisesRegex(AudioError, '查询失败'):
            self.play(hosted=True)
        self.agent.assert_not_called()
        self.frontend.assert_not_called()
        self.assertEqual(self.trace, ['robot.close'])
        self.media_factory.assert_not_called()
        self.session_factory.assert_not_called()
        self.assertFalse(self.session_record.exists())

    def test_unknown_media_or_effects_cleanup_preserves_records_and_runs_remaining_cleanup(self):
        for unknown in ('media', 'effects'):
            with self.subTest(unknown=unknown):
                self.unknown = unknown
                self.trace.clear()
                with self.assertRaisesRegex(AudioError, '释放未确认'):
                    self.play(hosted=True)
                self.assertEqual(self.trace[-4:], ['voice.close', 'effects.close', 'media.close', 'robot.close'])
                self.assertTrue(self.session_record.exists())
                self.assertTrue((self.directory / (unknown + '.json')).exists())
                result = runtime.status(self.directory)
                self.assertFalse(result['resources_released'])
                self.assertFalse(result['running'])
                self.assertIn('session.json', result['pending'])
                self.assertIn(unknown + '.json', result['pending'])
                # 清理的仅为本测试 fixture，下一子用例使用全新状态。
                self.session_record.unlink()
                (self.directory / (unknown + '.json')).unlink()

    def test_hosted_parent_exit_cancels_session_and_finishes_cleanup(self):
        def run():
            self.run_session()
            self.parent.return_value = 1
            self.robot.checkpoint()
        self.session.run.side_effect = run
        with self.assertRaisesRegex(Cancelled, '宿主已退出'):
            self.play(hosted=True)
        self.checkpoint.assert_called_once()
        self.assertEqual(self.trace[-4:], ['voice.close', 'effects.close', 'media.close', 'robot.close'])
        self.assertTrue(runtime.status(self.directory)['resources_released'])

    def test_hosted_requires_a_live_parent_before_record_or_device_creation(self):
        self.parent.return_value = 1
        with self.assertRaisesRegex(AudioError, '宿主进程'):
            self.play(hosted=True)
        self.assertFalse(self.session_record.exists())
        self.robot_factory.assert_not_called()

    def test_standalone_requires_explicit_management_and_rejects_mixed_ownership(self):
        for options in ({}, {'hosted': True, 'manage_agent': True}):
            with self.subTest(options=options), self.assertRaises(AudioError):
                self.play(**options)
        self.preflight.assert_not_called()
        self.agent.assert_not_called()
        self.frontend.assert_not_called()

    def test_standalone_holds_shared_lock_through_agent_restoration_and_cleanup(self):
        self.assertEqual(self.play(manage_agent=True), self.result)
        self.frontend.assert_called_once_with()
        self.agent.assert_called_once_with(self.directory)
        self.agent_mode.assert_not_called()
        self.assertEqual(self.trace, ['frontend.acquire', 'agent.acquire', 'session.run',
            'voice.close', 'effects.close', 'media.close', 'robot.close', 'agent.restore', 'frontend.release'])
        self.assertTrue(runtime.status(self.directory)['resources_released'])

    def test_status_does_not_release_a_running_application_lock(self):
        with exclusive(self.directory):
            result = runtime.status(self.directory)
            self.assertFalse(result['resources_released'])
            self.assertTrue(result['running'])
            self.assertEqual(result['pending'], [])
            with self.assertRaises(SetupError):
                with exclusive(self.directory):
                    self.fail('status 不得释放另一个调用持有的锁')
        self.assertTrue(runtime.status(self.directory)['resources_released'])

    def test_recover_clears_only_stale_session_without_accessing_hardware(self):
        write_json(self.session_record, {'fixture': 'stale'})
        result = runtime.recover(self.directory, self.root / 'unused-config.yaml')
        self.assertTrue(result['resources_released'])
        self.assertFalse(self.session_record.exists())
        self.robot_factory.assert_not_called()
        self.agent.assert_not_called()
        self.agent_mode.assert_not_called()
        self.frontend.assert_not_called()

    def test_recover_refuses_a_live_session_and_preserves_its_record(self):
        write_json(self.session_record, {'fixture': 'live'})
        with exclusive(self.directory), self.assertRaises(SetupError):
            runtime.recover(self.directory, self.root / 'unused-config.yaml')
        self.assertTrue(self.session_record.exists())
        self.robot_factory.assert_not_called()

    def test_recover_does_not_clear_session_when_media_release_remains_unknown(self):
        write_json(self.session_record, {'fixture': 'stale'})
        write_json(self.directory / 'media.json', {'fixture': 'unknown'})
        self.unknown = 'media'
        self.media.recover.side_effect = AudioError('媒体恢复未确认')
        with self.assertRaises(AudioError):
            runtime.recover(self.directory, self.root / 'unused-config.yaml')
        self.assertTrue(self.session_record.exists())
        self.assertTrue((self.directory / 'media.json').exists())
        self.assertFalse(runtime.status(self.directory)['resources_released'])
        self.robot.close.assert_called_once()

    def test_recover_agent_restoration_requires_shared_frontend_lock(self):
        target = {'series': 'T1', 'sku': 'T1-2X', 'host': 'fixture'}
        write_json(self.session_record, {'fixture': 'stale'})
        write_json(self.directory / 'agent_mode.json', {'purpose': 'chengyu_session', 'target': target,
                   'previous_running': True, 'previous_mode': 'normal', 'backend': 'http'})
        def restore(directory, previous, backend):
            self.assertTrue(self.frontend_held)
            self.assertTrue(self.session_record.exists())
            (directory / 'agent_mode.json').unlink()
        with patch('chengyu_game.robot.native_agent.agent_target', return_value=target), \
                patch('chengyu_game.robot.native_agent.agent_backend', return_value='http'), \
                patch('chengyu_game.robot.native_agent.restore_agent', side_effect=restore) as restoration:
            result = runtime.recover(self.directory, self.root / 'unused-config.yaml')
        restoration.assert_called_once_with(self.directory, 'normal', 'http')
        self.frontend.assert_called_once_with()
        self.assertTrue(result['resources_released'])
        self.assertFalse(self.session_record.exists())


if __name__ == '__main__':
    unittest.main()
