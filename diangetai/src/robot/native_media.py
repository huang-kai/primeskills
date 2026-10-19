"""原厂媒体生命周期：真实播放期间暂停双工超时，仅停止自己的 trace。"""
import logging
from pathlib import Path
import re
import time
import uuid

from ..support import AudioError, read_json, write_json
from .device import audio_host

logger = logging.getLogger(__name__)
# 未取得消息类的语义符号时，仅使用已确认的 END=3；失败不是停止证据。
CONFIRMED_TERMINAL = (3,)


def matched_publishers(subscription):
    method = getattr(subscription, 'get_publisher_count', None)
    if callable(method):
        return method()
    # Humble 的 Python 包装尚无此方法；使用同版本公开 rcl API 和受保护的句柄。
    # Humble subscription.cpp 的 handle.pointer 来自 subscription.rcl_ptr()，不是包装对象地址。
    # https://github.com/ros2/rclpy/blob/humble/rclpy/src/rclpy/subscription.cpp
    import ctypes
    try:
        library = ctypes.CDLL('librcl.so')
        count_publishers = library.rcl_subscription_get_publisher_count
        count_publishers.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        count_publishers.restype = ctypes.c_int32
        count = ctypes.c_size_t()
        with subscription.handle:
            code = count_publishers(subscription.handle.pointer, ctypes.byref(count))
        if code != 0:
            raise AudioError('无法确认原厂媒体状态订阅匹配。')
        return count.value
    except (AttributeError, OSError):
        raise AudioError('当前 ROS 环境不能确认媒体状态订阅匹配，未提交播放。') from None


class NativeMedia:
    def __init__(self, robot, record=None):
        try:
            from aimdk_msgs.msg import TtsStatus
            from aimdk_msgs.srv import PlayMediaFile, StopTtsTraceId
        except ImportError:
            raise AudioError('目标原厂消息包缺少 Agent 媒体生命周期接口；未回退到其他播放方式。') from None
        self.robot = robot
        self.play_type, self.stop_type = PlayMediaFile, StopTtsTraceId
        self.play_client = robot.node.create_client(PlayMediaFile, '/aimdk_5Fmsgs/srv/PlayMediaFile')
        self.stop_client = robot.node.create_client(StopTtsTraceId, '/aimdk_5Fmsgs/srv/StopTtsTraceId')
        self.subscription = robot.node.create_subscription(TtsStatus, '/interaction/tts_status', self.on_status, robot.qos)
        self.trace_id = ''
        self.events = []
        self.submitted = False
        self.record = Path(record) if record is not None else None
        self.stop_failed = False
        self.stop_requested = False
        status = getattr(TtsStatus(), 'tts_status', None)
        status_type = type(status)
        self.end_status = 3
        for name in ('TTS_STATUE_TYPE_END', 'END'):
            value = getattr(status_type, name, None)
            if type(value) is int:
                self.end_status = value
                break
        self.confirmed_terminal = {self.end_status}
        for name in ('TTS_STATUS_TYPE_STOP', 'STOP', 'STOPPED'):
            value = getattr(status_type, name, None)
            if type(value) is int:
                self.confirmed_terminal.add(value)

    def wait_feedback(self, cancellable=True):
        deadline = time.monotonic() + 6
        while matched_publishers(self.subscription) < 1:
            self.robot.spin(cancellable=cancellable)
            if time.monotonic() >= deadline:
                raise AudioError('原厂媒体状态订阅尚未匹配，未提交请求。')

    def clear_record(self):
        record = getattr(self, 'record', None)
        if record is not None and record.exists():
            if read_json(record).get('trace_id') != self.trace_id:
                raise AudioError('媒体恢复记录已变化，未删除。')
            record.unlink()

    def recover(self):
        saved = read_json(self.record)
        trace = saved.get('trace_id', '')
        if not re.fullmatch(r'diangetai_[0-9a-f]{32}', trace) or saved.get('audio_host') != audio_host():
            raise AudioError('媒体恢复记录与当前播放主机不匹配，未发送停止。')
        if type(saved.get('stop_requested', False)) is not bool:
            raise AudioError('媒体停止记录无效，未发送请求。')
        self.stop_requested = saved.get('stop_requested', False)
        self.trace_id, self.submitted = trace, True
        self.stop()

    def on_status(self, message):
        if message.trace_id == self.trace_id:
            self.events.append((time.monotonic(), int(message.tts_status.value)))
            self.events = self.events[-32:]

    def call(self, client, request, cancellable=True):
        deadline = time.monotonic() + 12
        while not client.service_is_ready():
            self.robot.spin(cancellable=cancellable)
            if time.monotonic() >= deadline:
                raise AudioError('原厂媒体服务未就绪。')
        request.header.header.stamp = self.robot.node.get_clock().now().to_msg()
        future = client.call_async(request)
        while not future.done():
            self.robot.spin(cancellable=cancellable)
            if time.monotonic() >= deadline:
                raise AudioError('原厂媒体请求超时，未重复提交。')
        result = future.result()
        if result is None or result.header.header.code != 0:
            raise AudioError('原厂媒体请求失败。')
        return result

    def start(self, remote_path, duration):
        if self.submitted:
            raise AudioError('本任务仍有未结束的音频。')
        if not 0 < duration <= 605 or not str(remote_path).startswith('/tmp/'):
            raise AudioError('原厂媒体路径或时长无效。')
        if self.record is not None and self.record.exists():
            raise AudioError('存在未确认的媒体恢复记录，请先运行 status/recover。')
        self.wait_feedback()
        self.trace_id = 'diangetai_' + uuid.uuid4().hex
        self.stop_requested = self.stop_failed = False
        self.events.clear()
        self.duration, self.requested_at = duration, time.monotonic()
        request = self.play_type.Request()
        media = request.media_file_req
        media.file_name, media.sample_rate = str(remote_path), 16000
        media.priority_level.value = 1
        media.domain, media.trace_id = 'diangetai', self.trace_id
        media.is_interrupted = False
        if self.record is not None:
            write_json(self.record, {'trace_id': self.trace_id, 'audio_host': audio_host()})
        self.submitted = True  # 即使 RPC 超时，也只能按本 trace 清理。
        result = self.call(self.play_client, request)
        if not result.tts_resp.is_success:
            self.submitted = False
            self.clear_record()
            raise AudioError('原厂播放器未接受本次音频。')

    @property
    def started(self):
        return next((at for at, state in self.events if state == 2), None)

    def poll(self, now=None):
        now = time.monotonic() if now is None else now
        confirmed = getattr(self, 'confirmed_terminal', CONFIRMED_TERMINAL)
        terminal = next(((at, state) for at, state in self.events if state in set(confirmed) | {3, 4, 5, 7}), None)
        if terminal:
            at, state = terminal
            if state in confirmed:
                self.submitted = False
                self.clear_record()
            if state != getattr(self, 'end_status', 3) or self.started is None or at - self.started < self.duration - max(1, self.duration * .03):
                logger.error('MEDIA_FAILURE %s', {'states': [s for _, s in self.events],
                      'expected': round(self.duration, 3),
                      'elapsed': None if self.started is None else round(at - self.started, 3)})
                raise AudioError('原厂音频提前停止或播放失败。')
            return True
        if self.started is None and now - self.requested_at > 12:
            raise AudioError('原厂音频开始未确认。')
        if now - self.requested_at > self.duration + 20:
            raise AudioError('原厂音频结束未确认。')
        return None

    def stop(self):
        if not self.submitted:
            return
        confirmed = getattr(self, 'confirmed_terminal', CONFIRMED_TERMINAL)
        if any(state in confirmed for _, state in self.events):
            self.submitted = False
            self.clear_record()
            return
        if getattr(self, 'stop_failed', False):
            raise AudioError('本次原厂音频停止未确认；记录保留，未重复提交。')
        request = self.stop_type.Request()
        request.trace_id = self.trace_id
        try:
            self.wait_feedback(cancellable=False)
            if any(state in confirmed for _, state in self.events):
                self.submitted = False
                self.clear_record()
                return
            if not getattr(self, 'stop_requested', False):
                record = getattr(self, 'record', None)
                if record is not None:
                    saved = read_json(record)
                    if saved.get('trace_id') != self.trace_id:
                        raise AudioError('媒体恢复记录已变化，未发送停止。')
                    write_json(record, dict(saved, stop_requested=True))
                self.stop_requested = True
                self.call(self.stop_client, request, cancellable=False)
            deadline = time.monotonic() + 5
            while not any(state in confirmed for _, state in self.events):
                self.robot.spin(cancellable=False)
                if time.monotonic() >= deadline:
                    raise AudioError('本次原厂音频停止未确认。')
        except BaseException:
            self.stop_failed = True
            raise
        self.submitted = False
        self.clear_record()

    def close(self):
        try:
            self.stop()
        finally:
            self.robot.node.destroy_subscription(self.subscription)
            self.robot.node.destroy_client(self.play_client)
            self.robot.node.destroy_client(self.stop_client)
