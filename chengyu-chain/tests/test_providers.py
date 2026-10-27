from copy import deepcopy
import io
import json
import os
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import Mock, patch
import urllib.error
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from chengyu_game.configuration import DEFAULTS, ConfigError
from chengyu_game.providers import Models, NoRedirect, ProviderError, ProviderNetworkError


def wav(rate=16000, seconds=.25):
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as audio:
        audio.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        audio.writeframes(b'\x00\x00' * int(rate * seconds))
    return buffer.getvalue()


def completion(content='{"ok":true}', finish='stop'):
    return {'choices': [{'message': {'content': content}, 'finish_reason': finish}]}


class Response(io.BytesIO):
    pass


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.config = deepcopy(DEFAULTS)
        self.config['api_key'] = 'private-test-key'
        self.models = Models(self.config)

    def opener(self, values):
        opener = Mock()
        opener.open.side_effect = [Response(json.dumps(value).encode() if isinstance(value, dict) else value)
                                    for value in values]
        patcher = patch('chengyu_game.providers.urllib.request.build_opener', return_value=opener)
        patcher.start()
        self.addCleanup(patcher.stop)
        return opener

    def test_construct_does_not_connect_and_copies_configuration(self):
        with patch('urllib.request.build_opener', side_effect=AssertionError('must not connect')):
            models = Models(self.config)
        self.config['api_key'] = 'changed'
        self.assertEqual(models.config['api_key'], 'private-test-key')

    def test_complete_posts_json_protocol_and_returns_object(self):
        opener = self.opener([completion()])
        self.assertEqual(self.models.complete([{'role': 'user', 'content': 'JSON'}]), {'ok': True})
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions')
        self.assertEqual(request.get_header('Authorization'), 'Bearer private-test-key')
        self.assertEqual(json.loads(request.data)['response_format'], {'type': 'json_object'})
        self.assertEqual(opener.open.call_args.kwargs['timeout'], 30)

    def test_reject_truncated_nonobject_and_invalid_model_json(self):
        self.opener([completion('{}', 'length'), completion('[]'), completion('private-test-key')])
        for _ in range(3):
            with self.assertRaises(ProviderError) as caught:
                self.models.complete([])
            self.assertNotIn('private-test-key', str(caught.exception))

    def test_asr_uses_base64_wav_without_environment_key(self):
        opener = self.opener([completion('一心一意')])
        with patch.dict(os.environ, {'DASHSCOPE_API_KEY': 'wrong-key'}):
            self.assertEqual(self.models.transcribe(wav()), '一心一意')
        request = opener.open.call_args.args[0]
        body = json.loads(request.data)
        self.assertTrue(body['messages'][-1]['content'][0]['input_audio']['data'].startswith('data:audio/wav;base64,'))
        self.assertEqual(request.get_header('Authorization'), 'Bearer private-test-key')

    def test_asr_context_uses_each_models_documented_message_type(self):
        for model, result in (('qwen3-asr-flash', completion('一心一意')),
                              ('qwen-audio-3.1-asr-flash', {'output': {'text': '一心一意'}})):
            with self.subTest(model=model), patch.object(Models, '_post', return_value=result) as post:
                self.config['models']['asr'] = model
                Models(self.config).transcribe(wav())
                body = post.call_args.args[1]
                messages = body['messages'] if model == 'qwen3-asr-flash' else body['input']['messages']
                self.assertEqual(len(messages), 2)
                context, audio = messages
                if model == 'qwen3-asr-flash':
                    self.assertEqual(context['role'], 'system')
                    text = context['content']
                else:
                    self.assertEqual(context['role'], 'user')
                    self.assertEqual(context['content'][0]['type'], 'input_text')
                    text = context['content'][0]['text']
                self.assertIn('成语接龙', text)
                self.assertIn('解释', text)
                self.assertIn('难度', text)
                self.assertLessEqual(len(text), 400)
                self.assertNotIn('一心一意', text)
                self.assertEqual(audio['content'][0]['type'], 'input_audio')
                self.assertNotIn('vocabulary', body.get('parameters', {}))

    def test_compatible_asr_accepts_text_blocks_and_rejects_incomplete_results(self):
        self.opener([completion([{'type': 'text', 'text': '心想'}, {'text': '事成'}]),
                     completion('心想', 'length'), completion('心想', None)])
        self.assertEqual(self.models.transcribe(wav()), '心想事成')
        for _ in range(2):
            with self.assertRaises(ProviderError):
                self.models.transcribe(wav())

    def test_asr_malformed_text_never_becomes_an_answer(self):
        cases = [None, 5, {'text': 'private-test-key'},
                 [{'type': 'input_audio', 'text': 'private-test-key'}],
                 [{'text': '心想'}, {}], '字' * 4001]
        with patch.object(self.models, '_post') as post:
            for content in cases:
                with self.subTest(content_type=type(content).__name__):
                    post.return_value = completion(content)
                    with self.assertRaises(ProviderError) as caught:
                        self.models.transcribe(wav())
                    self.assertNotIn('private-test-key', str(caught.exception))

    def test_new_asr_uses_native_protocol(self):
        self.config['models']['asr'] = 'qwen-audio-3.1-asr-flash'
        opener = self.opener([{'output': {'text': ' 心想事成 '}}])
        self.assertEqual(Models(self.config).transcribe(wav()), '心想事成')
        request = opener.open.call_args.args[0]
        self.assertTrue(request.full_url.endswith('/multimodal-generation/generation'))
        self.assertEqual(json.loads(request.data)['parameters']['sample_rate'], 16000)

    def test_native_asr_uses_full_text_without_repeating_sentence_and_checks_types(self):
        self.config['models']['asr'] = 'qwen-audio-3.1-asr-flash'
        models = Models(self.config)
        with patch.object(models, '_post') as post:
            post.return_value = {'output': {'text': ' 心想事成 ', 'sentence': {'text': '事成'}}}
            self.assertEqual(models.transcribe(wav()), '心想事成')
            for output in ({'text': '   '}, {'text': ''}):
                post.return_value = {'output': output}
                self.assertEqual(models.transcribe(wav()), '')
            for output in (None, {}, {'text': None}, {'text': [{'text': '心想事成'}]},
                           {'sentence': {'text': '心想事成'}}):
                post.return_value = {'output': output}
                with self.subTest(output=output), self.assertRaises(ProviderError):
                    models.transcribe(wav())

    def test_empty_asr_is_not_invented_as_answer(self):
        self.opener([completion('   ')])
        self.assertEqual(self.models.transcribe(wav()), '')

    def test_native_no_words_http_error_is_empty_asr_without_retry(self):
        self.config['models']['asr'] = 'qwen-audio-3.1-asr-flash'
        body = {'code': 'CLIENT_ERROR', 'message': 'ASR_RESPONSE_HAVE_NO_WORDS'}
        error = urllib.error.HTTPError('private-url', 400, 'private', {},
                                      io.BytesIO(json.dumps(body).encode()))
        opener = Mock(open=Mock(side_effect=error))
        with patch('urllib.request.build_opener', return_value=opener):
            self.assertEqual(Models(self.config).transcribe(wav()), '')
        self.assertEqual(opener.open.call_count, 1)

    def test_no_words_exception_does_not_hide_other_http_errors(self):
        for model, status, code, message in (
                ('qwen-audio-3.1-asr-flash', 401, 'CLIENT_ERROR', 'ASR_RESPONSE_HAVE_NO_WORDS'),
                ('qwen-audio-3.1-asr-flash', 400, 'InvalidParameter', 'ASR_RESPONSE_HAVE_NO_WORDS'),
                ('qwen-audio-3.1-asr-flash', 400, 'CLIENT_ERROR', 'private-test-key'),
                ('qwen3-asr-flash', 400, 'CLIENT_ERROR', 'ASR_RESPONSE_HAVE_NO_WORDS')):
            self.config['models']['asr'] = model
            error = urllib.error.HTTPError('private-url', status, 'private', {},
                io.BytesIO(json.dumps({'code': code, 'message': message}).encode()))
            opener = Mock(open=Mock(side_effect=error))
            with self.subTest(model=model, status=status, code=code), \
                    patch('urllib.request.build_opener', return_value=opener):
                with self.assertRaises(ProviderError) as caught:
                    Models(self.config).transcribe(wav())
                self.assertNotIn('private', str(caught.exception))
            self.assertEqual(opener.open.call_count, 1)

    def test_invalid_audio_never_reaches_cloud(self):
        with patch('urllib.request.build_opener') as opener:
            for data in (b'not-wave', wav(24000), wav(seconds=31), wav()[:-12]):
                with self.subTest(size=len(data)), self.assertRaises(ProviderError):
                    self.models.transcribe(data)
        opener.assert_not_called()

    def test_tts_download_has_no_key_and_upgrades_result_url_to_https(self):
        result = {'output': {'finish_reason': 'stop', 'audio': {
            'url': 'http://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/audio.wav?signature=private'}}}
        data = wav(24000)
        opener = self.opener([result, data])
        self.assertEqual(self.models.synthesize('答对啦！'), data)
        post, download = [item.args[0] for item in opener.open.call_args_list]
        self.assertEqual(json.loads(post.data)['input']['voice'], 'longanlingxin')
        self.assertIsNone(download.get_header('Authorization'))
        self.assertTrue(download.full_url.startswith('https://'))

    def test_tts_synthesis_http_400_identifies_model_voice_mismatch_without_private_detail(self):
        error = urllib.error.HTTPError('https://private.example/key', 400, 'private-test-key', {},
                                      io.BytesIO(b'{"code":"InvalidParameter","message":"private-test-key"}'))
        opener = Mock(open=Mock(side_effect=error))
        with patch('urllib.request.build_opener', return_value=opener):
            with self.assertRaises(ProviderError) as caught:
                self.models.synthesize('成语接龙结束啦。')
        message = str(caught.exception)
        self.assertIn('TTS 合成 HTTP 400', message)
        self.assertIn('模型', message)
        self.assertIn('音色', message)
        self.assertNotIn('private', message)
        self.assertEqual(opener.open.call_count, 1)

    def test_tts_download_http_failure_is_not_reported_as_model_voice_mismatch(self):
        result = {'output': {'finish_reason': 'stop', 'audio': {
            'url': 'https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/audio.wav?signature=private'}}}
        opener = Mock(open=Mock(side_effect=[Response(json.dumps(result).encode()),
            urllib.error.HTTPError('private-signed-url', 400, 'private-test-key', {}, None)]))
        with patch('urllib.request.build_opener', return_value=opener):
            with self.assertRaises(ProviderError) as caught:
                self.models.synthesize('成语接龙结束啦。')
        message = str(caught.exception)
        self.assertIn('TTS 音频下载 HTTP 400', message)
        self.assertNotIn('音色', message)
        self.assertNotIn('private', message)
        self.assertEqual(opener.open.call_count, 2)

    def test_tts_external_url_rejected_before_download(self):
        opener = self.opener([{'output': {'finish_reason': 'stop', 'audio': {'url': 'https://evil.example/audio.wav'}}}])
        with self.assertRaises(ProviderError):
            self.models.synthesize('你好')
        self.assertEqual(opener.open.call_count, 1)

    def test_tts_incomplete_response_not_downloaded(self):
        opener = self.opener([{'output': {'finish_reason': None, 'audio': {'url': 'https://evil.example'}}}])
        with self.assertRaises(ProviderError):
            self.models.synthesize('你好')
        self.assertEqual(opener.open.call_count, 1)

    def test_tts_stream_placeholder_normalized_but_truncation_rejected(self):
        data = bytearray(wav(24000))
        struct.pack_into('<I', data, 4, 0x7fffffbf)
        struct.pack_into('<I', data, 40, 0x7fffff9b)
        result = {'output': {'finish_reason': 'stop', 'audio': {
            'url': 'https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/audio.wav'}}}
        self.opener([result, bytes(data), result, wav(24000)[:-2]])
        self.assertEqual(self.models.synthesize('你好'), wav(24000))
        with self.assertRaises(ProviderError):
            self.models.synthesize('你好')

    def test_http_and_network_errors_do_not_expose_response_or_key(self):
        opener = Mock()
        opener.open.side_effect = [urllib.error.HTTPError('private-test-key', 401, 'private-test-key', {}, None),
                                   urllib.error.URLError('private-test-key')]
        with patch('urllib.request.build_opener', return_value=opener):
            with self.assertRaises(ProviderError) as caught:
                self.models.complete([])
            self.assertNotIn('private-test-key', str(caught.exception))
            with self.assertRaises(ProviderNetworkError) as caught:
                self.models.complete([])
            self.assertNotIn('private-test-key', str(caught.exception))

    def test_redirect_never_forwards_credentials(self):
        with self.assertRaises(ProviderError):
            NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.example')

    def test_oversize_response_and_server_error_are_rejected(self):
        self.opener([b'x' * (128 * 1024 + 1), {'code': 'InvalidApiKey', 'message': 'secret'}])
        for _ in range(2):
            with self.assertRaises(ProviderError):
                self.models.complete([])

    def test_check_reports_roles_independently_and_does_not_play(self):
        with patch.object(self.models, 'complete', return_value={'ok': True}), \
                patch.object(self.models, '_recognize', side_effect=ProviderNetworkError('网络连接失败。')), \
                patch.object(self.models, 'synthesize', return_value=wav(24000)):
            result = self.models.check()
        self.assertEqual({role: item['status'] for role, item in result.items()},
                         {'llm': 'success', 'asr': 'failed', 'tts': 'success'})


if __name__ == '__main__':
    unittest.main()
