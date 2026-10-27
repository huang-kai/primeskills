"""独立 ROS 执行器、原厂响应及媒体服务端文件准备。"""
from contextlib import contextmanager
from pathlib import Path
import shlex
import subprocess
import time
import uuid
import tempfile

from ..support import AudioError
from .device import audio_host


class RequestRejected(AudioError):
    """原厂返回结构完整且明确拒绝的响应；超时或未知状态不属于此类。"""


def ssh_options(runtime):
    return ['-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', '-o', 'ServerAliveInterval=5',
            '-o', 'ServerAliveCountMax=2']


class Robot:
    def __init__(self, runtime, checkpoint):
        self.runtime, self.checkpoint = runtime, checkpoint
        import rclpy
        from rclpy.context import Context
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.signals import SignalHandlerOptions
        from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
        self.ros, self.context = rclpy, Context()
        rclpy.init(context=self.context, signal_handler_options=SignalHandlerOptions.NO)
        self.node = rclpy.create_node('chengyu_' + uuid.uuid4().hex[:8], context=self.context)
        self.executor = SingleThreadedExecutor(context=self.context)
        self.executor.add_node(self.node)
        self.qos = QoSProfile(depth=100, reliability=ReliabilityPolicy.BEST_EFFORT,
                              durability=DurabilityPolicy.VOLATILE)

    def spin(self, seconds=.05, cancellable=True):
        if cancellable:
            self.checkpoint()
        self.executor.spin_once(timeout_sec=seconds)

    def call(self, client, request, timeout=12, cancellable=True, before_submit=None):
        if cancellable:
            self.checkpoint()
        deadline = time.monotonic() + timeout
        while not client.service_is_ready():
            self.spin(cancellable=cancellable)
            if time.monotonic() >= deadline:
                raise AudioError('原厂服务未就绪。')
        if cancellable:
            self.checkpoint()
        if before_submit is not None:
            before_submit()
        future = client.call_async(request)
        while not future.done():
            self.spin(cancellable=cancellable)
            if time.monotonic() >= deadline:
                raise AudioError('原厂请求超时，执行结果未知；未重发。')
        return self.validate_response(future.result())

    @staticmethod
    def validate_response(result):
        common = getattr(result, 'header', None)
        code = getattr(getattr(common, 'header', None), 'code', None)
        state = getattr(common, 'status', None)
        value, reason = getattr(state, 'value', None), getattr(state, 'reason', None)
        if not all(type(field) is int for field in (code, value, reason)):
            raise AudioError('原厂响应结构不完整，执行结果未知；未重发。')
        details = f'code={code}, status={value}, reason={reason}'
        refused = any(getattr(state, name, None) == expected and value == expected
                      for name, expected in (('FAILURE', 2), ('INVALID', 5), ('NOT_READY', 100)))
        if code != 0 or refused:
            raise RequestRejected('原厂明确拒绝请求（' + details + '）。')
        if value != 1 or reason != 0:
            raise AudioError('原厂响应状态未确认（' + details + '），执行结果未知；未重发。')
        return result

    @contextmanager
    def prepared_audio(self, path, checkpoint=None):
        if audio_host() == 'local':
            yield Path(path).resolve()
            return
        from .device import host_command
        def run(args, timeout, cancellable=True):
            if checkpoint:
                if cancellable:
                    checkpoint()
            # 传输期间仍接收原厂状态，协作取消可及时停止自己的 scp/ssh。
            with tempfile.TemporaryFile() as output:
                process = subprocess.Popen(args, stdout=output, stderr=subprocess.DEVNULL)
                try:
                    deadline = time.monotonic() + timeout
                    while process.poll() is None:
                        self.spin(cancellable=cancellable)
                        if time.monotonic() >= deadline:
                            raise AudioError('音频文件传输或清理超时。')
                    if process.returncode:
                        raise AudioError('音频文件传输或清理失败。')
                    output.seek(0)
                    return output.read(4096).decode().strip()
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=2)
        directory = run(host_command(self.runtime, 'mktemp -d /tmp/chengyu-transfer.XXXXXXXX'), 12)
        import re
        if not re.fullmatch(r'/tmp/chengyu-transfer\.[A-Za-z0-9]{8}', directory):
            raise AudioError('原厂媒体临时路径无效。')
        remote = directory + '/audio.wav'
        try:
            run(['scp', *ssh_options(self.runtime), str(path), audio_host() + ':' + remote], 45)
            yield Path(remote)
        finally:
            # 清理不被任务取消跳过，且只删除自己刚创建的目录。
            run(host_command(self.runtime, 'rm -f ' + shlex.quote(remote)
                             + ' && rmdir ' + shlex.quote(directory)), 15, cancellable=False)

    def close(self):
        self.executor.shutdown(timeout_sec=2)
        self.node.destroy_node()
        self.ros.shutdown(context=self.context)
