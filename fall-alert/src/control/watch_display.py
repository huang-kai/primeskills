"""Manage the optional body display while the Jetson monitoring process is alive."""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

UI_OWNED = Path(os.environ.get('RUNTIME_DIRECTORY', '/run/t1-fall-display')) / 'ui-owned'
HEARTBEAT_SECONDS = 8


def refresh_ui_orientation():
    """Refresh after this UI instance has received the robot's posture."""
    try:
        invocation = subprocess.run(
            ['systemctl', 'show', 'display_ui.service', '-p', 'InvocationID', '--value'],
            check=True, capture_output=True, text=True, timeout=1).stdout.strip()
        if not re.fullmatch(r'[0-9a-f]{32}', invocation):
            return False
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            state = subprocess.run(
                ['journalctl', '_SYSTEMD_INVOCATION_ID=' + invocation, '--no-pager',
                 '-o', 'cat', '-n', '200'],
                capture_output=True, text=True, timeout=1).stdout
            if re.search(r'formState=[A-Z_]+\([12]\)', state):
                break
            time.sleep(.15)
        else:
            return False
        # The native default-emotion route does not refresh rotation on this firmware.
        payload = dict(emotion_ids=[1033], loop_count=1, priority=0, type='emotion')
        request = Request('http://127.0.0.1:18080/PlayEmotion',
                          data=json.dumps(payload).encode(),
                          headers={'Content-Type': 'application/json'})
        with urlopen(request, timeout=1) as response:
            result = json.load(response)
        return result.get('code') == 0 and result.get('success') is True
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print('UI_ORIENTATION_UNCONFIRMED', type(error).__name__, flush=True)
        return False


def restore_ui(force=False, marker=None):
    marker = UI_OWNED if marker is None else marker
    if force or marker.exists():
        subprocess.run(['sudo', '-n', 'systemctl', 'start', 'display_ui.service'],
                       check=True, timeout=12)
        print('UI_ORIENTATION_REFRESH', refresh_ui_orientation(), flush=True)
        marker.unlink(missing_ok=True)


class DisplaySession:
    def __init__(self):
        self.receiver = None
        self.restart_after = 0.
        self.want_preview = None
        self.last_heartbeat = 0.

    def heartbeat(self, msg):
        self.want_preview = msg.data
        self.last_heartbeat = time.monotonic()

    def stop_receiver(self):
        if self.receiver:
            self.receiver.terminate()
            try:
                self.receiver.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.receiver.kill()
                self.receiver.wait()
            self.receiver = None
        restore_ui()

    def update(self):
        now = time.monotonic()
        enabled = self.want_preview if now - self.last_heartbeat < HEARTBEAT_SECONDS else None
        if self.receiver and self.receiver.poll() is not None:
            self.receiver = None
            restore_ui()
            self.restart_after = now + 1.
        if enabled is True:
            if self.receiver is None and now >= self.restart_after:
                receiver = Path(__file__).with_name('receive.py')
                self.receiver = subprocess.Popen([sys.executable, '-u', str(receiver), '--managed'])
        else:
            self.stop_receiver()


def main():
    if '--restore' in sys.argv or '--restore-force' in sys.argv:
        restore_ui(force='--restore-force' in sys.argv)
        return
    import rclpy
    from rclpy.executors import ExternalShutdownException
    from std_msgs.msg import Bool
    rclpy.init()
    node = rclpy.create_node('t1_fall_watch_display')
    session = DisplaySession()
    start_at = time.monotonic()
    single_case = '--case-once' in sys.argv
    node.create_subscription(Bool, '/t1_fall_alert/preview_enabled', session.heartbeat, 1)
    try:
        while rclpy.ok():
            rclpy.spin_once(node, timeout_sec=.25)
            session.update()
            if single_case:
                # Release the transient node even if Jetson lost power before cleanup.
                deadline = session.last_heartbeat + HEARTBEAT_SECONDS if session.last_heartbeat else start_at + 60
                if time.monotonic() >= deadline:
                    break
    except ExternalShutdownException:
        if rclpy.ok():
            raise
    finally:
        session.stop_receiver()
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
