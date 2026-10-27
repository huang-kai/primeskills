"""离线终端测试：方向键、密码隐藏、取消后终端恢复。"""
from contextlib import redirect_stderr, redirect_stdout
import io
import getpass
import os
from pathlib import Path
import pty
import select
import signal
import subprocess
import sys
import termios
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from chengyu_game.configuration import ConfigError
from chengyu_game.ui import Console


class ConsoleTests(unittest.TestCase):
    def test_nonterminal_cannot_fall_back_to_echoing_secret(self):
        with patch('sys.stdin.isatty', return_value=False), self.assertRaises(ConfigError):
            Console()

    def test_secret_default_is_never_printed_and_enter_retains_it(self):
        ui = Console.__new__(Console)
        with patch.object(ui, 'interactive', return_value=False), \
                patch('chengyu_game.ui.getpass.getpass', return_value='') as read:
            result = ui.ask('API Key（已配置）', 'test-private-value', secret=True)
        self.assertEqual(result, 'test-private-value')
        self.assertNotIn('test-private-value', read.call_args.args[0])
        self.assertIn('回车保留', read.call_args.args[0])

    def test_failed_echo_control_does_not_accept_unhidden_secret(self):
        ui = Console.__new__(Console)
        with patch.object(ui, 'interactive', return_value=False), \
                patch('chengyu_game.ui.getpass.getpass', side_effect=getpass.GetPassWarning), \
                self.assertRaises(ConfigError):
            ui.ask('API Key', secret=True)

    def test_all_ui_text_uses_stderr_leaving_stdout_for_json(self):
        ui = Console.__new__(Console)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), \
                patch.object(ui, 'interactive', return_value=False), patch('builtins.input', return_value=''):
            ui.header('成语接龙', '模型配置')
            self.assertEqual(ui.ask('Host', 'example'), 'example')
            self.assertEqual(ui.choose('模型', ['one', 'two'], 1), 1)
        self.assertEqual(out.getvalue(), '')
        self.assertIn('╭─ 成语接龙', err.getvalue())
        self.assertIn('╰', err.getvalue())

    def test_narrow_terminal_clips_chinese_by_display_columns(self):
        self.assertEqual(Console.clip('龙泡泡 ABC', 7), '龙泡泡 ')
        self.assertEqual(Console.clip('甲\033乙', 4), '甲 ')


class TerminalTests(unittest.TestCase):
    def start(self, expression, term='xterm-256color', size=(100, 30)):
        self.master, self.slave = pty.openpty()
        self.before = termios.tcgetattr(self.slave)
        env = dict(os.environ, PYTHONPATH=str(ROOT / 'src'), TERM=term,
                   COLUMNS=str(size[0]), LINES=str(size[1]))
        code = 'import json\nfrom chengyu_game.ui import Console\nui = Console()\n'
        code += 'try:\n    result = ' + expression + '\n    print("RESULT", json.dumps(result))\n'
        code += 'except (KeyboardInterrupt, EOFError):\n    print("CANCELLED")\n'
        self.process = subprocess.Popen([sys.executable, '-c', code], stdin=self.slave,
                                        stdout=self.slave, stderr=self.slave, env=env,
                                        start_new_session=True)
        self.output = b''
        self.addCleanup(self.close)

    def close(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=3)
        os.close(self.master)
        os.close(self.slave)

    def until(self, token):
        deadline = time.monotonic() + 4
        while token not in self.output:
            if time.monotonic() > deadline:
                self.fail('未出现预期输出：' + repr(self.output))
            if select.select([self.master], [], [], .1)[0]:
                self.output += os.read(self.master, 65536)

    def restored(self):
        self.process.wait(timeout=3)
        after = termios.tcgetattr(self.slave)
        after[3] &= ~getattr(termios, 'PENDIN', 0)
        self.before[3] &= ~getattr(termios, 'PENDIN', 0)
        self.assertEqual(after, self.before)
        self.assertIn(b'\x1b[?25h', self.output)

    def test_arrows_choose_and_restore_terminal(self):
        self.start("ui.choose('Voice', ['first', 'second'])")
        self.until(b'second')
        os.write(self.master, b'\x1b[B\r')
        self.until(b'RESULT 1')
        self.restored()

    def test_enter_keeps_nonfirst_default(self):
        self.start("ui.choose('Voice', ['first', 'second'], 1)")
        self.until(b'second')
        os.write(self.master, b'\r')
        self.until(b'RESULT 1')
        self.restored()

    def test_control_c_restores_terminal(self):
        self.start("ui.choose('Voice', ['first', 'second'])")
        self.until(b'second')
        self.process.send_signal(signal.SIGINT)
        self.until(b'CANCELLED')
        self.restored()

    def test_eof_restores_terminal(self):
        self.start("ui.choose('Voice', ['first', 'second'])")
        self.until(b'second')
        os.write(self.master, b'\x04')
        self.until(b'CANCELLED')
        self.restored()

    def test_dumb_terminal_retries_invalid_number_then_keeps_default(self):
        self.start("ui.choose('Voice', ['first', 'second'], 1)", term='dumb')
        self.until('请选择'.encode())
        os.write(self.master, b'99\n')
        self.until('请输入列表中的序号'.encode())
        os.write(self.master, b'\n')
        self.until(b'RESULT 1')
        self.assertNotIn(b'\x1b', self.output)

    def test_scrolls_to_voice_beyond_first_page(self):
        self.start("ui.choose('Voice', ['voice-' + str(i) for i in range(12)])", size=(24, 8))
        self.until(b'voice-3')
        os.write(self.master, b'\x1b[B' * 8 + b'\r')
        self.until(b'RESULT 8')
        self.restored()

    def test_new_secret_is_hidden_in_real_terminal(self):
        self.start("len(ui.ask('API Key', secret=True))")
        self.until(b'API Key')
        os.write(self.master, b'never-show-me\n')
        self.until(b'RESULT 13')
        self.assertNotIn(b'never-show-me', self.output)


if __name__ == '__main__':
    unittest.main()
