#!/usr/bin/env python3
"""只读复现：多个冷客户端分别查询两次 GetMotionList，绝不调用播放或运动接口。"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time


def probe(skill_root):
    sys.path[:0] = [str(skill_root / 'src'), str(skill_root / '_deps')]
    from aimdk_msgs.srv import GetMotionList
    from chengyu_game.robot.transport import Robot
    robot = Robot(Path('/tmp/chengyu-readonly-probe'), lambda: None)
    client = robot.node.create_client(GetMotionList, '/aimdk_5Fmsgs/srv/GetMotionList')
    try:
        print(json.dumps({'stage': 'client_created', 'wall_time_ns': time.time_ns()}), flush=True)
        for attempt in range(2):
            request = GetMotionList.Request()
            request.header.header.stamp = robot.node.get_clock().now().to_msg()
            started = time.monotonic()
            def submitted():
                print(json.dumps({'stage': 'submitted', 'attempt': attempt,
                    'ready_seconds': round(time.monotonic() - started, 4),
                    'wall_time_ns': time.time_ns()}), flush=True)
            try:
                response = robot.call(client, request, timeout=6, before_submit=submitted)
                result = {'stage': 'response', 'attempt': attempt,
                          'code': response.header.header.code, 'status': response.header.status.value}
            except Exception as error:
                result = {'stage': 'failed', 'attempt': attempt,
                          'error_type': type(error).__name__, 'message': str(error)}
            result['seconds'] = round(time.monotonic() - started, 4)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        robot.node.destroy_client(client)
        robot.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skill-root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--trials', type=int, choices=range(1, 6), default=3)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        probe(args.skill_root.resolve())
        return
    for trial in range(args.trials):
        print(json.dumps({'trial': trial}), flush=True)
        try:
            result = subprocess.run([sys.executable, '-u', str(Path(__file__).resolve()), '--worker',
                                     '--skill-root', str(args.skill_root.resolve())],
                                    capture_output=True, text=True, timeout=30)
            print(result.stdout[:16000], end='', flush=True)
            print(json.dumps({'exit_code': result.returncode, 'stderr_bytes': len(result.stderr)}), flush=True)
        except subprocess.TimeoutExpired:
            print(json.dumps({'whole_process_timeout': True}), flush=True)


if __name__ == '__main__':
    main()
