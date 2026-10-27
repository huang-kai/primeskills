"""独立千问 HTTP 客户端；构造不联网，不读取宿主模型或环境密钥。"""
import base64
import http.client
import io
import json
import struct
import urllib.error
import urllib.parse
import urllib.request
import wave

from .configuration import validate_config


class ProviderError(RuntimeError):
    pass


class ProviderNetworkError(ProviderError):
    pass


class _NoSpeech(ProviderError):
    pass


CHAT_PATH = '/compatible-mode/v1/chat/completions'
ASR_PATH = '/api/v1/services/aigc/multimodal-generation/generation'
TTS_PATH = '/api/v1/services/audio/tts/SpeechSynthesizer'
ASR_SAMPLE = 'https://dashscope.oss-cn-beijing.aliyuncs.com/audios/welcome.mp3'
ASR_CONTEXT = ('儿童成语接龙。成语、同音字、前后鼻音、解释含义、提高难度、降低难度、'
               '初级、中级、高级、结束成语接龙、不想玩了。')
MAX_AUDIO = 4 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProviderError('云接口发生重定向，未继续访问。')


def _wav(data, rate, max_seconds):
    if not isinstance(data, bytes) or not 44 <= len(data) <= MAX_AUDIO:
        raise ProviderError('音频文件大小不符合要求。')
    try:
        with wave.open(io.BytesIO(data), 'rb') as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, rate):
                raise ProviderError('音频须为规定采样率的单声道 PCM16 WAV。')
            frames = audio.getnframes()
            if not 0 < frames <= rate * max_seconds or len(audio.readframes(frames)) != frames * 2:
                raise ProviderError('音频时长或数据完整性不符合要求。')
    except (wave.Error, EOFError, ValueError):
        raise ProviderError('音频不是有效的 PCM WAV。') from None
    return data


def _tts_wav(data):
    # 北京服务实测的流式 WAV 占位头；其他截断数据仍拒绝。
    if (len(data) >= 44 and data[:4] == b'RIFF' and data[8:16] == b'WAVEfmt '
            and data[36:40] == b'data'
            and struct.unpack_from('<I', data, 4)[0] == 0x7fffffbf
            and struct.unpack_from('<I', data, 40)[0] == 0x7fffff9b):
        if len(data) > MAX_AUDIO or (len(data) - 44) % 2:
            raise ProviderError('TTS 音频数据不完整。')
        try:
            with wave.open(io.BytesIO(data), 'rb') as audio:
                if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 24000):
                    raise ProviderError('TTS 流式音频格式不符合要求。')
            normalized = io.BytesIO()
            with wave.open(normalized, 'wb') as audio:
                audio.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
                audio.writeframes(data[44:])
            data = normalized.getvalue()
        except (wave.Error, EOFError):
            raise ProviderError('TTS 音频格式错误。') from None
    return _wav(data, 24000, 80)


def _audio_url(value):
    try:
        parts = urllib.parse.urlsplit(value)
        if (parts.scheme not in ('http', 'https') or parts.username or parts.password
                or parts.hostname != 'dashscope-result-bj.oss-cn-beijing.aliyuncs.com'
                or parts.port not in (None, 80, 443) or parts.fragment):
            raise ValueError()
        return urllib.parse.urlunsplit(parts._replace(scheme='https', netloc=parts.hostname))
    except (TypeError, ValueError, AttributeError):
        raise ProviderError('TTS 返回的音频地址不属于已支持的北京结果服务。') from None


class Models:
    def __init__(self, config):
        self.config = validate_config(config)
        self.base_url = 'https://' + self.config['api_host']
        self.timeout = self.config.get('timeout_seconds', 30)

    def _read(self, request, limit, *, stage='云接口'):
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=self.timeout) as response:
                data = response.read(limit + 1)
        except urllib.error.HTTPError as error:
            if (stage == 'ASR 识别' and error.code == 400
                    and self.config['models']['asr'] == 'qwen-audio-3.1-asr-flash'):
                try:
                    raw = error.read(4097)
                    detail = json.loads(raw) if len(raw) <= 4096 else None
                except (ValueError, UnicodeError, OSError, http.client.HTTPException):
                    detail = None
                if (isinstance(detail, dict) and detail.get('code') == 'CLIENT_ERROR'
                        and detail.get('message') == 'ASR_RESPONSE_HAVE_NO_WORDS'):
                    raise _NoSpeech() from None
            hints = {401: '请核对密钥与地域', 403: '请核对模型权限',
                     404: '请核对模型和服务地址', 429: '请核对余额、配额或限流'}
            if stage == 'TTS 合成':
                hints[400] = '请核对 TTS 模型与音色是否匹配，并检查请求参数'
            raise ProviderError(stage + ' HTTP ' + str(error.code) + '；' +
                                hints.get(error.code, '请求未成功')) from None
        except (urllib.error.URLError, OSError, http.client.HTTPException):
            raise ProviderNetworkError(stage + '网络连接失败或超时。') from None
        if len(data) > limit:
            raise ProviderError(stage + '返回数据过大。')
        return data

    def _post(self, path, body, *, stage='云接口'):
        request = urllib.request.Request(self.base_url + path, data=json.dumps(body).encode(), method='POST',
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.config['api_key']})
        try:
            result = json.loads(self._read(request, 128 * 1024, stage=stage))
        except (ValueError, UnicodeError):
            raise ProviderError(stage + '返回无效 JSON。') from None
        if not isinstance(result, dict) or result.get('code') or result.get('status_code', 200) != 200:
            raise ProviderError(stage + '未返回成功结果。')
        return result

    def complete(self, messages):
        model = self.config['models']['llm']
        body = {'model': model, 'messages': messages, 'stream': False, 'temperature': 0,
                'max_tokens': 2000, 'response_format': {'type': 'json_object'}}
        if model in ('qwen-plus', 'qwen-flash'):
            body['enable_thinking'] = False
        result = self._post(CHAT_PATH, body)
        try:
            choice = result['choices'][0]
            if choice['finish_reason'] != 'stop' or choice['message'].get('tool_calls'):
                raise ValueError()
            value = json.loads(choice['message']['content'])
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (KeyError, IndexError, TypeError, ValueError):
            raise ProviderError('LLM 没有返回完整的 JSON 对象。') from None

    def _recognize(self, audio, audio_format='wav'):
        model = self.config['models']['asr']
        messages = [{'role': 'user', 'content': [{'type': 'input_audio', 'input_audio': {'data': audio}}]}]
        if model == 'qwen3-asr-flash':
            messages.insert(0, {'role': 'system', 'content': ASR_CONTEXT})
            result = self._post(CHAT_PATH, {'model': model, 'messages': messages, 'stream': False,
                                'asr_options': {'language': 'zh', 'enable_itn': False}})
        else:
            # 3.1 非实时接口通过音频消息前的 input_text 提供领域上下文。
            messages.insert(0, {'role': 'user', 'content': [{'type': 'input_text', 'text': ASR_CONTEXT}]})
            try:
                result = self._post(ASR_PATH, {'model': model, 'input': {'messages': messages},
                    'parameters': {'format': audio_format, 'sample_rate': 16000, 'language_hints': ['zh']}},
                    stage='ASR 识别')
            except _NoSpeech:
                return ''
        try:
            if model == 'qwen3-asr-flash':
                choice = result['choices'][0]
                if choice['finish_reason'] != 'stop':
                    raise ValueError()
                text = choice['message']['content']
                if isinstance(text, list):
                    if any(not isinstance(part, dict) or part.get('type', 'text') != 'text'
                           or not isinstance(part.get('text'), str) for part in text):
                        raise ValueError()
                    text = ''.join(part['text'] for part in text)
            else:
                text = result['output']['text']
        except (KeyError, IndexError, TypeError, ValueError):
            raise ProviderError('ASR 返回结构不符合预期。') from None
        if not isinstance(text, str) or len(text) > 4000:
            raise ProviderError('ASR 返回文字不符合预期。')
        return text.strip()

    def transcribe(self, wav_bytes):
        _wav(wav_bytes, 16000, 30)
        return self._recognize('data:audio/wav;base64,' + base64.b64encode(wav_bytes).decode())

    def synthesize(self, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 100:
            raise ProviderError('每段播报须为 1 至 100 字。')
        result = self._post(TTS_PATH, {'model': self.config['models']['tts'], 'input': {
            'text': text, 'voice': self.config['voice'], 'format': 'wav', 'sample_rate': 24000,
            'language_hints': ['zh']}}, stage='TTS 合成')
        try:
            if result['output']['finish_reason'] != 'stop':
                raise ValueError()
            url = _audio_url(result['output']['audio']['url'])
        except (KeyError, TypeError, ValueError):
            raise ProviderError('TTS 没有返回完整的合成结果。') from None
        # 下载音频不携带 API Key；拒绝重定向和未支持的结果域名。
        return _tts_wav(self._read(urllib.request.Request(url), MAX_AUDIO, stage='TTS 音频下载'))

    def check(self):
        """显式执行三项有界云检查，不录音、不播放、不调用机器人。"""
        operations = {
            'llm': lambda: self.complete([{'role': 'user', 'content': '仅返回 JSON：{"ok":true}'}]),
            'asr': lambda: self._recognize(ASR_SAMPLE, 'mp3'),
            'tts': lambda: self.synthesize('成语接龙语音检查。'),
        }
        results = {}
        for role, operation in operations.items():
            try:
                value = operation()
                if not value:
                    raise ProviderError('模型检查没有返回内容。')
                results[role] = {'status': 'success', 'message': role.upper() + ' 连接检查通过。'}
            except ProviderError as error:
                results[role] = {'status': 'failed', 'message': str(error)}
        return results
