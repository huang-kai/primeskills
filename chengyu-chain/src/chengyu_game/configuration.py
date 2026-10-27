"""游戏自己的模型配置；只有安装向导允许只读导入 Buddy 默认值。"""
from copy import deepcopy
import math
import os
from pathlib import Path
import re
import tempfile

import yaml

from .voices import TTS_DEFAULT_VOICES, VOICES, voice_label
from .speech import CONFIGURABLE_SPEECH, SpeechError, validate_speech


class ConfigError(ValueError):
    pass


class ConfigureCancelled(ConfigError):
    pass


MODELS = {
    'asr': ('qwen3-asr-flash', 'qwen-audio-3.1-asr-flash'),
    'llm': ('qwen-plus', 'qwen-flash', 'qwen-turbo', 'qwen-max'),
    'tts': ('qwen-audio-3.0-tts-plus', 'qwen-audio-3.0-tts-flash',
            'qwen-audio-3.1-tts-plus', 'qwen-audio-3.1-tts-flash'),
}
DEFAULTS = {'provider': 'qianwen', 'api_host': 'dashscope.aliyuncs.com', 'api_key': '',
            'models': {role: names[0] for role, names in MODELS.items()},
            'voice': 'longanlingxin', 'timeout_seconds': 30, 'speech': dict(CONFIGURABLE_SPEECH)}


class ConfigLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        result = {}
        for key, value in self.construct_pairs(node, deep=deep):
            if not isinstance(key, str) or key in result:
                raise ConfigError('配置键须为唯一文本。')
            result[key] = value
        return result


def validate_config(config, *, require_key=True):
    if not isinstance(config, dict) or config.get('provider') != 'qianwen':
        raise ConfigError('首版仅支持 qianwen provider。')
    host = config.get('api_host')
    if not isinstance(host, str) or (host != 'dashscope.aliyuncs.com' and not re.fullmatch(
            r'[a-zA-Z0-9][a-zA-Z0-9-]{0,62}\.cn-beijing\.maas\.aliyuncs\.com', host)):
        raise ConfigError('API Host 仅支持北京地域官方域名或业务空间域名，不含协议和路径。')
    key = config.get('api_key', '')
    if (not isinstance(key, str) or len(key) > 4096 or any(c.isspace() for c in key)
            or any(ord(c) < 32 or ord(c) > 126 for c in key) or require_key and not key):
        raise ConfigError('请配置游戏自己的有效 API Key。')
    models = config.get('models')
    if not isinstance(models, dict) or any(models.get(role) not in names for role, names in MODELS.items()):
        raise ConfigError('所选 ASR、LLM 或 TTS 模型尚未适配。')
    voice = config.get('voice')
    if not isinstance(voice, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,160}', voice):
        raise ConfigError('TTS 音色标识无效。')
    timeout = config.get('timeout_seconds', 30)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 60:
        raise ConfigError('模型调用超时须为 1 至 60 秒。')
    try:
        validate_speech(config.get('speech'))
    except SpeechError as error:
        raise ConfigError(str(error)) from None
    return deepcopy(config)


def _read_yaml(path):
    path = Path(path)
    try:
        if path.is_symlink() or path.stat().st_size > 128 * 1024:
            raise ConfigError('配置文件类型或大小不符合要求。')
        with path.open(encoding='utf-8') as source:
            result = yaml.load(source, Loader=ConfigLoader)
    except (OSError, yaml.YAMLError, UnicodeError):
        raise ConfigError('无法读取配置文件，请重新配置。') from None
    if not isinstance(result, dict):
        raise ConfigError('配置文件须为键值对象。')
    return result


def load_config(path):
    """只读取指定的游戏配置；不读取 Buddy 或环境变量中的密钥。"""
    return validate_config(_read_yaml(path))


def save_config(path, config):
    config = validate_config(config)
    path = Path(path)
    if path.is_symlink():
        raise ConfigError('不覆盖链接指向的配置。')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.chengyu-config-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            os.fchmod(output.fileno(), 0o600)
            yaml.safe_dump(config, output, allow_unicode=True, sort_keys=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _buddy_defaults(home):
    """导入匹配的 provider/host/key/model 组；不迁移、不写 Buddy 配置。"""
    try:
        app = _read_yaml(Path(home) / 'cfg/config.yaml')
        providers = _read_yaml(Path(home) / 'cfg/providers.yaml')
        settings = providers.get('qianwen', {})
        result = deepcopy(DEFAULTS)
        for role in MODELS:
            options = app.get(role, {})
            reference = options if isinstance(options, str) else options.get('model', '')
            provider, model = reference.split(':', 1)
            if provider != 'qianwen':
                return None
            result['models'][role] = model
        result.update(api_host=settings.get('api_host', ''), api_key=settings.get('api_key', ''))
        if isinstance(app.get('tts'), dict):
            result['voice'] = app['tts'].get('tts_voice', result['voice'])
        return validate_config(result)
    except (ConfigError, AttributeError, ValueError, TypeError):
        return None


def _choose_voice(ui, model, current):
    choices = list(VOICES[model])
    voice = current['voice']
    ids = [item[0] for item in choices]
    known = {item[0] for values in VOICES.values() for item in values}
    default = ids.index(voice) if voice in ids else 0
    if voice not in known:
        choices.append((voice, '保留原自定义音色 · ' + voice))
        if model == current['models']['tts']:
            default = len(choices) - 1
    if model != current['models']['tts'] or voice in known and voice not in ids:
        ui.info('模型已调整，推荐匹配的音色：' + voice_label(model, TTS_DEFAULT_VOICES[model]) + '。')
    labels = [label for _, label in choices] + ['自定义音色 ID…']
    index = ui.choose('TTS 音色', labels, default=default)
    if type(index) is not int or not 0 <= index < len(labels):
        raise ConfigError('音色选项无效。')
    if index == len(choices):
        ui.info('请填写当前 TTS 模型支持的音色 ID；可使用该模型的其他系统音色或专属音色。')
        return ui.ask('自定义音色 ID', voice if voice not in known else '').strip()
    return choices[index][0]


def configure(ui, path, buddy_home=None):
    """所有交互完成并确认后一次保存；取消不改变已有配置。"""
    path = Path(path)
    ui.header('成语接龙 · 配置引导', '方向键选择，回车确认；Ctrl+C 取消且不保存本次修改')
    if path.exists():
        current = load_config(path)
        ui.info('使用游戏已保存的配置作为默认值；回车保留。')
    else:
        current = _buddy_defaults(buddy_home or Path.home() / '.primebuddy')
        ui.info('使用 Buddy 的可支持配置作为安装默认值，保存后独立使用。' if current else
                '未找到可支持的 Buddy 配置，请填写游戏自己的模型配置。')
        current = current or deepcopy(DEFAULTS)
    try:
        ui.header('1 / 3 · 模型服务')
        selected = ui.choose('云 provider', ['qianwen'], default=0)
        if type(selected) is not int or selected != 0:
            raise ConfigError('首版仅支持 qianwen provider。')
        result = deepcopy(current)
        result['speech'] = dict(CONFIGURABLE_SPEECH, **(current.get('speech') or {}))
        result['api_host'] = ui.ask('千问北京地域 API Host', current['api_host']).strip()
        previous_key = current['api_key'] if result['api_host'] == current['api_host'] else ''
        if result['api_host'] != current['api_host']:
            ui.info('服务地址已改变，请重新填写与该地址匹配的 API Key。')
        label = 'API Key（已配置）' if previous_key else 'API Key'
        result['api_key'] = ui.ask(label, previous_key, secret=True).strip()
        ui.header('2 / 3 · 模型与声音', '音色使用官方中文名称，按所选 TTS 模型提供')
        for role, choices in MODELS.items():
            index = ui.choose(role.upper() + ' 模型', list(choices), default=choices.index(current['models'][role]))
            if type(index) is not int or not 0 <= index < len(choices):
                raise ConfigError('模型选项无效。')
            result['models'][role] = choices[index]
        result['voice'] = _choose_voice(ui, result['models']['tts'], current)
        validate_config(result)
        ui.header('3 / 3 · 确认保存')
        ui.info('模型：' + ' / '.join(result['models'][role] for role in MODELS))
        ui.info('声音：' + voice_label(result['models']['tts'], result['voice']))
        ui.info('API Key 已填写；保存后由成语接龙独立使用。')
        if not ui.confirm('保存为游戏自己的配置？'):
            raise ConfigureCancelled('已取消配置，原有设置未改变。')
        save_config(path, result)
    except (KeyboardInterrupt, EOFError):
        raise ConfigureCancelled('已取消配置，原有设置未改变。') from None
    ui.info('游戏配置已独立保存；连接能力需使用模型检查确认。')
    return result
