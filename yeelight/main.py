#!/usr/bin/env python3
"""独立易来 CLI：配置、查询真实目标和执行开关，不需要 PrimeBuddy。"""
import argparse
import json
import logging
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


def power_value(value):
    if value.lower() in ('true', 'on'):
        return True
    if value.lower() in ('false', 'off'):
        return False
    raise argparse.ArgumentTypeError('power 须为 true/false 或 on/off')


def main(argv=None):
    from src import skill
    from src.configuration import Cancelled, SkillError, cancellation, paths, read_config, write_config
    from src.business.feedback import LightError
    from src.business.intent import IntentError
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--runtime-dir', type=Path)
    sub = parser.add_subparsers(dest='command')
    sub.add_parser('configure')
    sub.add_parser('context')
    for name in ('set-power', 'check-power', 'preview-power'):
        control = sub.add_parser(name)
        control.add_argument('--power', type=power_value, required=True)
        control.add_argument('--scope', choices=('device', 'room', 'home'), required=True)
        control.add_argument('--room', default='')
        control.add_argument('--device', default='')
        control.add_argument('--simulate', action='store_true')
        if name == 'preview-power':
            control.add_argument('--text', required=True)
    args = parser.parse_args(argv)
    if args.check:
        result = {'status': 'success', 'message': 'yeelight 独立入口与默认配置校验通过。'}
    elif not args.command:
        parser.print_help()
        return 0
    else:
        try:
            path, data, _ = paths(args.config, args.data_dir, args.runtime_dir)
            config = read_config(path)
            if args.command == 'configure':
                from src.setup import setup
                with cancellation(immediate=True):
                    current = setup(Console(), data, config)
                if current is None:
                    result = {'status': 'failed', 'message': '配置尚未完成。'}
                else:
                    write_config(path, current)
                    result = {'status': 'success', 'message': '易来配置完成。'}
            else:
                with cancellation() as check:
                    if args.command == 'context':
                        result = skill.context(config, check)
                    elif args.command == 'preview-power':
                        result = skill.preview_power(args.power, args.scope, args.room, args.device,
                                                     args.text, config=config, check=check, simulate=args.simulate)
                    elif args.command == 'check-power':
                        result = skill.check_power(args.power, args.scope, args.room, args.device,
                                                   config=config, simulate=args.simulate)
                    else:
                        result = skill.set_power(args.power, args.scope, args.room, args.device,
                                                 config=config, check=check, simulate=args.simulate)
        except (Cancelled, KeyboardInterrupt):
            result = {'status': 'failed', 'message': '任务已取消，已发出的灯控状态仍须核实。', 'cancelled': True}
        except (SkillError, LightError, IntentError) as error:
            result = {'status': 'failed', 'message': str(error)}
        except Exception as error:
            logging.error('灯控失败：%s', type(error).__name__)
            result = {'status': 'failed', 'message': '灯控执行失败，结果未确认。', 'error_type': type(error).__name__}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] == 'success' else 1


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    raise SystemExit(main())
