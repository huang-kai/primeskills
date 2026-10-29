"""网络截止时间与硬件提交前取消屏障；只用假线程、时钟和客户端。"""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from contextlib import contextmanager
import tempfile


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from chengyu_game.robot.native_media import NativeMedia
from chengyu_game.robot.transport import Robot
from chengyu_game.session import Session, InactivityExpired
from chengyu_game.simulation import synthetic_wav
from chengyu_game.support import AudioError, PlaybackInterrupted, Cancelled, read_json, write_json


class LifecycleTests(unittest.TestCase):
    def test_short_playback_is_recoverable_only_with_confirmed_stop(self):
        for terminal in (3, 4, 5, 7):
            media = NativeMedia.__new__(NativeMedia)
            media.events = [(10.0, 2), (10.509, terminal)]
            media.duration, media.requested_at = 9.84, 9.9
            media.submitted = True
            media.confirmed_terminal = {3, 4}
            media.end_status = 3
            media.trace_id = 'chengyu_fixture'
            with self.subTest(terminal=terminal), tempfile.TemporaryDirectory() as directory:
                media.record = Path(directory) / 'media.json'
                write_json(media.record, {'trace_id': media.trace_id})
                with self.assertRaises(AudioError) as raised:
                    media.poll(now=11)
                self.assertEqual(isinstance(raised.exception, PlaybackInterrupted), terminal in (3, 4))
                self.assertEqual(media.submitted, terminal not in (3, 4))
                self.assertEqual(media.record.exists(), terminal not in (3, 4))

    def test_missing_play_start_is_not_treated_as_recoverable_interruption(self):
        media = NativeMedia.__new__(NativeMedia)
        media.events, media.duration, media.requested_at = [(10.509, 3)], 9.84, 9.9
        media.submitted = True
        with self.assertRaises(AudioError) as raised:
            media.poll(now=11)
        self.assertNotIsInstance(raised.exception, PlaybackInterrupted)

    def test_idle_expiry_while_waiting_for_media_service_never_submits_or_keeps_record(self):
        now = [119.0]
        media = NativeMedia.__new__(NativeMedia)
        response = SimpleNamespace(header=SimpleNamespace(header=SimpleNamespace(code=0)),
                                   tts_resp=SimpleNamespace(is_success=True))
        future = SimpleNamespace(done=lambda: True, result=lambda: response)
        media.play_client = SimpleNamespace(
            service_is_ready=Mock(side_effect=[False, True]), call_async=Mock(return_value=future))
        media.stop_client = Mock()
        media.robot = SimpleNamespace(
            checkpoint=Mock(), spin=lambda **kwargs: now.__setitem__(0, 121),
            node=SimpleNamespace(get_clock=lambda: SimpleNamespace(
                now=lambda: SimpleNamespace(to_msg=lambda: 'stamp'))))
        media.play_type = SimpleNamespace(Request=lambda: SimpleNamespace(
            header=SimpleNamespace(header=SimpleNamespace(stamp=None)),
            media_file_req=SimpleNamespace(priority_level=SimpleNamespace(value=0))))
        media.submitted, media.events = False, []
        media.wait_feedback = Mock()
        @contextmanager
        def transferred(path, checkpoint=None):
            yield Path('/tmp/fixture/audio.wav')
        def completed():
            media.submitted = False
            media.clear_record()
            return True
        media.robot.prepared_audio, media.poll = transferred, completed
        session = Session(Mock(), media.robot, Mock(), media, Mock(), None, clock=lambda: now[0])
        session.inactivity_deadline = 120
        with tempfile.TemporaryDirectory() as directory:
            media.record = Path(directory) / 'media.json'
            with patch('chengyu_game.robot.native_media.audio_host', return_value='fixture'), \
                    self.assertRaises(InactivityExpired):
                media.play(synthetic_wav(), before_start=session.check_inactivity)
            self.assertFalse(media.submitted)
            self.assertFalse(media.record.exists())
            media.stop()
        media.play_client.call_async.assert_not_called()
        media.stop_client.call_async.assert_not_called()

    def test_idle_expiry_during_audio_transfer_prevents_new_playback_and_cleans_file(self):
        now, cleaned = [119.0], []
        @contextmanager
        def transferred(path, checkpoint=None):
            self.assertTrue(path.is_file())
            now[0] = 121
            try:
                yield Path('/tmp/fixture/audio.wav')
            finally:
                cleaned.append(True)
        robot = SimpleNamespace(prepared_audio=transferred, checkpoint=Mock())
        media = NativeMedia.__new__(NativeMedia)
        media.robot, media.start, media.stop = robot, Mock(), Mock()
        session = Session(SimpleNamespace(complete=Mock()), robot, Mock(), media, Mock(), None,
                          clock=lambda: now[0])
        session.inactivity_deadline = 120
        with self.assertRaises(InactivityExpired):
            media.play(synthetic_wav(), before_start=session.check_inactivity)
        media.start.assert_not_called()
        media.stop.assert_called_once_with()
        self.assertEqual(cleaned, [True])

    def model_work(self, complete_at):
        clock, pending = [0.0], []
        class PendingThread:
            def __init__(self, target, **kwargs):
                self.target = target

            def start(self):
                pending.append(self.target)

        def spin():
            clock[0] = complete_at
            pending.pop(0)()

        robot = SimpleNamespace(spin=spin, checkpoint=Mock())
        session = Session(SimpleNamespace(complete=Mock()), robot, Mock(), Mock(), Mock(), None,
                          clock=lambda: clock[0], operation_timeout=95)
        return session, patch('chengyu_game.session.threading.Thread', PendingThread)

    def test_result_arriving_during_spin_after_deadline_is_rejected(self):
        session, thread = self.model_work(96)
        with thread, self.assertRaisesRegex(AudioError, '超时'):
            session.work(lambda: '迟到的下一成语')
        session.media.play.assert_not_called()
        session.effects.celebrate.assert_not_called()

    def test_result_before_deadline_is_returned(self):
        session, thread = self.model_work(94)
        with thread:
            self.assertEqual(session.work(lambda: '及时结果'), '及时结果')

    def rpc(self, kind, cancelled):
        robot = object.__new__(Robot)
        def checkpoint():
            if cancelled[0]:
                raise Cancelled('已取消')
        robot.checkpoint = Mock(side_effect=checkpoint)
        robot.spin = Mock(side_effect=lambda **kwargs: checkpoint() if kwargs.get('cancellable', True) else None)
        robot.node = SimpleNamespace(get_clock=lambda: SimpleNamespace(
            now=lambda: SimpleNamespace(to_msg=lambda: 'stamp')))
        response = SimpleNamespace(header=SimpleNamespace(
            header=SimpleNamespace(code=0), status=SimpleNamespace(value=1, reason=0)))
        future = SimpleNamespace(done=lambda: True, result=lambda: response)
        client = SimpleNamespace(service_is_ready=Mock(return_value=True), call_async=Mock(return_value=future))
        request = SimpleNamespace(header=SimpleNamespace(header=SimpleNamespace(stamp=None)))
        if kind == 'media':
            media = object.__new__(NativeMedia)
            media.robot = robot
            call = media.call
        else:
            call = robot.call
        return call, client, request, robot, response

    def media_for_start(self, record, cancelled=False):
        call, client, _, robot, _ = self.rpc('media', [cancelled])
        media = call.__self__
        media.play_client, media.stop_client = client, Mock()
        media.play_type = SimpleNamespace(Request=lambda: SimpleNamespace(
            header=SimpleNamespace(header=SimpleNamespace(stamp=None)),
            media_file_req=SimpleNamespace(priority_level=SimpleNamespace(value=0))))
        media.submitted, media.events, media.record = False, [], record
        media.wait_feedback = Mock()
        return media

    def test_known_pre_submit_failure_leaves_no_trace_or_stop_request(self):
        for cause in ('cancel', 'service_unavailable'):
            with self.subTest(cause=cause), tempfile.TemporaryDirectory() as directory:
                media = self.media_for_start(Path(directory) / 'media.json', cancelled=cause == 'cancel')
                if cause == 'service_unavailable':
                    media.play_client.service_is_ready.return_value = False
                with patch('chengyu_game.robot.native_media.time.monotonic', side_effect=[0, 13]), \
                        self.assertRaises(Cancelled if cause == 'cancel' else AudioError):
                    media.start('/tmp/fixture/audio.wav', 1, before_submit=lambda: None)
                media.play_client.call_async.assert_not_called()
                self.assertFalse(media.submitted)
                self.assertFalse(media.record.exists())
                media.stop()
                media.stop_client.call_async.assert_not_called()

    def test_exception_after_entering_call_async_keeps_exact_trace_record(self):
        with tempfile.TemporaryDirectory() as directory:
            media = self.media_for_start(Path(directory) / 'media.json')
            media.play_client.call_async.side_effect = OSError('提交结果未知')
            with patch('chengyu_game.robot.native_media.audio_host', return_value='fixture'), \
                    self.assertRaises(OSError):
                media.start('/tmp/fixture/audio.wav', 1, before_submit=lambda: None)
            media.play_client.call_async.assert_called_once()
            self.assertTrue(media.submitted)
            trace = media.play_client.call_async.call_args.args[0].media_file_req.trace_id
            self.assertEqual(read_json(media.record)['trace_id'], trace)
            self.assertEqual(trace, media.trace_id)

    def test_cancelled_ready_service_never_receives_new_hardware_request(self):
        for kind in ('media', 'robot'):
            for when in ('before', 'during_readiness'):
                cancelled = [when == 'before']
                call, client, request, robot, _ = self.rpc(kind, cancelled)
                if when == 'during_readiness':
                    def ready():
                        cancelled[0] = True
                        return True
                    client.service_is_ready.side_effect = ready
                with self.subTest(kind=kind, cancelled=when), self.assertRaises(Cancelled):
                    call(client, request)
                client.call_async.assert_not_called()

    def test_cleanup_rpc_remains_available_after_cancellation(self):
        for kind in ('media', 'robot'):
            call, client, request, robot, response = self.rpc(kind, [True])
            with self.subTest(kind=kind):
                self.assertIs(call(client, request, cancellable=False), response)
            client.call_async.assert_called_once_with(request)
            robot.checkpoint.assert_not_called()


if __name__ == '__main__':
    unittest.main()
