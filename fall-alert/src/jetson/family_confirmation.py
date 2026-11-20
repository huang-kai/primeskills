"""Handle two timed response windows for one confirmed fall."""

import os
import time
import unicodedata
from uuid import uuid4

from graphical_voice import GraphicalVoice


QUESTION = '检测到您摔倒了，是否需要通知家人？'
REPEAT_QUESTION = '您是否需要通知家人？请回答需要或不需要。如果仍没有听到您的回答，我会发短信通知家人。'
ACKNOWLEDGEMENT = '好的，正在发送短信通知家人。'
REFUSAL_ACK = '好的，我不发送通知。您先别急，能安全起身时再慢慢起来。'
SILENCE_ANNOUNCEMENT = '没有听到您的回答，正在发送短信通知家人。'
RESPONSE_SECONDS = 30
YES = {'是', '是的', '好', '好的', '要', '需要', '通知', '通知家人', '请通知家人',
       '我需要', '我需要通知家人', '需要帮我通知家人', '我需要帮我通知家人',
       '需要通知家人', '帮我通知家人', '是需要', '是的需要', '是的通知家人'}
NO = {'不', '否', '不是', '不用', '不需要', '不要', '别通知', '不用通知',
      '不要通知家人', '不用通知家人', '不需要通知家人', '不要通知', '不通知', '我不需要',
      '我不需要通知家人', '先不要通知', '先不要通知家人'}


def answer(text):
    if '?' in unicodedata.normalize('NFKC', text):
        return 'unclear'
    words = ''.join(c for c in unicodedata.normalize('NFKC', text)
                    if not c.isspace() and not unicodedata.category(c).startswith('P'))
    if words == '嗯':
        return 'hesitation'
    if words in YES:
        return 'yes'
    if words in NO:
        return 'no'
    return 'unclear'


class FamilyConfirmation:
    response_seconds = RESPONSE_SECONDS

    def __init__(self, node, on_result, response_seconds=RESPONSE_SECONDS):
        self.response_seconds = response_seconds
        from aimdk_msgs.msg import AgentConversationState, AsrResult, TtsStatus
        from aimdk_msgs.srv import PlayTts
        from rclpy.qos import qos_profile_sensor_data
        self.on_result = on_result
        self.node = node
        self.phase = 'idle'
        self.event = None
        self.ack_trace = None
        self.native_listening = False
        self.voice = GraphicalVoice(node, os.environ.get('T1_FALL_SESSION') or 't1-fall-' + uuid4().hex)
        self.voice_held = False
        self.deadline = 0
        self.consumed_ids = set()
        self.accepted = False
        self.ended = False
        self.tts_type = PlayTts
        self.tts = node.create_client(PlayTts, '/aimdk_5Fmsgs/srv/PlayTts')
        node.create_subscription(TtsStatus, '/interaction/tts_status', self.playback, qos_profile_sensor_data)
        node.create_subscription(AsrResult, '/aima/agent/asr_result', self.recognized, qos_profile_sensor_data)
        node.create_subscription(AgentConversationState, '/aima/agent/interaction_state',
                                 self.voice_state, qos_profile_sensor_data)
        node.create_timer(.2, self.tick)
        if not self.tts.wait_for_service(timeout_sec=8):
            raise RuntimeError('Fall question speech services unavailable')

    def speak(self, text, trace):
        req = self.tts_type.Request()
        req.header.header.stamp = self.node.get_clock().now().to_msg()
        req.tts_req.text = text
        req.tts_req.domain = 't1-fall-alert'
        req.tts_req.trace_id = trace
        req.tts_req.priority_level.value = req.tts_req.priority_level.INTERACTION_L6
        req.tts_req.priority_weight = 10
        req.tts_req.is_interrupted = False
        return self.tts.call_async(req)

    def ask(self, event):
        if self.phase != 'idle' or self.voice_held:
            return
        self.voice.switch('start')
        self.voice_held = True
        self.event = event
        self.consumed_ids.clear()
        self.prompt(1)

    def prompt(self, attempt):
        self.attempt = attempt
        self.accepted = self.ended = False
        self.phase = 'question'
        self.deadline = time.monotonic() + 22
        trace = self.trace = f"fall_confirm_{self.event['event_id']}_q{attempt}"
        event = self.event
        print('FAMILY_QUESTION', event['event_id'], attempt, flush=True)

        def result(future):
            if self.phase != 'question' or self.trace != trace:
                return
            if future.cancelled() or future.exception() is not None:
                self.finish('question_failed')
                return
            response = future.result()
            if response.header.header.code or not response.tts_resp.is_success:
                self.finish('question_failed')
                return
            self.accepted = True
            print('FAMILY_QUESTION_ACCEPTED', event['event_id'], flush=True)
            self.start_listening()
        self.speak(QUESTION if attempt == 1 else REPEAT_QUESTION, trace).add_done_callback(result)

    def playback(self, msg):
        if self.ack_trace and (msg.trace_id == self.ack_trace or
                               msg.trace_id.startswith(self.ack_trace + '_')):
            status = msg.tts_status.value
            if status in (3, 4, 5, 7):
                print('FAMILY_ACK_PLAYBACK', self.ack_trace, status, flush=True)
                self.ack_trace = None
            return
        if self.phase != 'question' or not (msg.trace_id == self.trace or
                                            msg.trace_id.startswith(self.trace + '_')):
            return
        status = msg.tts_status.value
        if status == 3:
            self.ended = True
            print('FAMILY_QUESTION_END', msg.trace_id, flush=True)
            self.start_listening()
        elif status in (4, 5, 7):
            self.finish('question_interrupted')

    def start_listening(self):
        if not (self.accepted and self.ended) or self.phase != 'question':
            return
        self.open_ns = self.node.get_clock().now().nanoseconds
        self.phase = 'listening'
        self.deadline = time.monotonic() + self.response_seconds
        print('FAMILY_LISTENING', self.event['event_id'], self.attempt, flush=True)

    def voice_state(self, msg):
        # LISTENING with an idle substate still has the microphone.
        self.native_listening = msg.main_state.value == 1
        if self.phase == 'listening' and msg.main_state.value == 0:
            self.finish('listen_unavailable')

    def recognized(self, msg):
        if not msg.is_final or not msg.text.strip():
            return
        if self.phase != 'listening' or time.monotonic() >= self.deadline:
            return
        # A previous dialogue can publish a late or retained transcription.
        stamp = msg.header.stamp
        if not (stamp.sec or stamp.nanosec):
            stamp = msg.header.meas_stamp
        stamp_ns = stamp.sec * 10**9 + stamp.nanosec
        if not stamp_ns or stamp_ns < self.open_ns:
            print('FAMILY_ASR_DISCARDED', 'missing_stamp' if not stamp_ns else 'stale', flush=True)
            return
        if msg.event_id and msg.event_id in self.consumed_ids:
            return
        decision = answer(msg.text)
        if decision == 'hesitation':
            return
        if msg.event_id:
            self.consumed_ids.add(msg.event_id)
        self.finish(decision)

    def acknowledge(self, event_id, text):
        trace = self.ack_trace = 'fall_ack_' + event_id
        def result(future):
            ok = (not future.cancelled() and future.exception() is None
                  and not future.result().header.header.code and future.result().tts_resp.is_success)
            if not ok and self.ack_trace == trace:
                self.ack_trace = None
            print('FAMILY_ACK_ACCEPTED', event_id, bool(ok), flush=True)
        try:
            self.speak(text, trace).add_done_callback(result)
        except Exception as error:
            self.ack_trace = None
            print('FAMILY_ACK_FAILED', event_id, type(error).__name__, flush=True)

    def release_after_answer(self):
        if self.phase != 'idle' or not self.voice_held:
            return
        self.wait_for_ack()
        self.voice.switch('stop')
        self.voice_held = False

    def wait_for_ack(self):
        import rclpy
        deadline = time.monotonic() + 15
        while self.ack_trace and rclpy.ok() and time.monotonic() < deadline:
            rclpy.spin_once(self.node, timeout_sec=.1)
        if self.ack_trace:
            print('FAMILY_ACK_TIMEOUT', self.ack_trace, flush=True)

    def finish(self, reason):
        event = self.event
        self.on_result(event, reason)
        print('FAMILY_RESULT', event['event_id'], reason, flush=True)
        self.phase = 'idle'
        self.event = None
        text = {'yes': ACKNOWLEDGEMENT, 'no': REFUSAL_ACK, 'no_reply': SILENCE_ANNOUNCEMENT}.get(reason)
        if text:
            self.acknowledge(event['event_id'], text)

    def tick(self):
        if self.phase == 'idle' or time.monotonic() < self.deadline:
            return
        if self.phase != 'listening':
            self.finish(self.phase + '_timeout')
        elif not self.node.count_publishers('/aima/agent/asr_result') or not self.native_listening:
            self.finish('listen_unavailable')
        elif self.attempt == 1:
            self.prompt(2)
        else:
            self.finish('no_reply')
