"""游戏会话：只在主线程提交输出，网络迟到结果不能再次发声或运动。"""
import io
import logging
import queue
import threading
import time
import wave
from dataclasses import replace

from .game import Game
from .support import AudioError, PlaybackInterrupted, Cancelled, measure
from .providers import ProviderError

TIMEOUT_TEXT = '两分钟没有听到你的回答，成语接龙先结束啦。下次再一起玩吧！'
logger = logging.getLogger(__name__)


class InactivityExpired(Exception):
    """自动提醒或援助不能因自己的网络等待延长应用空闲上限。"""


def pcm_wav(pcm):
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        wav.writeframes(pcm)
    return output.getvalue()


def synthesize(models, text):
    """TTS 的百字上限只影响请求分段；播报合并为同一个原厂媒体 trace。"""
    frames, params = [], None
    for start in range(0, len(text), 100):
        with wave.open(io.BytesIO(models.synthesize(text[start:start + 100])), 'rb') as wav:
            current = (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype())
            if current[0:2] != (1, 2) or current[3] != 'NONE' or (params is not None and params != current):
                raise AudioError('TTS 返回的音频格式不一致。')
            pcm = wav.readframes(wav.getnframes())
            if not pcm or len(pcm) != wav.getnframes() * 2:
                raise AudioError('TTS 音频不完整。')
            params = current
            frames.append(pcm)
    if params is None:
        raise AudioError('播报文本为空。')
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setparams((params[0], params[1], params[2], 0, 'NONE', 'not compressed'))
        wav.writeframes(b''.join(frames))
    return output.getvalue()


class Session:
    def __init__(self, models, robot, voice, media, effects, fallback, *, clock=time.monotonic, operation_timeout=95,
                 start_notice='', speech=None):
        self.models, self.robot, self.voice, self.media = models, robot, voice, media
        self.effects, self.fallback = effects, fallback
        self.clock, self.operation_timeout = clock, operation_timeout
        self.game = Game(models.complete, speech=speech)
        self.closed = False
        self.start_notice = start_notice
        self.inactivity_deadline = 0
        self.answer_remaining = 30.0
        self.reminders = 0
        self.wait_started = None
        self.waiting_for_answer = False

    def listen(self, reset_question=False):
        now = self.clock()
        if not self.inactivity_deadline:
            self.inactivity_deadline = now + 120
        if reset_question:
            self.answer_remaining, self.reminders = 30.0, 0
        self.waiting_for_answer = self.game.snapshot()['phase'] == 'playing'
        deadline = self.inactivity_deadline
        if self.waiting_for_answer:
            until_reminder = self.answer_remaining - (20, 10, 0)[self.reminders]
            deadline = min(deadline, now + max(0, until_reminder))
        self.wait_started = now
        self.voice.listen(deadline=deadline)
        logger.info('GAME_WAIT answer_remaining=%.2f inactivity_remaining=%.2f reminders=%d phase=%s',
                    self.answer_remaining, max(0, self.inactivity_deadline - now), self.reminders,
                    self.game.snapshot()['phase'])

    def pause_wait(self):
        # 仅累计机器人实际等待回答的时间；模型处理和自己的播报不占孩子的答题时间。
        if self.wait_started is not None and self.waiting_for_answer:
            self.answer_remaining = max(0, self.answer_remaining - (self.clock() - self.wait_started))
        self.wait_started = None

    def check_inactivity(self):
        if self.inactivity_deadline and self.clock() >= self.inactivity_deadline:
            raise InactivityExpired()

    def check_response_deadline(self, deadline):
        if self.closed:
            raise AudioError('会话已经关闭，未提交迟到的回答。')
        if self.clock() >= deadline:
            self.check_inactivity()
            raise AudioError('模型操作超时，游戏已结束。')

    def work(self, operation, *, inactivity_limit=False, expires_at=None):
        """ROS 持续泵送；daemon 仅计算/联网，永远不持有硬件接口。"""
        if self.closed:
            raise AudioError('会话已经关闭。')
        if inactivity_limit:
            self.check_inactivity()
        results = queue.Queue(maxsize=1)
        def run():
            try:
                results.put((True, operation()))
            except Exception as error:
                results.put((False, error))
        threading.Thread(target=run, daemon=True, name='chengyu-model').start()
        deadline = self.clock() + self.operation_timeout
        if expires_at is not None:
            deadline = min(deadline, expires_at)
        while True:
            self.robot.spin()
            if self.closed:
                raise AudioError('会话已经关闭。')
            if inactivity_limit:
                self.check_inactivity()
            if self.clock() >= deadline:
                if expires_at is not None:
                    self.check_inactivity()
                raise AudioError('模型操作超时，游戏已结束。')
            try:
                ok, result = results.get_nowait()
            except queue.Empty:
                continue
            if not ok:
                raise result
            return result

    def say(self, text, *, listen=False, ending=False, reset_question=False, inactivity_limit=False):
        self.voice.mute()
        try:
            with measure('tts'):
                audio = self.work(lambda: synthesize(self.models, text), inactivity_limit=inactivity_limit)
        except Cancelled:
            raise
        except Exception:
            # 结束播报可在断网时使用随包提供的普通话录音。
            if not ending:
                raise
            audio = self.fallback.read_bytes()
        if self.closed:
            raise AudioError('会话已经关闭，未播报迟到结果。')
        options = {'before_start': self.check_inactivity} if inactivity_limit else {}
        try:
            self.media.play(audio, on_finished=(lambda: self.listen(reset_question)) if listen else None, **options)
        except PlaybackInterrupted:
            if not listen or ending:
                raise
            # 游戏状态已提交；唤醒应答抢占播报时保留题目和得分，不重放判题/庆祝。
            # VoiceInput 仍只接受原厂 LISTENING 下的新 VAD；打断本身不延长空闲期限。
            logger.info('GAME_PLAYBACK_INTERRUPTED resume=listening')
            self.listen(reset_question)

    def deliver(self, reply, *, timed=False):
        if timed:
            self.check_inactivity()
        outcome = reply.outcome or ('success' if reply.celebrate else '')
        if outcome:
            feedback = self.effects.celebrate(outcome=outcome)
            logger.info('GAME_EFFECT outcome=%s result=%s', outcome, feedback)
        if reply.text:
            self.say(reply.text, listen=not reply.ended, ending=reply.ended,
                     reset_question=reply.new_question or reply.responded, inactivity_limit=timed)
        elif not reply.ended:
            self.listen(reset_question=reply.new_question)

    def finish_inactivity(self):
        self.game.close()
        self.voice.mute()
        # 到期后只用缓存结束语，不能再等一次云端 TTS 才退出。
        logger.info('GAME_INACTIVITY_TIMEOUT')
        self.media.play(self.fallback.read_bytes())
        return {'status': 'success', 'message': '成语接龙因两分钟无回应结束。', 'reason': 'inactivity'}

    def run(self):
        turn = 0
        try:
            with measure('initial_llm'):
                reply = self.work(self.game.start)
            if self.start_notice and not reply.ended:
                reply = replace(reply, text=self.start_notice + reply.text)
            self.deliver(reply)
            if reply.ended:
                return {'status': 'failed', 'message': reply.text}
            while True:
                self.robot.spin()
                event = self.voice.poll()
                if event is None:
                    continue
                kind, audio = event
                self.pause_wait()
                if kind == 'timeout':
                    if self.clock() >= self.inactivity_deadline:
                        return self.finish_inactivity()
                    if self.answer_remaining > 0 and self.reminders < 2:
                        self.reminders += 1
                        self.deliver(self.game.hint(), timed=True)
                    else:
                        with measure('timed_assistance'):
                            reply = self.work(self.game.timeout, inactivity_limit=True)
                        self.deliver(reply, timed=True)
                    continue
                heard_at = self.clock()
                # 期限前收到的完整语句有一次判别宽限；ASR 和意图判定共用预算。
                response_deadline = heard_at + min(30, self.operation_timeout)
                try:
                    with measure('asr'):
                        text = self.work(lambda: self.models.transcribe(pcm_wav(audio)),
                                         expires_at=response_deadline)
                except ProviderError:
                    # 失败不是用户回应，不把噪声或 ASR 重试变成无限保活。
                    self.say('刚才没有听清，不算答错，请再说一次。', listen=True, inactivity_limit=True)
                    continue
                if not text.strip():
                    self.listen()
                    continue
                turn += 1
                with measure('turn_reply'):
                    reply = self.work(lambda: self.game.respond(
                        text, turn_id=turn, before_commit=lambda: self.check_response_deadline(response_deadline)),
                                      expires_at=response_deadline)
                state = self.game.snapshot()
                logger.info('GAME_TURN turn=%d phase=%s level=%s successes=%d level_successes=%d target=%d outcome=%s ended=%s',
                            turn, state['phase'], state['level'], state['total_successes'], state['level_successes'],
                            state['target_successes'], reply.outcome or ('success' if reply.celebrate else 'none'), reply.ended)
                if reply.responded:
                    self.inactivity_deadline = heard_at + 120
                self.deliver(reply, timed=not reply.responded and not reply.ended)
                if reply.ended:
                    return {'status': 'success', 'message': '成语接龙已结束。', 'reason': 'user_exit'}
        except InactivityExpired:
            return self.finish_inactivity()
        except ProviderError:
            self.game.close()
            self.voice.mute()
            self.media.play(self.fallback.read_bytes())
            return {'status': 'failed', 'message': '云模型暂时不可用，游戏已结束。', 'reason': 'provider_error'}
        finally:
            self.closed = True
            self.game.close()
            self.voice.mute()
