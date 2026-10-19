"""独立命令的取消与恢复，不加载框架或操作真实机器人。"""
from contextlib import contextmanager, redirect_stdout
import io
import json
import os
from pathlib import Path
import runpy
import signal
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from helpers import load_entry

ROOT = Path(__file__).resolve().parents[1]
recovery = load_entry(ROOT, 'src/recovery.py')
media = load_entry(ROOT, 'src/robot/native_media.py')
support = load_entry(ROOT, 'src/support.py')


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runtime = Path(self.temp.name)

    def test_status_preserves_pending_records_and_live_command_ownership(self):
        self.assertTrue(recovery.status(self.runtime)['resources_released'])
        with recovery.exclusive(self.runtime, ('command.lock',)):
            self.assertTrue(recovery.status(self.runtime)['running'])
            self.assertFalse(recovery.status(self.runtime)['resources_released'])
        record = self.runtime / 'music_effects.json'
        record.write_text('private prior state')
        state = recovery.status(self.runtime)
        self.assertFalse(state['resources_released'])
        self.assertTrue(state['recovery_required'])
        self.assertNotIn('private prior state', json.dumps(state))

    def test_effects_recovery_is_not_automatically_replayed(self):
        record = self.runtime / 'music_effects.json'
        record.write_text('keep')
        with self.assertRaisesRegex(ValueError, '未自动执行运动'):
            recovery.recover(self.runtime)
        self.assertEqual(record.read_text(), 'keep')
        self.assertFalse(recovery.status(self.runtime)['resources_released'])

    def test_status_reads_records_after_acquiring_the_process_locks(self):
        exclusive = recovery.exclusive
        @contextmanager
        def write_before_lock(runtime):
            (runtime / 'media.json').write_text('record written by previous owner')
            with exclusive(runtime):
                yield
        with patch.object(recovery, 'exclusive', write_before_lock):
            result = recovery.status(self.runtime)
        self.assertFalse(result['resources_released'])
        self.assertTrue(result['recovery_required'])

    def player(self):
        p = media.NativeMedia.__new__(media.NativeMedia)
        p.trace_id, p.events, p.submitted = '', [], False
        p.record = self.runtime / 'media.json'
        p.stop_failed = False
        p.subscription = SimpleNamespace(get_publisher_count=lambda: 1)
        p.robot = Mock()
        p.stop_type = type('Stop', (), {'Request': staticmethod(lambda: type('Request', (), {})())})
        p.stop_client = object()
        return p

    def test_recover_only_stops_recorded_own_trace_and_removes_after_terminal(self):
        p = self.player()
        trace = 'diangetai_' + 'a' * 32
        support.write_json(p.record, {'trace_id': trace, 'audio_host': 'local'})
        def stop(client, request, cancellable):
            self.assertEqual(request.trace_id, trace)
            self.assertFalse(cancellable)
            self.assertTrue(p.record.exists())
            p.events.append((0, 3))
        p.call = stop
        with patch.object(media, 'audio_host', return_value='local'):
            p.recover()
        self.assertFalse(p.record.exists())
        self.assertFalse(p.submitted)

    def test_foreign_trace_or_host_is_never_stopped(self):
        p = self.player()
        p.call = Mock()
        for trace, host in [('other_trace', 'local'), ('diangetai_' + 'a' * 32, 'other-host')]:
            support.write_json(p.record, {'trace_id': trace, 'audio_host': host})
            with patch.object(media, 'audio_host', return_value='local'), self.assertRaises(ValueError):
                p.recover()
            p.call.assert_not_called()
            self.assertTrue(p.record.exists())

    def test_unconfirmed_stop_keeps_record_and_does_not_repeat_write_in_finally(self):
        p = self.player()
        support.write_json(p.record, {'trace_id': 'diangetai_' + 'a' * 32, 'audio_host': 'local'})
        p.call = Mock(side_effect=ValueError('stop result unknown'))
        with patch.object(media, 'audio_host', return_value='local'), self.assertRaises(ValueError):
            p.recover()
        with self.assertRaisesRegex(ValueError, '未重复提交'):
            p.stop()
        p.call.assert_called_once()
        self.assertTrue(p.record.exists())

    def test_new_recover_process_observes_previous_stop_without_repeating_rpc(self):
        p = self.player()
        trace = 'diangetai_' + 'a' * 32
        support.write_json(p.record, {'trace_id': trace, 'audio_host': 'local'})
        p.call = Mock(side_effect=ValueError('stop RPC result unknown'))
        with patch.object(media, 'audio_host', return_value='local'), self.assertRaises(ValueError):
            p.recover()
        self.assertTrue(support.read_json(p.record)['stop_requested'])
        new = self.player()
        new.call = Mock()
        new.robot.spin.side_effect = lambda **kwargs: new.on_status(SimpleNamespace(
            trace_id=trace, tts_status=SimpleNamespace(value=3)))
        with patch.object(media, 'audio_host', return_value='local'):
            new.recover()
        new.call.assert_not_called()
        self.assertFalse(new.record.exists())

    def test_play_journals_before_rpc_and_clears_only_after_real_end(self):
        p = self.player()
        p.play_type = SimpleNamespace(Request=lambda: SimpleNamespace(media_file_req=SimpleNamespace(
            priority_level=SimpleNamespace(value=0))))
        p.play_client = object()
        def accept(client, request):
            self.assertEqual(support.read_json(p.record)['trace_id'], request.media_file_req.trace_id)
            return SimpleNamespace(tts_resp=SimpleNamespace(is_success=True))
        p.call = accept
        with patch.object(media, 'audio_host', return_value='local'):
            p.start('/tmp/song.wav', 30)
        self.assertEqual(p.record.stat().st_mode & 0o777, 0o600)
        p.events = [(10, 2), (40, 3)]
        self.assertTrue(p.poll(now=40))
        self.assertFalse(p.record.exists())

    def test_play_rpc_timeout_preserves_trace_but_definite_rejection_removes_it(self):
        for outcome in ('timeout', 'rejected'):
            p = self.player()
            p.play_type = SimpleNamespace(Request=lambda: SimpleNamespace(media_file_req=SimpleNamespace(
                priority_level=SimpleNamespace(value=0))))
            p.play_client = object()
            if outcome == 'timeout':
                p.call = Mock(side_effect=ValueError('unknown'))
            else:
                p.call = Mock(return_value=SimpleNamespace(tts_resp=SimpleNamespace(is_success=False)))
            with patch.object(media, 'audio_host', return_value='local'), self.assertRaises(ValueError):
                p.start('/tmp/song.wav', 30)
            self.assertEqual(p.record.exists(), outcome == 'timeout')
            if p.record.exists():
                p.record.unlink()

    def test_check_play_rejects_pending_recovery_before_execution(self):
        namespace = runpy.run_path(str(ROOT / 'main.py'))
        (self.runtime / 'media.json').write_text('keep')
        output = io.StringIO()
        with redirect_stdout(output):
            code = namespace['main'](['--data-dir', str(self.runtime), '--runtime-dir', str(self.runtime),
                                      '--config', str(self.runtime / 'missing.yaml'),
                                      'check-play', '--artist', '测试歌手'])
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(output.getvalue())['resources_released'])


class CancellationTests(unittest.TestCase):
    def test_sigterm_runs_business_finally_and_reports_confirmed_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / 'cleaned'
            code = '''import runpy,sys,time
from pathlib import Path
ns=runpy.run_path(sys.argv[1])
import src.skill as skill
def play(*args,check,**kwargs):
 try:
  print('READY',file=sys.stderr,flush=True)
  while True:
   check()
   time.sleep(.02)
 finally:
  Path(sys.argv[3]).write_text('cleaned')
skill.play=play
raise SystemExit(ns['main'](['--data-dir',sys.argv[2],'play','--song','测试']))
'''
            process = subprocess.Popen([sys.executable, '-I', '-c', code, str(ROOT / 'main.py'), directory, str(marker)],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                       env=dict(os.environ, DIANGETAI_CONFIG=str(root / 'config.yaml')))
            try:
                self.assertEqual(process.stderr.readline().strip(), 'READY')
                process.send_signal(signal.SIGTERM)
                stdout, stderr = process.communicate(timeout=5)
                result = json.loads(stdout)
                self.assertTrue(result['cancelled'])
                self.assertTrue(result['resources_released'])
                self.assertEqual(marker.read_text(), 'cleaned')
                self.assertNotEqual(process.returncode, 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()


class MediaFailureFeedbackTests(unittest.TestCase):
    def test_error_feedback_requires_own_stop_and_confirmed_end(self):
        self.run_failure(4, terminal=3)
        self.run_failure(7, terminal=3)

    def test_error_after_stop_retains_recovery_and_does_not_repeat_stop(self):
        self.run_failure(4, terminal=4)
        self.run_failure(7, terminal=7)

    def test_only_symbolically_declared_stop_is_confirmed(self):
        self.run_failure(5, terminal=4, symbolic_stop=True)

    def test_q1_deployed_status_names_confirm_stop_but_not_error_or_not_in_queue(self):
        self.run_failure(5, terminal=4, q1=True)
        self.run_failure(7, terminal=4, q1=True)
        self.run_failure(5, terminal=5, q1=True)
        self.run_failure(7, terminal=7, q1=True)

    def run_failure(self, failure, terminal, symbolic_stop=False, q1=False):
        from types import ModuleType
        msg, srv = ModuleType('aimdk_msgs.msg'), ModuleType('aimdk_msgs.srv')
        enum = type('StatusEnum', (), {'STOP': 4, 'ERROR': 5, 'END': 3})
        if q1:
            enum = type('TtsStatusType', (), {
                'TTS_CONFIG_STATUS_TYPE_UNKNOWN': 0, 'TTS_STATUE_TYPE_END': 3,
                'TTS_STATUS_TYPE_BEGIN': 1, 'TTS_STATUS_TYPE_ERROR': 5,
                'TTS_STATUS_TYPE_INQUE': 6, 'TTS_STATUS_TYPE_NOT_INQUE': 7,
                'TTS_STATUS_TYPE_PLAYING': 2, 'TTS_STATUS_TYPE_STOP': 4})
        msg.TtsStatus = (lambda: SimpleNamespace(tts_status=enum())) if symbolic_stop or q1 else type('TtsStatus', (), {})
        srv.PlayMediaFile = type('PlayMediaFile', (), {'Request': staticmethod(lambda: SimpleNamespace(
            header=SimpleNamespace(header=SimpleNamespace(stamp=None)),
            media_file_req=SimpleNamespace(priority_level=SimpleNamespace(value=0))))})
        srv.StopTtsTraceId = type('StopTtsTraceId', (), {'Request': staticmethod(lambda: SimpleNamespace(
            header=SimpleNamespace(header=SimpleNamespace(stamp=None)), trace_id=''))})
        robot = Mock()
        robot.qos = object()
        robot.node.get_clock.return_value.now.return_value.to_msg.return_value = object()
        robot.node.create_client.side_effect = [Mock(), Mock()]
        robot.node.create_subscription.return_value = SimpleNamespace(get_publisher_count=lambda: 1)
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict('sys.modules', {'aimdk_msgs.msg': msg, 'aimdk_msgs.srv': srv}), \
                patch.object(media, 'audio_host', return_value='local'):
            p = media.NativeMedia(robot, Path(directory) / 'media.json')
            if q1:
                self.assertEqual(p.end_status, 3)
                self.assertEqual(p.confirmed_terminal, {3, 4})
            play_response = SimpleNamespace(header=SimpleNamespace(header=SimpleNamespace(code=0)),
                                            tts_resp=SimpleNamespace(is_success=True))
            stop_response = SimpleNamespace(header=SimpleNamespace(header=SimpleNamespace(code=0)))
            p.play_client.call_async.return_value = SimpleNamespace(done=lambda: True, result=lambda: play_response)
            p.stop_client.call_async.return_value = SimpleNamespace(done=lambda: True, result=lambda: stop_response)
            p.start('/tmp/test.wav', 30)
            callback = robot.node.create_subscription.call_args.args[2]
            callback(SimpleNamespace(trace_id=p.trace_id, tts_status=SimpleNamespace(value=2)))
            callback(SimpleNamespace(trace_id=p.trace_id, tts_status=SimpleNamespace(value=failure)))
            with self.assertRaisesRegex(ValueError, '播放失败'):
                p.poll()
            self.assertTrue(p.record.exists())
            self.assertTrue(p.submitted)
            # 外来 END 不能终止本 trace；只有停止后的自有终态才能清除记录。
            callback(SimpleNamespace(trace_id='unrelated', tts_status=SimpleNamespace(value=3)))
            robot.spin.side_effect = lambda **kwargs: callback(SimpleNamespace(
                trace_id=p.trace_id, tts_status=SimpleNamespace(value=terminal)))
            if terminal in p.confirmed_terminal:
                p.stop()
                self.assertFalse(p.record.exists())
            else:
                import itertools
                with patch.object(media.time, 'monotonic', side_effect=itertools.count()):
                    with self.assertRaisesRegex(ValueError, '停止未确认'):
                        p.stop()
                self.assertTrue(p.record.exists())
                with self.assertRaisesRegex(ValueError, '未重复提交'):
                    p.stop()
            p.stop_client.call_async.assert_called_once()
            self.assertEqual(p.stop_client.call_async.call_args.args[0].trace_id, p.trace_id)


class SubscriptionMatchingTests(unittest.TestCase):
    def player(self, count):
        p = media.NativeMedia.__new__(media.NativeMedia)
        p.record, p.submitted, p.events = None, False, []
        p.subscription = SimpleNamespace(get_publisher_count=count)
        p.robot = Mock()
        p.play_type = SimpleNamespace(Request=lambda: SimpleNamespace(media_file_req=SimpleNamespace(
            priority_level=SimpleNamespace(value=0))))
        p.play_client = object()
        p.call = Mock(return_value=SimpleNamespace(tts_resp=SimpleNamespace(is_success=True)))
        return p

    def test_waits_for_subscription_match_before_play_rpc(self):
        count = Mock(side_effect=[0, 0, 1])
        p = self.player(count)
        p.start('/tmp/song.wav', 30)
        self.assertEqual(count.call_count, 3)
        self.assertEqual(p.robot.spin.call_count, 2)
        p.call.assert_called_once()

    def test_unmatched_or_cancelled_subscription_never_submits_play(self):
        import itertools
        p = self.player(lambda: 0)
        with patch.object(media.time, 'monotonic', side_effect=itertools.count()), \
                self.assertRaisesRegex(ValueError, '尚未匹配'):
            p.start('/tmp/song.wav', 30)
        p.call.assert_not_called()
        self.assertFalse(p.submitted)
        p = self.player(lambda: 0)
        p.robot.spin.side_effect = support.Cancelled('cancelled')
        with self.assertRaises(support.Cancelled):
            p.start('/tmp/song.wav', 30)
        p.call.assert_not_called()

    def test_humble_uses_public_rcl_subscription_pointer_with_typed_readonly_call(self):
        import ctypes
        class Handle:
            pointer = 1234
            def __enter__(self): return self
            def __exit__(self, *args): pass
        sub = SimpleNamespace(handle=Handle())
        function = Mock()
        def read_count(pointer, output):
            self.assertEqual(pointer, 1234)
            output._obj.value = 2
            return 0
        function.side_effect = read_count
        library = SimpleNamespace(rcl_subscription_get_publisher_count=function)
        with patch.object(ctypes, 'CDLL', return_value=library) as load:
            self.assertEqual(media.matched_publishers(sub), 2)
        load.assert_called_once_with('librcl.so')
        self.assertEqual(function.argtypes, [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)])
        self.assertEqual(function.restype, ctypes.c_int32)

    def test_humble_count_failure_does_not_guess_from_graph_count(self):
        import ctypes
        class Handle:
            pointer = 1234
            def __enter__(self): return self
            def __exit__(self, *args): pass
        sub = SimpleNamespace(handle=Handle())
        library = SimpleNamespace(rcl_subscription_get_publisher_count=Mock(return_value=1))
        with patch.object(ctypes, 'CDLL', return_value=library), \
                self.assertRaisesRegex(ValueError, '无法确认'):
            media.matched_publishers(sub)
