"""完整 VAD 收音与游戏等待期限；原厂 IDLE 不结束应用或重置期限。"""
import time
import logging

from .support import AudioError

AUDIO_TOPIC = '/agent/process_audio_output'
STATE_TOPIC = '/aima/agent/interaction_state'
logger = logging.getLogger(__name__)


class VoiceInput:
    def __init__(self, robot, clock=time.monotonic):
        self.robot, self.clock = robot, clock
        self.state = None
        self.opened_at_ns = 0
        self.deadline = 0
        self.active = False
        self.buffer = None
        self.started_at = self.last_packet_at = 0
        self.answer = None
        self.subscriptions = []
        self.error = None
        self.closed = False
        self.ready = False
        self.last_health_check = 0

    def start(self):
        from aimdk_msgs.msg import ProcessedAudioOutput, AgentConversationState, AgentConversationMainState
        from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
        if AgentConversationMainState.LISTENING != 1:
            raise AudioError('原厂会话状态枚举不匹配。')
        qos = QoSProfile(depth=500, reliability=ReliabilityPolicy.BEST_EFFORT,
                         durability=DurabilityPolicy.VOLATILE)
        for message, topic, callback in ((ProcessedAudioOutput, AUDIO_TOPIC, self.on_audio),
                                         (AgentConversationState, STATE_TOPIC, self.on_state)):
            self.subscriptions.append(self.robot.node.create_subscription(message, topic, callback, qos))
        end = self.clock() + 15
        while any(self.robot.node.count_publishers(topic) == 0 for topic in (AUDIO_TOPIC, STATE_TOPIC)):
            self.robot.spin()
            if self.clock() >= end:
                raise AudioError('原厂语音前端未就绪，未使用原始麦克风。')
        self.ready = True
        self.last_health_check = self.clock()

    def listen(self, deadline=None):
        if self.closed:
            return
        self.mute()
        self.active = True
        self.deadline = self.clock() + 120 if deadline is None else deadline
        self.opened_at_ns = self.robot.node.get_clock().now().nanoseconds
        logger.info('GAME_LISTEN seconds_remaining=%.2f', max(0, self.deadline - self.clock()))

    def mute(self):
        self.active = False
        self.buffer = self.answer = None

    def on_state(self, message):
        if self.closed:
            return
        previous, self.state = self.state, int(message.main_state.value)
        if self.state != previous:
            logger.info('GAME_FRONTEND previous=%s state=%s listening=%s', previous, self.state, self.active)
        if self.state != 1:
            # 唤醒应答、机器人发声、IDLE 均不能拼接成用户的下一句话。
            self.buffer = None

    def on_audio(self, message):
        if self.closed or not self.active or self.state != 1 or int(message.stream_id) != 0:
            return
        stamp = message.header.stamp
        timestamp = stamp.sec * 1_000_000_000 + stamp.nanosec
        if timestamp and timestamp < self.opened_at_ns:
            return
        vad, pcm, now = int(message.audio_vad_state.value), bytes(message.audio_data), self.clock()
        if vad not in (0, 1, 2, 3) or len(pcm) % 2:
            self.error = AudioError('原厂 VAD 格式不符合 PCM16 协议。')
            return
        if vad == 0:
            self.buffer = None
            return
        if vad == 1:
            self.buffer = None
            # 期限内开始的完整句子可获得最多 18 秒收尾时间。
            if now >= self.deadline:
                return
            self.buffer = bytearray()
            self.started_at = now
            self.last_packet_at = now
        if self.buffer is None:
            return
        if now - self.started_at > 18 or now - self.last_packet_at > 3:
            self.buffer = None
            return
        self.buffer.extend(pcm)
        self.last_packet_at = now
        if len(self.buffer) > 16000 * 2 * 15:
            self.buffer = None
        elif vad == 3:
            audio = bytes(self.buffer)
            self.buffer = None
            if len(audio) >= 16000 * 2 * .2:
                self.answer = audio
                self.active = False

    def poll(self):
        if self.error:
            raise self.error
        if self.answer is not None:
            audio, self.answer = self.answer, None
            return 'utterance', audio
        now = self.clock()
        if self.ready and not self.closed and now - self.last_health_check >= 5:
            self.last_health_check = now
            if any(self.robot.node.count_publishers(topic) == 0 for topic in (AUDIO_TOPIC, STATE_TOPIC)):
                raise AudioError('原厂语音前端已离线，游戏结束。')
        if self.buffer is not None and (now - self.last_packet_at > 3 or now - self.started_at > 18):
            self.buffer = None
        if self.active and now >= self.deadline and self.buffer is None:
            self.mute()
            logger.info('GAME_WAIT_DEADLINE')
            return 'timeout', None
        return None

    def close(self):
        self.closed = True
        self.mute()
        for subscription in self.subscriptions:
            self.robot.node.destroy_subscription(subscription)
        self.subscriptions.clear()
