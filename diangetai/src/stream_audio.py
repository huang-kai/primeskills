"""歌曲流播放器：提示语继续走SDK，只替换歌曲播放。"""
import contextlib
import json
from pathlib import Path
import queue
import shlex
import subprocess
import threading
import time

from primebuddy.voice_support.robot_audio import AudioError, SOC0, ssh_options, check_cancel
from .qqmusic_client import validate_url, validate_duration
from .config import playback_seconds


class RemoteSong:
    def __init__(self, runtime, url, seconds, play_seconds=0):
        validate_url(url)
        validate_duration(seconds, seconds)
        playback_seconds({'play_seconds': play_seconds})
        self.events = queue.Queue()
        self.started = None
        self.ended = False
        self.end_reason = None
        self.closed = False
        self.error = False
        self.ready = False
        source = Path(__file__).with_name('gst_player_process.py').read_text()
        self.proc = subprocess.Popen(['ssh', '-T', *ssh_options(runtime), SOC0,
                                      'python3 -u -c ' + shlex.quote(source)],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True)
        def read():
            for line in self.proc.stdout:
                try:
                    self.events.put(json.loads(line))
                except ValueError:
                    self.events.put({'event': 'error'})
        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()
        try:
            self.send(json.dumps({'url': url, 'seconds': seconds, 'play_seconds': play_seconds}))
        except BaseException:
            self.stop()
            raise

    def send(self, command):
        self.proc.stdin.write(command + '\n')
        self.proc.stdin.flush()

    def update(self):
        while True:
            try:
                item = self.events.get_nowait()
            except queue.Empty:
                break
            event = item['event']
            if event == 'timing':
                print('STAGE_TIME', json.dumps({'stage': item['stage'], 'seconds': item['seconds']}, ensure_ascii=False), flush=True)
            elif event == 'ready':
                self.ready = True
            elif event == 'started':
                self.started = time.monotonic()
            elif event == 'ended':
                self.ended = True
                self.end_reason = item.get('reason', 'natural')
            elif event == 'closed':
                self.closed = item.get('confirmed', False)
            elif event == 'error':
                self.error = True

    def wait_ready(self, checkpoint):
        deadline = time.monotonic() + 45
        while True:
            check_cancel()
            if checkpoint:
                checkpoint()
            self.update()
            if self.error or self.proc.poll() is not None or time.monotonic() > deadline:
                raise AudioError('歌曲流预缓冲或时长校验失败。')
            if self.ready:
                return
            time.sleep(.05)

    def stop(self):
        # 只关闭本曲管线；不调用原厂全局停止。stdin断开亦触发远端finally。
        if self.proc.poll() is None:
            try:
                self.send('stop')
            except (OSError, ValueError):
                pass
            try:
                self.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
                self.proc.wait(timeout=5)
        self.reader.join(timeout=1)
        self.update()
        self.proc.stdin.close()
        self.proc.stdout.close()
        if not self.closed:
            raise AudioError('歌曲播放器关闭未确认。')


class StreamAudio:
    """把单曲流反馈交给已有会话；SDK语音接口保持原样。"""
    def __init__(self, robot, runtime):
        self.robot, self.runtime = robot, runtime
        self.song = None

    def __getattr__(self, name):
        return getattr(self.robot, name)

    @contextlib.contextmanager
    def prepared_song(self, url, seconds, checkpoint=None):
        song = RemoteSong(self.runtime, url, seconds)
        try:
            song.wait_ready(checkpoint)
            yield song, None
        finally:
            if not song.closed:
                song.stop()

    def start_file(self, local, remote, checkpoint=None):
        if not isinstance(local, RemoteSong):
            self.song = None
            return self.robot.start_file(local, remote, checkpoint=checkpoint)
        if checkpoint:
            checkpoint()
        # 提示语已结束才开歌；失效结果不发送play。
        self.song = local
        self.song.send('play')
        self.requested_at = time.monotonic()

    @property
    def playback_submitted(self):
        return self.song is not None or self.robot.playback_submitted

    @property
    def playback_started(self):
        if self.song is None:
            return self.robot.playback_started
        self.song.update()
        return self.song.started is not None and not self.song.ended and not self.song.closed

    def poll_playback(self, max_seconds=None):
        if self.song is None:
            return self.robot.poll_playback(max_seconds)
        song = self.song
        song.update()
        if song.error or (song.proc.poll() is not None and not song.ended):
            raise AudioError('歌曲流播放失败。')
        if song.ended:
            self.stop_owned()
            return {'playback_feedback': 'stream_natural_end'}
        if song.started is not None and max_seconds is not None and time.monotonic() - song.started >= max_seconds:
            seconds = time.monotonic() - song.started
            self.stop_owned()
            return {'seconds': round(seconds, 3), 'playback_feedback': 'stream_stopped_at_limit'}
        if song.started is None and time.monotonic() - self.requested_at > 15:
            raise AudioError('未收到歌曲流开始反馈。')

    def stop_owned(self):
        if self.song is None:
            return self.robot.stop_owned()
        self.song.stop()
        self.song = None
