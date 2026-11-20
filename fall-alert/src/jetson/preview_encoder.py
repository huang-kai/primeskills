"""Optional H.264 preview. Losing the display must not interrupt family monitoring."""

import subprocess
import time


H264_COMMAND = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'warning', '-f', 'rawvideo',
 '-pixel_format', 'bgr24', '-video_size', '480x272', '-framerate', '10', '-i', 'pipe:0', '-an',
 '-vf', 'format=yuv420p', '-c:v', 'libx264', '-threads', '2', '-preset', 'ultrafast', '-tune',
 'zerolatency', '-g', '8', '-bf', '0', '-b:v', '550k', '-mpegts_flags', '+resend_headers',
 '-muxdelay', '0', '-muxpreload', '0', '-flush_packets', '1', '-f', 'mpegts',
 'tcp://10.1.1.100:38473?tcp_nodelay=1&timeout=3000000']


class PreviewEncoder:
    def __init__(self, command, log, optional):
        self.command = command
        self.log = log
        self.optional = optional
        self.process = None
        self.retry_at = 0.

    def ready(self):
        if self.process and self.process.poll() is not None:
            self._lost()
        if self.process is None and time.monotonic() >= self.retry_at:
            try:
                self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stderr=self.log)
            except OSError:
                if not self.optional:
                    raise
                self._lost()
        return self.process is not None

    def write(self, frame):
        try:
            self.process.stdin.write(frame)
            self.process.stdin.flush()
        except (BrokenPipeError, OSError):
            self._lost()

    def _lost(self):
        self.close()
        if not self.optional:
            raise RuntimeError('H.264 preview connection lost')
        self.retry_at = time.monotonic() + 5
        print('PREVIEW_DISCONNECTED', flush=True)

    def close(self):
        process, self.process = self.process, None
        if process is None:
            return
        try:
            process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
