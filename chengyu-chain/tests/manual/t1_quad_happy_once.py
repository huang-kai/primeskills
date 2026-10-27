#!/usr/bin/env python3
"""开发验收：T1-2X 单次四足开心模式与表情，可包含开局提示和起身。

必须传入实际游戏 runtime 目录并取得现场单次许可。--include-stand 复用开局
提示及坐趴起身流程；不会变形、改验收配置或管理原厂 Agent。
"""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / '_deps')]

from chengyu_game.robot.celebration import Celebration
from chengyu_game.robot.transport import Robot
from chengyu_game.support import AudioError, Cancelled, SetupError, cancellation, exclusive, frontend_lock


class QuadHappyTrial(Celebration):
    def __init__(self, robot, runtime, *, include_step=False, include_stand=False):
        # 单次现场许可与永久验收配置分开；不设置任何 commissioned/verified 标记。
        super().__init__(robot, runtime, {'celebration': {'enabled': True, 'quad_seconds': 1.2}})
        self.include_step = include_step
        self.include_stand = include_stand

    def _check_stand_permission(self, mood):
        if not (self.include_stand is True and mood == 'happy'
                and (self.device.series, self.device.sku) == ('T1', 'T1-2X')):
            raise AudioError('本次现场许可未包含开局起身。')

    def _prepare_motion(self, mood='happy', *, before_stand=None):
        try:
            if mood != 'happy' or (self.device.series, self.device.sku) != ('T1', 'T1-2X'):
                raise AudioError('本工具仅支持 T1-2X 四足开心反馈。')
            name, info = self.prepare_stand(mood, before_stand=before_stand)
            if name != 'T1_quad' or info.current_action.value not in (101, 102):
                raise AudioError('须先由操作者通过原厂方式准备四足稳定站立。')
            from aimdk_msgs.msg import McAction
            expected = {'QUADRUPED_STAND_DEFAULT': 101, 'QUADRUPED_LOCOMOTION_DEFAULT': 102,
                        'QUADRUPED_LOCOMOTION_PLEASURE': 114}
            if any(getattr(McAction, key, None) != value for key, value in expected.items()):
                raise AudioError('当前消息包的四足站立/开心枚举不匹配。')
            task_id = getattr(getattr(self.state, 'task_info', None), 'task_id', None)
            if type(task_id) is not int or task_id < 0:
                raise AudioError('当前运控任务归属未知。')
            route = self._prepare_quad_route(info.current_action.value, allow_step=self.include_step is True)
            self.record['motion'] = {'profile': 'T1_quad', 'initial_action': info.current_action.value,
                                     'task_id': task_id, 'owner_seen': True, 'stage': 'prepared',
                                     'boot_id': self._boot_id(), 'mood': 'happy', 'quad_route': route}
            self._save()
            self.robot.spin(0)
            self._safe(stationary=True)
            self.motion_status = {'status': 'ready', 'profile': 'T1_quad'}
            return True
        except AudioError as error:
            # 现场单次测试不采用游戏的“动作不可用但继续表情”降级路径。
            raise SetupError('验收前提未满足，未提交开心反馈：' + str(error)) from None

    def _mode(self, action, stage, cleanup=False):
        if self.include_stand and action == 101 and stage == 'standing' and not cleanup:
            return super()._mode(action, stage, cleanup)
        route = self.record['motion'].get('quad_route', {})
        expected = (route.get('return', []) if cleanup and stage == 'return'
                    else route.get('enter', []) if not cleanup and stage == 'mode' else [])
        if action not in (101, 102, 114, 121) or action not in expected:
            raise SetupError('单次验收禁止白名单之外的模式切换。')
        return super()._mode(action, stage, cleanup)


def run_once(runtime, *, confirmed=False, include_step=False, include_stand=False, config_path=None):
    if confirmed is not True:
        raise SetupError('须明确确认现场空间、急停路径及一次四足开心反馈许可。')
    if include_stand and config_path is None:
        raise SetupError('开局起身验收须指定游戏配置以播报安全提示。')
    runtime = Path(runtime).expanduser().resolve()
    with cancellation() as check, exclusive(runtime), frontend_lock(), ExitStack() as stack:
        if any((runtime / name).exists() for name in ('media.json', 'effects.json', 'agent_mode.json', 'session.json')):
            raise SetupError('游戏存在未确认记录，未启动单次验收。')
        robot = Robot(runtime, check)
        stack.callback(robot.close)
        notice = None
        if include_stand:
            from chengyu_game.configuration import load_config
            from chengyu_game.providers import Models
            from chengyu_game.robot.native_media import NativeMedia
            from chengyu_game.runtime import STAND_NOTICE
            from chengyu_game.session import synthesize
            models = Models(load_config(config_path))
            media = NativeMedia(robot, runtime / 'media.json')
            stack.callback(media.close)
            notice = lambda: media.play(synthesize(models, STAND_NOTICE))
        effects = QuadHappyTrial(robot, runtime, include_step=include_step, include_stand=include_stand)
        stack.callback(effects.close)
        effects.start()
        if include_stand:
            effects.prepare_for_game(motion_enabled=True, before_stand=notice)
        return effects.celebrate('win')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-dir', type=Path, required=True, help='实际游戏使用的运行目录，不能改用空目录绕过锁')
    parser.add_argument('--confirm-clear-space-estop-one-trial', action='store_true', required=True,
                        help='确认现场安全、可急停，且已批准本次指定的起身、开心和归位范围')
    parser.add_argument('--include-step', action='store_true',
                        help='本次现场许可明确包含进入及退出原地踏步模式 121')
    parser.add_argument('--include-stand', action='store_true', help='本次现场许可包含播报后由坐趴姿起身')
    parser.add_argument('--config', type=Path, help='开局提示使用的游戏私有模型配置')
    args = parser.parse_args(argv)
    try:
        result = run_once(args.runtime_dir, confirmed=args.confirm_clear_space_estop_one_trial,
                          include_step=args.include_step, include_stand=args.include_stand, config_path=args.config)
        result = {'status': 'success', **result, 'message': '单次反馈及恢复已确认；幅度、表情效果仍须现场验收。'}
        code = 0
    except (SetupError, Cancelled) as error:
        result = {'status': 'failed', 'message': str(error)}
        code = 1
    result['record_retained'] = (args.runtime_dir.expanduser() / 'effects.json').exists()
    print(json.dumps(result, ensure_ascii=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
