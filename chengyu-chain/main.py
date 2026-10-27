#!/usr/bin/env python3
"""独立成语接龙：配置、模型检查、前台游戏与本应用恢复。"""
import argparse
import json
import logging
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / '_deps')]


from chengyu_game.ui import Console


def main(argv=None):
    from chengyu_game.configuration import configure, load_config, save_config, ConfigError
    from chengyu_game.providers import Models, ProviderError
    from chengyu_game.support import AudioError, SetupError, Cancelled, cancellation, private_dir
    from chengyu_game import runtime as app
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--runtime-dir', type=Path)
    sub = parser.add_subparsers(dest='command')
    for name in ('configure', 'context', 'status', 'recover', 'check-models', 'prepare-audio'):
        sub.add_parser(name)
    motion = sub.add_parser('motion', help='查看或保存下次开局的身体动作开关；不会立即发送动作')
    motion.add_argument('setting', choices=('status', 'on', 'off'), nargs='?', default='status')
    for name in ('play', 'check-play'):
        command = sub.add_parser(name)
        command.add_argument('--simulate', action='store_true')
        command.add_argument('--hosted', action='store_true')
        command.add_argument('--manage-agent', action='store_true')
        command.add_argument('--motion', choices=('on', 'off'), help='只覆盖本次运行的身体动作开关')
    args = parser.parse_args(argv)
    if args.check:
        from chengyu_game.game import Game
        Game(lambda _: {})
        print(json.dumps({'status': 'success', 'message': '成语接龙独立入口检查通过。'}, ensure_ascii=False))
        return 0
    if not args.command:
        parser.print_help()
        return 0
    runtime = None
    try:
        data = private_dir(args.data_dir or os.environ.get('CHENGYU_DATA', Path.home() / '.chengyu-chain'))
        runtime = private_dir(args.runtime_dir or data / 'runtime')
        path = (args.config or data / 'config.yaml').expanduser()
        if args.command == 'status':
            result = app.status(runtime)
        elif args.command == 'recover':
            result = app.recover(runtime, path)
        elif getattr(args, 'simulate', False):
            from chengyu_game.simulation import simulate
            result = simulate()
        elif args.command == 'configure':
            config = configure(Console(), path, buddy_home=os.environ.get('PRIMEBUDDY_HOME'))
            try:
                app.cache_ending(Models(config), data)
            except ProviderError as error:
                raise ProviderError('游戏配置已保存，但结束播报 TTS 生成失败：' + str(error)
                                    + '。请通过 configure 核对模型与音色，'
                                    + '修正后使用相同路径参数运行 prepare-audio 重试。') from None
            result = {'status': 'success', 'message': '独立模型配置与离线结束播报已保存。'}
        else:
            config = load_config(path)
            if args.command == 'motion':
                settings = config.setdefault('celebration', {})
                if not isinstance(settings, dict):
                    raise ConfigError('celebration 配置须为对象。')
                if args.setting != 'status':
                    if not app.status(runtime)['resources_released']:
                        raise SetupError('请先结束游戏并确认资源释放，再修改下一次开局的动作设置。')
                    settings['motion_enabled'] = args.setting == 'on'
                    if args.setting == 'on':
                        settings['enabled'] = True
                    save_config(path, config)
                result = {'status': 'success', 'motion_enabled': settings.get('motion_enabled') is True,
                          'commissioned': settings.get('commissioned') is True,
                          'message': '动作设置已保存，下一次开局生效；仍须通过对应机型的动作验收与站立检查。'
                          if args.setting != 'status' else '已读取身体动作设置。'}
            elif args.command == 'context':
                result = {'status': 'success', 'initial_level': '初级', 'inactivity_seconds': 120,
                          'answer_seconds': 30, 'win_targets': [5, 7, 10],
                          'motion_enabled': config.get('celebration', {}).get('motion_enabled') is True,
                          'exclusive': True, 'resources_released': app.status(runtime)['resources_released'],
                          'message': '前台成语接龙应用；退出后才能使用其他技能。'}
            elif args.command == 'check-models':
                checks = Models(config).check()
                result = {'status': 'success' if all(x['status'] == 'success' for x in checks.values()) else 'failed',
                          'checks': checks}
            elif args.command == 'prepare-audio':
                app.cache_ending(Models(config), data)
                result = {'status': 'success', 'message': '离线结束播报已准备。'}
            elif args.command == 'check-play':
                result = app.check_play(config, data, runtime)
            else:
                with cancellation() as check:
                    enabled = None if args.motion is None else args.motion == 'on'
                    result = app.play(config, data, runtime, check, hosted=args.hosted,
                                      manage_agent=args.manage_agent, motion_enabled=enabled)
    except (KeyboardInterrupt, Cancelled):
        result = {'status': 'failed', 'message': '成语接龙已取消。', 'cancelled': True}
    except (ConfigError, SetupError, ProviderError) as error:
        result = {'status': 'failed', 'message': str(error)}
    except Exception as error:
        logging.error('成语接龙失败：%s', type(error).__name__)
        result = {'status': 'failed', 'message': '游戏执行失败，结果未确认。', 'error_type': type(error).__name__}
    released = app.status(runtime)['resources_released'] if runtime is not None else False
    result['resources_released'] = released
    if not released:
        result['status'] = 'failed'
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get('status') == 'success' else 1


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    raise SystemExit(main())
