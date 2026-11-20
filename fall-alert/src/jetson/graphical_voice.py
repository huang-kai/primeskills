"""Pair the T1 graphical-programming voice session with its owning fall case."""
import json
import os
from pathlib import Path
import sys


OWNER = Path('/robot/persist/t1-fall-alert/voice-owner')


def owner(marker=OWNER):
    try:
        return os.readlink(marker)
    except FileNotFoundError:
        return None


class GraphicalVoice:
    def __init__(self, node, tag, marker=OWNER):
        from aimdk_msgs.srv import BusinessAgentControl
        self.node, self.tag, self.marker = node, tag, marker
        self.request_type = BusinessAgentControl.Request
        self.service = node.create_client(BusinessAgentControl, '/aimdk_5Fmsgs/srv/BusinessAgentControl')

    def switch(self, action):
        import rclpy
        if action == 'stop' and owner(self.marker) != self.tag:
            return
        if not self.service.wait_for_service(timeout_sec=10):
            raise RuntimeError('原厂语音接管服务不可用')
        req = self.request_type()
        req.header.header.stamp = self.node.get_clock().now().to_msg()
        req.agent_name = 'graphical_programming'
        req.payload_json = json.dumps({'action': action})
        if action == 'start':
            # Reserve before submitting: a lost reply or killed detector still needs cleanup.
            self.marker.symlink_to(self.tag)
        future = self.service.call_async(req)
        rclpy.spin_until_future_complete(self.node, future, timeout_sec=6)
        if not future.done() or future.result() is None:
            raise RuntimeError(f'未收到原厂语音 {action} 回执')
        header = future.result().header
        if header.header.code or header.status.reason or header.status.value != 1:
            if action == 'start':
                self.marker.unlink()
            raise RuntimeError(f'原厂拒绝语音 {action}，状态 {header.status.value}，原因 {header.status.reason}')
        if action == 'stop':
            self.marker.unlink()
        print('FAMILY_VOICE_MODE', action, self.tag, flush=True)


if __name__ == '__main__':
    import rclpy
    tag = sys.argv[1]
    if owner() == tag:
        rclpy.init(args=[])
        node = rclpy.create_node('fall_voice_restore')
        try:
            GraphicalVoice(node, tag).switch('stop')
        finally:
            node.destroy_node()
            rclpy.try_shutdown()
