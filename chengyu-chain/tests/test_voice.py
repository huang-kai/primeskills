"""原厂 VAD 与 120 秒边界；使用假时钟、假 ROS 消息和订阅，不连接机器人。"""
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from chengyu_game.support import AudioError
from chengyu_game.voice import AUDIO_TOPIC, STATE_TOPIC, VoiceInput


class Clock:
    def __init__(self):
        self.value = 1000.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds

    def now(self):
        return SimpleNamespace(nanoseconds=int((1_000_000 + self.value) * 1_000_000_000))


class Node:
    def __init__(self, clock):
        self.clock = clock
        self.subscriptions = []
        self.destroyed = []
        self.publishers = {AUDIO_TOPIC: 1, STATE_TOPIC: 1}

    def get_clock(self):
        return self.clock

    def create_subscription(self, message, topic, callback, qos):
        subscription = SimpleNamespace(message=message, topic=topic, callback=callback, qos=qos)
        self.subscriptions.append(subscription)
        return subscription

    def destroy_subscription(self, subscription):
        self.destroyed.append(subscription)

    def count_publishers(self, topic):
        return self.publishers.get(topic, 0)


class Robot:
    def __init__(self, clock):
        self.node = Node(clock)

    def spin(self):
        self.node.clock.advance(1)


def ros_modules():
    messages, qos = ModuleType('aimdk_msgs.msg'), ModuleType('rclpy.qos')
    messages.ProcessedAudioOutput = type('ProcessedAudioOutput', (), {})
    messages.AgentConversationState = type('AgentConversationState', (), {})
    messages.AgentConversationMainState = SimpleNamespace(LISTENING=1)
    qos.QoSProfile = lambda **kwargs: SimpleNamespace(**kwargs)
    qos.ReliabilityPolicy = SimpleNamespace(BEST_EFFORT='best-effort')
    qos.DurabilityPolicy = SimpleNamespace(VOLATILE='volatile')
    return {'aimdk_msgs': ModuleType('aimdk_msgs'), 'aimdk_msgs.msg': messages,
            'rclpy': ModuleType('rclpy'), 'rclpy.qos': qos}


class VoiceTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.robot = Robot(self.clock)
        self.voice = VoiceInput(self.robot, clock=self.clock)
        self.origin = self.clock()

    def at(self, seconds):
        self.clock.value = self.origin + seconds

    def state(self, value):
        self.voice.on_state(SimpleNamespace(main_state=SimpleNamespace(value=value)))

    def packet(self, vad, size=3200, stream=0, stamp=None):
        timestamp = self.clock.now().nanoseconds if stamp is None else stamp
        return SimpleNamespace(
            stream_id=stream, audio_vad_state=SimpleNamespace(value=vad), audio_data=b'\x01\x00' * (size // 2),
            header=SimpleNamespace(stamp=SimpleNamespace(
                sec=timestamp // 1_000_000_000, nanosec=timestamp % 1_000_000_000)),
        )

    def listen(self):
        self.state(1)
        self.voice.listen()

    def sentence(self, *, size=3200, stream=0, stamp=None):
        self.voice.on_audio(self.packet(1, size=size, stream=stream, stamp=stamp))
        self.clock.advance(.1)
        self.voice.on_audio(self.packet(3, size=size, stream=stream, stamp=stamp))

    def test_start_subscribes_to_processed_audio_and_state_without_real_ros(self):
        with patch.dict(sys.modules, ros_modules()):
            self.voice.start()
        self.assertEqual([item.topic for item in self.robot.node.subscriptions], [AUDIO_TOPIC, STATE_TOPIC])
        self.assertTrue(all(item.qos.reliability == 'best-effort' for item in self.robot.node.subscriptions))
        self.assertFalse(self.voice.active)
        self.voice.close()
        self.assertEqual(self.robot.node.destroyed, self.robot.node.subscriptions)

    def test_missing_frontend_has_bounded_start_failure(self):
        self.robot.node.publishers[AUDIO_TOPIC] = 0
        with patch.dict(sys.modules, ros_modules()), self.assertRaises(AudioError):
            self.voice.start()
        self.assertEqual(self.clock() - self.origin, 15)
        self.voice.close()
        self.assertEqual(len(self.robot.node.destroyed), 2)

    def test_native_idle_at_thirty_seconds_does_not_end_game_before_120(self):
        self.listen()
        self.at(30)
        self.state(0)
        self.assertIsNone(self.voice.poll())
        self.assertTrue(self.voice.active)
        self.at(119.999)
        self.assertIsNone(self.voice.poll())
        self.at(120)
        self.assertEqual(self.voice.poll(), ('timeout', None))
        self.assertFalse(self.voice.active)
        self.assertIsNone(self.voice.poll())

    def test_idle_wake_reply_and_listening_transitions_do_not_reset_deadline(self):
        self.listen()
        deadline = self.voice.deadline
        for seconds, state in ((30, 0), (80, 3), (110, 1), (119, 1)):
            self.at(seconds)
            self.state(state)
            self.assertIsNone(self.voice.poll())
            self.assertEqual(self.voice.deadline, deadline)
        self.at(120)
        self.assertEqual(self.voice.poll(), ('timeout', None))

    def test_complete_vad_yields_once_then_mutes_until_explicit_listen(self):
        self.listen()
        self.voice.on_audio(self.packet(1))
        self.clock.advance(.1)
        self.voice.on_audio(self.packet(2))
        self.clock.advance(.1)
        self.voice.on_audio(self.packet(3))
        event = self.voice.poll()
        self.assertEqual(event, ('utterance', b'\x01\x00' * 4800))
        self.assertFalse(self.voice.active)
        self.assertIsNone(self.voice.poll())
        self.sentence()
        self.assertIsNone(self.voice.poll())
        self.voice.listen()
        self.sentence()
        self.assertEqual(self.voice.poll()[0], 'utterance')

    def test_unknown_idle_and_speaking_states_do_not_capture_audio(self):
        for state in (None, 0, 2, 3, 99):
            with self.subTest(state=state):
                self.voice.listen()
                self.voice.state = None
                if state is not None:
                    self.state(state)
                self.sentence()
                self.assertIsNone(self.voice.poll())
                self.assertIsNone(self.voice.buffer)

    def test_leaving_listening_discards_partial_user_speech(self):
        self.listen()
        self.voice.on_audio(self.packet(1))
        self.state(3)
        self.state(1)
        self.voice.on_audio(self.packet(3))
        self.assertIsNone(self.voice.poll())
        self.sentence()
        self.assertEqual(self.voice.poll()[0], 'utterance')

    def test_stale_packets_wrong_stream_and_missing_begin_are_discarded(self):
        self.listen()
        self.sentence(stamp=self.voice.opened_at_ns - 1)
        self.assertIsNone(self.voice.poll())
        self.sentence(stream=2)
        self.assertIsNone(self.voice.poll())
        for vad in (2, 3):
            self.voice.on_audio(self.packet(vad, size=6400))
            self.assertIsNone(self.voice.poll())
        self.sentence()
        self.assertEqual(self.voice.poll()[0], 'utterance')

    def test_packets_from_previous_listening_window_cannot_form_new_answer(self):
        self.listen()
        previous = self.clock.now().nanoseconds
        self.clock.advance(1)
        self.voice.listen()
        self.sentence(stamp=previous)
        self.assertIsNone(self.voice.poll())

    def test_sentence_started_at_119_seconds_can_finish_within_18_seconds(self):
        self.listen()
        self.at(119)
        self.voice.on_audio(self.packet(1, size=640))
        for seconds in range(121, 137, 2):
            self.at(seconds)
            self.voice.on_audio(self.packet(2, size=640))
            self.assertIsNone(self.voice.poll())
        self.at(137)
        self.voice.on_audio(self.packet(3, size=640))
        event = self.voice.poll()
        self.assertEqual(event[0], 'utterance')
        self.assertEqual(len(event[1]), 6400)

    def test_sentence_started_at_119_cannot_extend_beyond_18_seconds(self):
        self.listen()
        self.at(119)
        self.voice.on_audio(self.packet(1))
        for seconds in range(121, 137, 2):
            self.at(seconds)
            self.voice.on_audio(self.packet(2))
        self.at(137.001)
        self.voice.on_audio(self.packet(3))
        self.assertEqual(self.voice.poll(), ('timeout', None))

    def test_new_begin_at_or_after_deadline_is_rejected(self):
        for seconds in (120, 121):
            with self.subTest(seconds=seconds):
                self.at(0)
                self.listen()
                self.at(seconds)
                self.sentence()
                self.assertEqual(self.voice.poll(), ('timeout', None))

    def test_expired_new_begin_cannot_attach_its_end_to_previous_sentence(self):
        self.listen()
        self.at(119)
        self.voice.on_audio(self.packet(1, size=6400))
        self.at(120.1)
        self.voice.on_audio(self.packet(1, size=6400))
        self.at(120.2)
        self.voice.on_audio(self.packet(3, size=6400))
        self.assertEqual(self.voice.poll(), ('timeout', None))

    def test_noise_and_short_fragments_do_not_extend_wait_deadline(self):
        self.listen()
        deadline = self.voice.deadline
        for seconds in (30, 60, 100, 119):
            self.at(seconds)
            self.voice.on_audio(self.packet(0, size=6400))
            self.sentence(size=1000)
            self.assertIsNone(self.voice.poll())
            self.assertEqual(self.voice.deadline, deadline)
        self.at(120)
        self.assertEqual(self.voice.poll(), ('timeout', None))

    def test_vad_silence_cancels_pending_segment_without_emitting_partial_audio(self):
        self.listen()
        self.voice.on_audio(self.packet(1, size=6400))
        self.voice.on_audio(self.packet(0))
        self.voice.on_audio(self.packet(3, size=6400))
        self.assertIsNone(self.voice.poll())
        self.assertIsNone(self.voice.buffer)

    def test_missing_end_gap_exceeding_three_seconds_discards_partial(self):
        self.listen()
        self.voice.on_audio(self.packet(1, size=6400))
        self.clock.advance(.1)
        self.voice.on_audio(self.packet(2, size=6400))
        self.clock.advance(3.001)
        self.assertIsNone(self.voice.poll())
        self.voice.on_audio(self.packet(3, size=6400))
        self.assertIsNone(self.voice.poll())
        self.assertIsNone(self.voice.buffer)
        self.sentence()
        self.assertEqual(self.voice.poll()[0], 'utterance')

    def test_gap_after_only_begin_is_rejected_even_before_poll_runs(self):
        self.listen()
        self.voice.on_audio(self.packet(1, size=6400))
        self.clock.advance(3.001)
        self.voice.on_audio(self.packet(3, size=6400))
        self.assertIsNone(self.voice.poll())
        self.assertIsNone(self.voice.buffer)

    def test_resuming_original_deadline_does_not_grant_another_120_seconds(self):
        self.listen()
        deadline = self.voice.deadline
        self.at(90)
        self.sentence()
        self.assertEqual(self.voice.poll()[0], 'utterance')
        self.at(100)
        self.voice.listen(deadline=deadline)
        self.at(120)
        self.assertEqual(self.voice.poll(), ('timeout', None))

    def test_close_discards_pending_audio_and_late_callbacks(self):
        self.listen()
        self.voice.on_audio(self.packet(1))
        self.voice.close()
        self.state(1)
        self.voice.on_audio(self.packet(3))
        self.sentence()
        self.assertIsNone(self.voice.poll())
        self.assertFalse(self.voice.active)

    def test_closed_input_cannot_be_reopened(self):
        self.listen()
        self.voice.close()
        try:
            self.voice.listen()
        except AudioError:
            pass
        self.state(1)
        self.sentence()
        self.assertIsNone(self.voice.poll())
        self.assertFalse(self.voice.active)


if __name__ == '__main__':
    unittest.main()
