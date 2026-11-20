import io
import json
import os
from pathlib import Path
from types import ModuleType
import importlib.util
import yaml
import sys
import tarfile
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src/jetson')]
import main as cli
from fall_preview import mirror_preview, screen_frame
from family_confirmation import FamilyConfirmation
from graphical_voice import GraphicalVoice, owner
from floor_detection import FloorTest
from settings import parse, imported_yaml, read_private, write_private
from sms_sender import RobotNotifier, sender_for
from datetime import datetime, timezone
from posture import StandingView


class PreviewTests(unittest.TestCase):
    def test_empty_scene_has_only_camera_pixels(self):
        image = np.random.default_rng(7).integers(0, 256, (100, 120, 3), dtype=np.uint8)
        np.testing.assert_array_equal(mirror_preview(image, [], []), cv2.flip(image, 1))

    def test_current_person_box_changes_from_green_to_red_in_both_poses(self):
        image = np.zeros((100, 120, 3), np.uint8)
        box = [10, 30, 28, 50]
        for pose in ('biped', 'quadruped'):
            for fallen, color in [([], (0, 255, 80)), ([box], (30, 50, 255))]:
                frame = screen_frame(image, [(box, .9)], fallen, pose, None, 0, 1, 10.)
                if pose == 'quadruped':
                    frame = cv2.rotate(frame, cv2.ROTATE_180)
                self.assertTrue(np.any(np.all(frame[90:190, 295:400] == color, axis=2)))
                self.assertFalse(frame[110:210, 65:260].any())

    def test_change_of_pose_resets_tracking_and_epoch(self):
        track = FloorTest()
        robot = StandingView('biped', True, track)
        msg = Mock(is_moving=False, is_fallen=False)
        msg.lift_state.is_lifted = False
        msg.action_info.status.value = 100
        msg.fsm_state.current_state = 2
        for pose, action, start in [(2, 'BIPED_STAND_DEFAULT', 10.), (1, 'QUADRUPED_STAND_DEFAULT', 11.)]:
            msg.form_state.current_form = pose
            msg.action_info.action_desc = action
            track.previous = ['old']
            for delta in (0., .3, .6):
                robot.update(msg, start + delta)
            self.assertEqual(track.previous, [])
            self.assertEqual(robot.stable_since, start)
            self.assertTrue(robot.stable(start + .61))
            self.assertFalse(robot.stable(start + 1.2))

    def test_configurable_hold_applies_on_full_frame(self):
        track = FloorTest(3.)
        box = [2, 1, 30, 8]
        for i in range(30):
            self.assertEqual(track.update(i / 10, [box]), [])
        self.assertEqual(track.update(3., [box]), [box])


class ConfigTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.file = self.directory / 'config.yaml'
        self.template = (ROOT / 'cfg/config.example.yaml').read_text()
        self.sms = dict(phone='13800000000', sign_name='example', template_code='SMS_123',
                        time_variable='when', access_key_id='test-id', access_key_secret='test-secret')
        self.config = imported_yaml(json.dumps(self.sms), self.template)

    def test_migration_and_private_yaml(self):
        write_private(self.file, self.config)
        self.assertEqual(os.stat(self.file).st_mode & 0o777, 0o600)
        cfg = parse(read_private(self.file))
        self.assertEqual(cfg['sms']['provider'], 'aliyun')
        self.assertEqual(cfg['contact']['phone'], self.sms['phone'])
        self.assertEqual(cfg['sms']['aliyun']['time_variable'], 'when')
        self.assertEqual(cfg['voice']['response_seconds'], 30)
        self.assertEqual(cfg['detection']['hold_seconds'], 2)
        self.file.chmod(0o644)
        with self.assertRaises(ValueError):
            read_private(self.file)

    def test_import_never_overwrites_customized_cfg(self):
        write_private(self.file, self.config)
        with patch.object(cli, 'CONFIG', self.file), patch.object(cli, 'BASE', self.directory / 'runtime'), patch.object(cli, 'command') as run:
            with self.assertRaises(ValueError):
                cli.install(import_legacy=True)
        run.assert_not_called()
        self.assertEqual(self.file.read_text(), self.config)

    def test_invalid_yaml_is_rejected_without_echoing_secrets(self):
        for invalid in ("sensitive-example: [bad", self.template,
                        self.config.replace("hold_seconds: 2.0", "hold_seconds: false")):
            with self.assertRaises(ValueError) as error:
                parse(invalid)
            self.assertNotIn('sensitive-example', str(error.exception))

    def test_only_display_code_crosses_the_board_link(self):
        write_private(self.file, self.config)
        with patch.object(cli, 'CONFIG', self.file), patch.object(cli, 'BASE', self.directory / 'runtime'), patch.object(cli, 'command') as run:
            cli.install()
        probe, deploy = run.call_args_list
        self.assertIn(str(self.file), probe.args[0])
        self.assertNotIn('ssh', probe.args[0])
        self.assertNotIn('input', probe.kwargs)
        self.assertIn('run@10.1.1.100', deploy.args[0])
        with tarfile.open(fileobj=io.BytesIO(deploy.kwargs['input'])) as bundle:
            self.assertEqual(set(bundle.getnames()), {'watch_display.py', 'receive.py'})
        self.assertNotIn('test-secret', str(deploy.args))

    def test_install_accepts_pristine_template_without_contact(self):
        self.file.write_text(self.template)
        with patch.object(cli, 'CONFIG', self.file), patch.object(cli, 'BASE', self.directory / 'runtime'), patch.object(cli, 'command') as run:
            result = cli.install()
        self.assertIn('cfg/config.yaml', result)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(self.file.read_text(), self.template)
        self.assertEqual(self.file.stat().st_mode & 0o777, 0o600)

    def test_gateway_install_has_no_aliyun_dependency_or_notification(self):
        spec = importlib.util.spec_from_file_location('install_runtime', ROOT / 'scripts/install_runtime.py')
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        cfg = yaml.safe_load(self.template)
        cfg['contact']['phone'] = self.sms['phone']
        cfg['sms'].update(provider='https_gateway', https_gateway={'url': 'https://example.org/sms'})
        write_private(self.file, yaml.safe_dump(cfg))
        mocks = {name: Mock() for name in ('rclpy', 'tensorrt', 'aimdk_msgs', 'aimdk_msgs.srv')}
        with patch.dict(sys.modules, mocks), patch('ctypes.CDLL'), patch('subprocess.run') as pip, \
                patch('sms_sender.HTTPSConnection') as gateway:
            installer.prepare(self.file)
        pip.assert_not_called()
        gateway.assert_not_called()

    def test_first_case_creates_private_ledger_without_legacy_install(self):
        private = self.directory / 'fresh-skill/private'
        with patch('sms_sender.CONFIG_PATH', private / 'sms.json'), patch('sms_sender.sender_for', return_value=(Mock(), Mock())):
            original_umask = os.umask(0o077)
            try:
                notifier = RobotNotifier.from_config(case_once=True, settings={})
                notifier.close()
            finally:
                os.umask(original_umask)
        self.assertEqual(len(list((private / 'cases').glob('*.sqlite'))), 1)
        self.assertEqual(private.stat().st_mode & 0o777, 0o700)

    def test_both_install_commands_reach_same_entry(self):
        for action in ('install', 'configure'):
            with self.subTest(action=action), patch.object(cli, 'check_board'), \
                    patch.object(cli, 'install', return_value='ok') as install, \
                    patch.object(sys, 'argv', ['main.py', action]), patch('builtins.print'):
                self.assertEqual(cli.main(), 0)
                install.assert_called_once_with(False)

    def test_gateway_payload_acceptance_and_ambiguous_responses(self):
        settings = yaml.safe_load(self.template)
        settings['contact']['phone'] = self.sms['phone']
        settings['sms'].update(provider='https_gateway',
                               https_gateway={'url': 'https://gateway.example:9443/sms', 'bearer_token': 'test'})
        config = parse(yaml.safe_dump(settings))
        send, probe = sender_for(config)
        self.assertEqual(probe.args, ('gateway.example', 9443))
        with patch('sms_sender.HTTPSConnection') as connection:
            response = connection.return_value.getresponse.return_value
            response.status = 202
            response.read.return_value = b'{"accepted":true,"id":"gw-id"}'
            now = datetime.now(timezone.utc)
            self.assertEqual(send('a'*32, now), ('OK', 'gw-id'))
            method, path, data, headers = connection.return_value.request.call_args.args
            self.assertEqual((method, path), ('POST', '/sms'))
            self.assertEqual(headers['Authorization'], 'Bearer test')
            self.assertEqual(json.loads(data)['event_id'], 'a'*32)
            self.assertEqual(json.loads(data)['phone'], self.sms['phone'])
            response.read.return_value = b'{"accepted":false}'
            self.assertEqual(send('b'*32, now), ('GATEWAY_REJECTED', ''))
            response.status = 302
            with self.assertRaises(ValueError):
                send('c'*32, now)
            self.assertEqual(connection.return_value.request.call_count, 3)
        settings['sms']['https_gateway']['url'] = 'http://gateway.example/sms'
        with self.assertRaises(ValueError):
            parse(yaml.safe_dump(settings))


class DetectorTests(unittest.TestCase):
    def test_full_frame_tensor_decoding_with_furniture_suppression(self):
        # Synthetic TensorRT outputs. CUDA transfer is stubbed; decode, mask and NMS are real.
        spec = importlib.util.spec_from_file_location('offline_person_detector', ROOT / 'src/jetson/detector.py')
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'tensorrt': ModuleType('tensorrt')}):
            spec.loader.exec_module(module)
        detector = module.PersonDetector.__new__(module.PersonDetector)
        detector.detect_floor = True
        detector.cuda = Mock()
        detector.cuda.cudaMemcpy.return_value = detector.cuda.cudaStreamSynchronize.return_value = 0
        detector.context = Mock()
        detector.stream = Mock(value=123)
        detector.gpu = {'images': 7}
        detector.pred = np.zeros((116, 6300), np.float32)
        detector.prototypes = np.ones((32, 120, 160), np.float32)
        detector.download = Mock()
        detector.pred[:4, 0] = (60, 30, 80, 16)
        detector.pred[4, 0] = .6
        detector.pred[84, 0] = 1
        image = np.zeros((480, 640, 3), np.uint8)
        people, fallen = detector.infer(image)
        self.assertEqual(people[0][0], [20, 22, 80, 16])
        self.assertEqual(fallen, [[20, 22, 80, 16]])
        detector.pred[:4, 1] = (60, 30, 100, 60)
        detector.pred[4+59, 1] = .9
        people, fallen = detector.infer(image)
        self.assertEqual(len(people), 1)
        self.assertEqual(fallen, [])


class VoiceOwnershipTests(unittest.TestCase):
    def test_only_a_fall_opens_voice_and_yes_holds_it_until_its_own_ack(self):
        family = FamilyConfirmation.__new__(FamilyConfirmation)
        actions = []
        family.phase, family.voice_held, family.ack_trace = 'idle', False, None
        family.consumed_ids, family.node, family.voice = set(), Mock(), Mock()
        family.voice.switch.side_effect = actions.append
        family.speak = Mock()
        family.on_result = lambda event, decision: actions.append(decision)
        family.release_after_answer()
        self.assertEqual(actions, [])
        family.ask({'event_id': 'case1'})
        family.ask({'event_id': 'case2'})
        family.release_after_answer()
        self.assertEqual(actions, ['start'])
        family.finish('yes')
        ros = Mock()
        def spin(node, timeout_sec):
            actions.append('playing')
            msg = Mock(trace_id='fall_ack_case1_native')
            msg.tts_status.value = 3
            family.playback(msg)
        ros.spin_once.side_effect = spin
        with patch.dict(sys.modules, rclpy=ros):
            family.release_after_answer()
            family.release_after_answer()
        self.assertEqual(actions, ['start', 'yes', 'playing', 'stop'])

    def test_spoken_refusal_acknowledged_once_without_sms_before_releasing_voice(self):
        with tempfile.TemporaryDirectory() as temp:
            sender = Mock()
            notifier = RobotNotifier(Path(temp) / 'refusal.sqlite', sender)
            self.addCleanup(notifier.close)
            notifier.begin([1, 2, 3, 4])
            event = notifier.questions.popleft()
            family = FamilyConfirmation.__new__(FamilyConfirmation)
            family.phase, family.voice_held, family.ack_trace = 'idle', False, None
            family.consumed_ids, family.node, family.voice = set(), Mock(), Mock()
            family.speak, family.on_result = Mock(), notifier.result
            family.ask(event)
            family.phase, family.open_ns, family.deadline = 'listening', 1, time.monotonic() + 30
            reply = Mock(is_final=True, text='不需要。', event_id='asr-refusal')
            reply.header.stamp.sec, reply.header.stamp.nanosec = 1, 0
            family.recognized(reply)
            family.recognized(reply)
            self.assertEqual(family.speak.call_count, 2)
            self.assertIn('我不发送通知', family.speak.call_args.args[0])
            self.assertEqual(notifier.active()['decision'], 'no')
            self.assertEqual(notifier.db.execute('SELECT count(*) FROM submissions').fetchone()[0], 0)
            sender.assert_not_called()
            self.assertEqual([c.args[0] for c in family.voice.switch.call_args_list], ['start'])
            ros = Mock()
            def playback_end(node, timeout_sec):
                msg = Mock(trace_id='fall_ack_' + event['event_id'] + '_native')
                msg.tts_status.value = 3
                family.playback(msg)
            ros.spin_once.side_effect = playback_end
            with patch.dict(sys.modules, rclpy=ros):
                family.release_after_answer()
            ros.spin_once.assert_called_once()
            self.assertEqual([c.args[0] for c in family.voice.switch.call_args_list], ['start', 'stop'])

    def test_restore_only_releases_our_marker_and_keeps_it_on_unknown_reply(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(sys.modules, rclpy=Mock()):
            voice = GraphicalVoice.__new__(GraphicalVoice)
            voice.marker, voice.tag = Path(temp) / 'owner', 'mine'
            voice.node, voice.service, voice.request_type = Mock(), Mock(), Mock()
            voice.marker.symlink_to('other')
            voice.switch('stop')
            voice.service.call_async.assert_not_called()
            voice.marker.unlink()
            voice.marker.symlink_to(voice.tag)
            future = voice.service.call_async.return_value
            future.done.return_value = False
            with self.assertRaises(RuntimeError):
                voice.switch('stop')
            self.assertEqual(owner(voice.marker), 'mine')
            future.done.return_value = True
            header = future.result.return_value.header
            header.header.code, header.status.value, header.status.reason = 0, 1, 0
            voice.switch('stop')
            self.assertIsNone(owner(voice.marker))


class LaunchTests(unittest.TestCase):
    @patch.object(cli, 'read_private', return_value='')
    @patch.object(cli, 'parse')
    def test_foreign_monitor_is_left_alone(self, *_):
        with patch.object(cli, 'unit_state', return_value='active'), patch.object(cli, 'command') as run:
            with self.assertRaises(RuntimeError):
                cli.start()
            run.assert_not_called()

    def test_ssh_failure_does_not_show_idle(self):
        with patch('subprocess.run', return_value=Mock(returncode=255, stdout='')):
            with self.assertRaises(RuntimeError):
                cli.unit_state(cli.WATCH, remote=True)

    @patch.object(cli, 'read_private', return_value='')
    @patch.object(cli, 'parse')
    def test_start_calls_one_transient_session(self, *_):
        with patch.object(cli, 'command') as run, patch.object(cli, 'unit_state', return_value='inactive'), \
                patch.object(cli, 'status', return_value={'resources_released': True}):
            cli.start()
        args = run.call_args.args[0]
        self.assertIn('--unit=' + cli.WATCH, args)
        self.assertNotIn('ssh', args)
        self.assertIn('--wait', args)
        self.assertNotIn('enable', args)
        self.assertEqual(args[-2:], [str(ROOT / 'main.py'), 'case'])
        tag = next(a.split('=', 2)[2] for a in args if a.startswith('--setenv=T1_FALL_SESSION='))
        with patch.dict(os.environ, T1_FALL_SESSION=tag):
            self.assertEqual(cli.session_tag(), tag)

    def test_unavailable_display_continues_case_without_video(self):
        with patch.dict(os.environ, T1_FALL_SESSION='t1-fall-' + 'c' * 32), \
                patch.object(cli, 'command', side_effect=OSError), patch('os.execl') as execute:
            cli.run_case()
            execute.assert_called_once()
            self.assertEqual(os.environ['T1_FALL_NO_PREVIEW'], '1')

    def test_cleanup_only_stops_its_own_display_session(self):
        tag = 't1-fall-' + 'a' * 32
        with patch.dict(os.environ, T1_FALL_SESSION=tag), patch.object(cli, 'command') as run:
            cli.cleanup()
        remote_script = run.call_args.args[0][-1]
        self.assertIn('if test "$owner" = ' + tag, remote_script)
        self.assertIn('stop ' + cli.DISPLAY, remote_script)


if __name__ == '__main__':
    unittest.main()
