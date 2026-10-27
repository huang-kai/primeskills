"""无设备、无网络的完整会话演练；模型响应是测试夹具，不是离线成语词库。"""
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import wave

from .providers import ProviderNetworkError
from .session import Session, TIMEOUT_TEXT
from .voice import VoiceInput


def _word(text, sounds, meaning):
    return {'text': text, 'pinyin': sounds.split(), 'meaning': meaning}


FIRST = _word('一心一意', 'yi1 xin1 yi1 yi4', '心思专一，专心做一件事。')
FIRST_ANSWER = _word('一马当先', 'yi1 ma3 dang1 xian1', '走在最前面，带头前进。')
SECOND = _word('先声夺人', 'xian1 sheng1 duo2 ren2', '先用声势压倒对方。')
SECOND_ANSWER = _word('人山人海', 'ren2 shan1 ren2 hai3', '形容人非常多。')
THIRD = _word('海阔天空', 'hai3 kuo4 tian1 kong1', '形容天地广阔，也形容说话没有拘束。')
WRONG = _word('山清水秀', 'shan1 qing1 shui3 xiu4', '形容山水景色优美。')


def synthetic_wav(rate=24000, sample=1):
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        audio.writeframes(int(sample).to_bytes(2, 'little', signed=True) * (rate // 20))
    return output.getvalue()


class FixtureModels:
    """仅认识本文件演练语句；不配置真实 provider，也不具备真实语言理解能力。"""
    def __init__(self, *, fail_ending=False, rate=24000):
        self.transcripts = []
        self.requests = []
        self.spoken = []
        self.fail_ending, self.rate = fail_ending, rate

    def complete(self, messages):
        request = json.loads(messages[-1]['content'])
        self.requests.append(request)
        if request['operation'] == 'start':
            return {'intent': 'start', 'idiom': deepcopy(FIRST)}
        if request['operation'] == 'assist':
            return {'intent': 'assist', 'idiom': deepcopy(FIRST_ANSWER), 'next_idiom': deepcopy(SECOND)}
        utterance = request['utterance']
        responses = {
            '一心一意': {'intent': 'answer', 'verdict': 'valid', 'idiom': FIRST, 'next_idiom': FIRST},
            '一马当先': {'intent': 'answer', 'verdict': 'valid', 'idiom': FIRST_ANSWER, 'next_idiom': SECOND},
            '人山人海': {'intent': 'answer', 'verdict': 'valid', 'idiom': SECOND_ANSWER, 'next_idiom': THIRD},
            '山清水秀': {'intent': 'answer', 'verdict': 'wrong_sound', 'idiom': WRONG},
            '什么意思': {'intent': 'explain', 'text': '这个成语就是说，先用声势压倒对方。'},
            '难一点': {'intent': 'adjust', 'change': 'up'},
            '元仔元仔': {'intent': 'wake'},
            '无意义噪声': {'intent': 'noise'},
        }
        if utterance not in responses:
            raise AssertionError('模拟夹具不支持该语句')
        return deepcopy(responses[utterance])

    def transcribe(self, data):
        with wave.open(io.BytesIO(data), 'rb') as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000):
                raise AssertionError('模拟 ASR 收到错误音频格式')
        if not self.transcripts:
            raise AssertionError('模拟 ASR 没有对应的测试语句')
        text = self.transcripts.pop(0)
        if isinstance(text, Exception):
            raise text
        return text

    def synthesize(self, text):
        if not isinstance(text, str) or not 0 < len(text) <= 100:
            raise AssertionError('模拟 TTS 仅接受 1 至 100 字')
        self.spoken.append(text)
        if self.fail_ending and text == TIMEOUT_TEXT:
            raise ProviderNetworkError('模拟结束时 TTS 断网')
        return synthetic_wav(self.rate)


class VirtualClock:
    def __init__(self):
        self.value = 1000.0

    def __call__(self):
        return self.value

    def now(self):
        return SimpleNamespace(nanoseconds=int((1_000_000 + self.value) * 1_000_000_000))


class VirtualRobot:
    def __init__(self, clock, steps, models):
        self.clock, self.steps, self.models = clock, list(steps), models
        self.node = SimpleNamespace(get_clock=lambda: clock, count_publishers=lambda topic: 1,
                                    destroy_subscription=lambda subscription: None)
        self.voice = self.session = None
        self.last_window = None
        self.events = []
        self.windows = []
        self.states = []
        self.on_spin = None

    def state(self, value):
        self.states.append((self.clock(), value))
        self.voice.on_state(SimpleNamespace(main_state=SimpleNamespace(value=value)))

    def _packet(self, vad):
        stamp = self.clock.now().nanoseconds
        return SimpleNamespace(stream_id=0, audio_vad_state=SimpleNamespace(value=vad),
            audio_data=b'\x01\x00' * 1600,
            header=SimpleNamespace(stamp=SimpleNamespace(sec=stamp // 1_000_000_000,
                                                       nanosec=stamp % 1_000_000_000)))

    def _open_window(self):
        self.last_window = self.voice.opened_at_ns
        self.windows.append({'at': self.clock(), 'deadline': self.voice.deadline,
                             'inactivity_deadline': self.session.inactivity_deadline,
                             'state': self.session.game.snapshot()})
        # 自动提醒重开收音窗口，但不能吃掉原来排定的用户输入。
        if self.events:
            return
        if not self.steps:
            return
        step = self.steps.pop(0)
        for delay, state in step.get('states', []):
            self.events.append((self.clock() + delay, 'state', state))
        if 'text' in step:
            when = self.clock() + step.get('wait', .5)
            self.events.extend(((when, 'begin', None), (when + .1, 'end', step['text'])))
        self.events.sort(key=lambda event: event[0])

    def spin(self):
        # 让 Session 的模型线程完成；不等待真实的 30/120 秒。
        time.sleep(0)
        if self.on_spin:
            self.on_spin(self)
        if not self.voice.active:
            self.clock.value += .01
            return
        if self.last_window != self.voice.opened_at_ns:
            self._open_window()
        limit = (min(self.voice.last_packet_at + 3.001, self.voice.started_at + 18.001)
                 if self.voice.buffer is not None else self.voice.deadline)
        target = min(self.events[0][0], limit) if self.events else limit
        self.clock.value = max(self.clock() + .001, target)
        while self.events and self.events[0][0] <= self.clock():
            _, kind, value = self.events.pop(0)
            if kind == 'state':
                self.state(value)
            else:
                if kind == 'end':
                    self.models.transcripts.append(value)
                self.voice.on_audio(self._packet(1 if kind == 'begin' else 3))


class VirtualMedia:
    def __init__(self, robot):
        self.robot = robot
        self.played = []

    def play(self, data, on_finished=None, before_start=None):
        if self.robot.session.closed:
            raise AssertionError('已关闭会话不能继续播放')
        if self.robot.voice.active:
            raise AssertionError('模拟播报时不能同时收音')
        if before_start is not None:
            before_start()
        with wave.open(io.BytesIO(data), 'rb') as audio:
            if audio.getnchannels() != 1 or audio.getsampwidth() != 2 or audio.getnframes() <= 0:
                raise AssertionError('模拟媒体收到无效音频')
        self.played.append({'audio': data, 'at': self.robot.clock(), 'thread': threading.get_ident()})
        self.robot.state(3)
        self.robot.clock.value += .25
        self.robot.state(1)
        if on_finished:
            on_finished()


class VirtualEffects:
    def __init__(self, robot):
        self.robot = robot
        self.count = 0
        self.threads = []
        self.outcomes = []

    def celebrate(self, outcome='success'):
        if self.robot.session.closed or self.robot.voice.active:
            raise AssertionError('庆祝只能发生在活动会话的非收音阶段')
        self.count += outcome in ('success', 'win')
        self.outcomes.append(outcome)
        self.threads.append(threading.get_ident())


class Fixture:
    def __init__(self, steps=(), models=None, *, operation_timeout=95):
        self.clock = VirtualClock()
        self.models = models if models is not None else FixtureModels()
        self.robot = VirtualRobot(self.clock, steps, self.models)
        self.voice = VoiceInput(self.robot, clock=self.clock)
        self.robot.voice = self.voice
        self.robot.state(1)
        self.media = VirtualMedia(self.robot)
        self.effects = VirtualEffects(self.robot)
        self.directory = tempfile.TemporaryDirectory(prefix='chengyu-simulation-')
        self.fallback = Path(self.directory.name) / 'timeout.wav'
        self.fallback_audio = synthetic_wav(sample=2)
        self.fallback.write_bytes(self.fallback_audio)
        self.session = Session(self.models, self.robot, self.voice, self.media, self.effects, self.fallback,
                               clock=self.clock, operation_timeout=operation_timeout)
        self.robot.session = self.session

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.voice.close()
        self.directory.cleanup()

    def summary(self, result):
        return {'reason': result.get('reason'), 'state': self.session.game.snapshot(),
                'celebrations': self.effects.count, 'media_plays': len(self.media.played),
                'fallback_used': any(item['audio'] == self.fallback_audio for item in self.media.played),
                'listening_windows': len(self.robot.windows)}


def simulate():
    """运行实际游戏/会话/VAD 状态机；只替换设备、时钟和模型，不读取安装配置。"""
    details = {}
    steps = [
        {'text': '一马当先'}, {'text': '山清水秀'}, {'text': '什么意思'}, {'text': '难一点'},
        {'text': '人山人海', 'wait': 5, 'states': [(1, 0), (2, 3), (3, 1)]},
        {'text': '不玩了'},
    ]
    with Fixture(steps) as fixture:
        result = fixture.session.run()
        state = fixture.session.game.snapshot()
        if (result.get('reason') != 'user_exit' or state['total_successes'] != 2
                or state['level'] != '中级' or fixture.effects.count != 2):
            raise RuntimeError('模拟完整游戏未通过。')
        details['game'] = fixture.summary(result)
    with Fixture([{'states': [(30, 0)]}]) as fixture:
        result = fixture.session.run()
        if result.get('reason') != 'inactivity':
            raise RuntimeError('模拟两分钟退出未通过。')
        if fixture.effects.outcomes != ['lose'] or sum(r['operation'] == 'assist' for r in fixture.models.requests) != 1:
            raise RuntimeError('模拟单次援助和本局失败未通过。')
        details['timeout'] = fixture.summary(result)
    levels = ['一心一意'] * 5 + ['继续'] + ['一心一意'] * 7 + ['继续'] + ['一心一意'] * 10
    with Fixture([{'text': text} for text in levels + ['再来一局', '不玩了']]) as fixture:
        result = fixture.session.run()
        if (result.get('reason') != 'user_exit' or fixture.effects.outcomes.count('win') != 3
                or fixture.session.game.snapshot()['total_successes'] != 22):
            raise RuntimeError('模拟三档通关与继续新局未通过。')
        details['three_levels'] = fixture.summary(result)
    with Fixture([{'text': text} for text in ['山清水秀'] * 3 + ['再来一局', '不玩了']]) as fixture:
        result = fixture.session.run()
        if result.get('reason') != 'user_exit' or fixture.effects.outcomes.count('lose') != 1:
            raise RuntimeError('模拟三次接错及重开未通过。')
        details['round_loss'] = fixture.summary(result)
    with Fixture(models=FixtureModels(fail_ending=True)) as fixture:
        result = fixture.session.run()
        if not any(item['audio'] == fixture.fallback_audio for item in fixture.media.played):
            raise RuntimeError('模拟离线结束音频未通过。')
        details['offline_ending'] = fixture.summary(result)
    with Fixture([{'text': '', 'wait': 4}, {'text': '元仔元仔', 'wait': 4},
                  {'text': '无意义噪声', 'wait': 4}]) as fixture:
        result = fixture.session.run()
        deadlines = {window['inactivity_deadline'] for window in fixture.robot.windows}
        if result.get('reason') != 'inactivity' or len(deadlines) != 1:
            raise RuntimeError('模拟噪声或唤醒重置了等待期限。')
        details['unchanged_deadline'] = fixture.summary(result)
    return {'status': 'success', 'message': '离线完整会话模拟通过；未调用云模型、机器人或真实播放。',
            'simulated': True, 'model': '固定测试响应，仅供验收程序逻辑，并非离线成语词库。', 'details': details}
