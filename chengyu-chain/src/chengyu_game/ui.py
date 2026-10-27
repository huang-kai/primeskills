"""独立终端向导，沿用 Buddy 的界面风格，不导入 Buddy 的代码或配置。"""
import getpass
import os
import select
import shutil
import sys
import termios
import tty
import unicodedata
import warnings

from .configuration import ConfigError


class Console:
    def __init__(self):
        if not sys.stdin.isatty():
            raise ConfigError('配置引导需要交互终端；通过 SSH 运行时请使用 ssh -t。')

    def info(self, message):
        print(message, file=sys.stderr, flush=True)

    def interactive(self):
        return sys.stdin.isatty() and sys.stderr.isatty() and os.environ.get('TERM', 'dumb') != 'dumb'

    def paint(self, text, color):
        return '\033[' + color + 'm' + text + '\033[0m' if self.interactive() and 'NO_COLOR' not in os.environ else text

    def header(self, title, subtitle=''):
        self.info('\n' + self.paint('╭─ ' + title, '1;36'))
        if subtitle:
            self.info('│  ' + subtitle)
        self.info(self.paint('╰' + '─' * 38, '36'))

    def ask(self, label, default='', secret=False):
        while True:
            suffix = ' [回车保留]' if secret and default else (f' [{default}]' if default else '')
            prompt = self.paint('◆ ', '36') + label + suffix + '：'
            if secret:
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter('error', getpass.GetPassWarning)
                        value = getpass.getpass(prompt, stream=sys.stderr).strip()
                except getpass.GetPassWarning:
                    raise ConfigError('当前终端无法隐藏 API Key，请在交互终端中重新配置。') from None
            else:
                print(prompt, end='', file=sys.stderr, flush=True)
                value = input().strip()
            if value or default:
                return value or default
            self.info('不能为空，请重新输入。')

    def confirm(self, label, default=True):
        return self.choose(label, ['是，继续', '否，取消'], 0 if default else 1) == 0

    def choose(self, label, choices, default=0):
        if not choices or type(default) is not int or not 0 <= default < len(choices):
            raise ValueError('选择列表或默认项无效')
        if self.interactive():
            return self._select(label, choices, default)
        self.info(label)
        for index, choice in enumerate(choices, 1):
            self.info(f'  {index}. {choice}')
        while True:
            answer = self.ask('请选择', str(default + 1))
            if answer.isdigit() and 1 <= int(answer) <= len(choices):
                return int(answer) - 1
            self.info('请输入列表中的序号。')

    @staticmethod
    def clip(text, width):
        result, used = '', 0
        for char in str(text):
            if not char.isprintable():
                char = ' '
            size = 0 if unicodedata.combining(char) else (2 if unicodedata.east_asian_width(char) in ('W', 'F') else 1)
            if used + size > width:
                break
            result += char
            used += size
        return result

    @staticmethod
    def key(fd):
        value = os.read(fd, 1)
        if not value or value == b'\x04':
            raise EOFError
        if value == b'\x03':
            raise KeyboardInterrupt
        if value == b'\x1b':
            # SSH 可能把方向键拆成多次传输。
            while len(value) < 8 and select.select([fd], [], [], .2)[0]:
                tail = os.read(fd, 1)
                if not tail:
                    raise EOFError
                value += tail
                if len(value) >= 3 and (value[-1:].isalpha() or value.endswith(b'~')):
                    break
        return value

    def _select(self, label, choices, cursor):
        fd = sys.stdin.fileno()
        previous = termios.tcgetattr(fd)
        rows = 0

        def clear():
            if rows:
                sys.stderr.write(f'\033[{rows}A\r\033[J')

        try:
            tty.setcbreak(fd)
            sys.stderr.write('\033[?25l')
            while True:
                clear()
                size = shutil.get_terminal_size((100, 30))
                visible = min(len(choices), max(1, size.lines - 4))
                start = min(max(0, cursor - visible + 1), len(choices) - visible)
                width = max(1, size.columns - 2)
                lines = [self.paint(self.clip('◆ ' + label, width), '1;36')]
                for index in range(start, start + visible):
                    text = ('❯ ● ' if index == cursor else '  ○ ') + str(choices[index])
                    lines.append(self.paint(self.clip(text, width), '36' if index == cursor else '2'))
                hint = '↑↓ 移动 · Enter 确认 · Ctrl+C 取消'
                if visible < len(choices):
                    hint += f' · {cursor + 1}/{len(choices)}'
                lines.append(self.paint(self.clip(hint, width), '2'))
                sys.stderr.write('\n'.join(lines) + '\n')
                sys.stderr.flush()
                rows = len(lines)
                key = self.key(fd)
                if key in (b'\x1b[A', b'\x1bOA', b'k'):
                    cursor = (cursor - 1) % len(choices)
                elif key in (b'\x1b[B', b'\x1bOB', b'j'):
                    cursor = (cursor + 1) % len(choices)
                elif key in (b'\r', b'\n'):
                    clear()
                    rows = 0
                    self.info(self.paint('✓ ' + label, '32') + ' · ' + str(choices[cursor]))
                    return cursor
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, previous)
            sys.stderr.write('\033[?25h')
            sys.stderr.flush()
