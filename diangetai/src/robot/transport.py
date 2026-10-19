"""独立 ROS 执行器、原厂响应及媒体服务端文件准备。"""
from contextlib import contextmanager
from pathlib import Path
import shlex
import subprocess
import time
import uuid

from ..support import AudioError
from .device import audio_host


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
        self.node = rclpy.create_node('diangetai_' + uuid.uuid4().hex[:8], context=self.context)
        self.executor = SingleThreadedExecutor(context=self.context)
        self.executor.add_node(self.node)
        self.qos = QoSProfile(depth=100, reliability=ReliabilityPolicy.BEST_EFFORT,
                              durability=DurabilityPolicy.VOLATILE)

    def spin(self, seconds=.05, cancellable=True):
        if cancellable:
            self.checkpoint()
        self.executor.spin_once(timeout_sec=seconds)

    def call(self, client, request, timeout=12, cancellable=True):
        deadline = time.monotonic() + timeout
        while not client.service_is_ready():
            self.spin(cancellable=cancellable)
            if time.monotonic() >= deadline:
                raise AudioError('原厂服务未就绪。')
        future = client.call_async(request)
        while not future.done():
            self.spin(cancellable=cancellable)
            if time.monotonic() >= deadline:
                raise AudioError('原厂请求超时，执行结果未知；未重发。')
        result = future.result()
        if result is None or result.header.header.code != 0 or result.header.status.value not in (0, 1):
            raise AudioError('原厂请求未成功。')
        return result

    @contextmanager
    def prepared_audio(self, path, checkpoint=None):
        if audio_host() == 'local':
            yield Path(path).resolve()
            return
        from .device import host_command
        def run(args, timeout):
            # 文件准备不阻塞宿主的 ROS 线程；独立任务线程中执行。
            if checkpoint:
                checkpoint()
            result = subprocess.run(args, capture_output=True, timeout=timeout)
            if result.returncode:
                raise AudioError('音频文件传输或清理失败。')
            return result.stdout.decode().strip()
        directory = run(host_command(self.runtime, 'mktemp -d /tmp/diangetai-transfer.XXXXXXXX'), 12)
        import re
        if not re.fullmatch(r'/tmp/diangetai-transfer\.[A-Za-z0-9]{8}', directory):
            raise AudioError('原厂媒体临时路径无效。')
        remote = directory + '/audio.wav'
        try:
            run(['scp', *ssh_options(self.runtime), str(path), audio_host() + ':' + remote], 45)
            yield Path(remote)
        finally:
            # 清理不被任务取消跳过，且只删除自己刚创建的目录。
            result = subprocess.run(host_command(self.runtime, 'rm -f ' + shlex.quote(remote)
                                    + ' && rmdir ' + shlex.quote(directory)), capture_output=True, timeout=15)
            if result.returncode:
                raise AudioError('本任务音频文件清理未确认。')

    def close(self):
        self.executor.shutdown(timeout_sec=2)
        self.node.destroy_node()
        self.ros.shutdown(context=self.context)
