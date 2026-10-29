#!/usr/bin/env python3
"""独立点歌 CLI：配置、只读上下文、播放及本技能操作恢复。"""
import argparse
import json
import logging
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(ROOT / '_deps')]


class Console:
    def info(self, text):
        print(text, file=sys.stderr)

    def ask(self, label, default=''):
        print(f'{label} [{default}]：', end='', file=sys.stderr, flush=True)
        return input().strip() or default

    def confirm(self, label, default=True):
        return self.ask(label + ' (y/n)', 'y' if default else 'n').lower() in ('y', 'yes', '是')

    def choose(self, label, choices):
        self.info(label)
        for index, choice in enumerate(choices):
            self.info(f'{index + 1}. {choice}')
        while True:
            value = self.ask('选择', '1')
            if value.isdigit() and 1 <= int(value) <= len(choices):
                return int(value) - 1


def main(argv=None):
    from src import skill
    from src.recovery import status, recover, exclusive
    from src.support import Cancelled, SetupError, cancellation, config_path, read_config, runtime_dir, skill_data, write_config
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--runtime-dir', type=Path)
    sub = parser.add_subparsers(dest='command')
    for name in ('configure', 'context', 'status', 'recover'):
        sub.add_parser(name)
    for name in ('play', 'check-play'):
        play = sub.add_parser(name)
        play.add_argument('--artist', default='')
        play.add_argument('--song', default='')
        play.add_argument('--simulate', action='store_true')
        play.add_argument('--seconds', type=int)
        play.add_argument('--effects', action='store_true', default=None)
    args = parser.parse_args(argv)
    if args.check:
        skill.settings({})
        print(json.dumps({'status': 'success', 'message': 'diangetai 独立入口与默认配置校验通过。'}, ensure_ascii=False))
        return 0
    if not args.command:
        parser.print_help()
        return 0
    runtime = None
    try:
        if args.data_dir:
            os.environ['DIANGETAI_DATA'] = str(args.data_dir.expanduser())
        data = skill_data()
        runtime = runtime_dir(data, args.runtime_dir)
        path = config_path(data, args.config)
        if args.command in ('status', 'recover'):
            result = status(runtime) if args.command == 'status' else recover(runtime)
        else:
            config = read_config(path)
            if args.command == 'configure':
                from src.setup import setup
                with cancellation(immediate=True):
                    result = setup(Console(), data, config)
                if result is None:
                    result = {'status': 'failed', 'message': '配置尚未完成。'}
                else:
                    write_config(path, result)
                    result = {'status': 'success', 'message': '点歌台配置完成。'}
            elif args.command == 'context':
                result = dict(skill.settings(config), resources_released=status(runtime)['resources_released'])
            else:
                if args.seconds is not None:
                    config['play_seconds'] = args.seconds
                if args.effects is not None:
                    config['music_effects'] = args.effects
                if args.command == 'check-play':
                    result = skill.check_play(args.artist, args.song, config=config,
                                              runtime=runtime, simulate=args.simulate)
                    state = status(runtime)
                    if not state['resources_released']:
                        result = state
                else:
                    with cancellation() as check, exclusive(runtime, ('command.lock',)):
                        try:
                            result = skill.play(args.artist, args.song, config=config,
                                                runtime=runtime, check=check, simulate=args.simulate)
                        finally:
                            skill.shutdown()
                result['resources_released'] = status(runtime)['resources_released']
    except (Cancelled, KeyboardInterrupt):
        result = {'status': 'failed', 'message': '任务已取消。', 'cancelled': True}
    except SetupError as error:
        result = {'status': 'failed', 'message': str(error)}
    except Exception as error:
        logging.error('点歌失败：%s', type(error).__name__)
        result = {'status': 'failed', 'message': '点歌执行失败，结果未确认。', 'error_type': type(error).__name__}
    if runtime is not None:
        result.setdefault('resources_released', status(runtime)['resources_released'])
    else:
        result.setdefault('resources_released', False)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] == 'success' else 1


if __name__ == '__main__':
    from src.robot.native_sdk import bootstrap
    bootstrap()
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    raise SystemExit(main())
