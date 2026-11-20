"""Read the operator's YAML without printing secrets. Legacy JSON is import-only."""

import json
import math
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit



def read_private(path):
    if path.stat().st_mode & 0o077:
        raise ValueError('请先将配置文件权限设为 600。')
    return path.read_text()


def required(settings, field):
    value = settings.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError('配置缺少文本字段 ' + field)
    return value


def phone_number(phone):
    if not isinstance(phone, str) or not re.fullmatch(r'1[0-9]{10}', phone):
        raise ValueError('contact.phone 应为带引号的 11 位中国内地手机号。')
    return phone


def validate_sms(sms):
    phone_number(sms.get('phone'))
    for key in ('sign_name', 'template_code', 'time_variable', 'access_key_id', 'access_key_secret'):
        required(sms, key)
    if not re.fullmatch(r'SMS_[0-9]+', sms['template_code']):
        raise ValueError('无效的短信模板 CODE。')
    return sms


def section(config, key):
    if not isinstance(config, dict) or not isinstance(config.get(key), dict):
        raise ValueError('配置缺少 ' + key + ' 分组。')
    return config[key]


def parse(text):
    import yaml
    try:
        settings = yaml.safe_load(text)
    except yaml.YAMLError:
        raise ValueError('配置 YAML 格式有误，请检查缩进和引号。') from None
    phone = phone_number(section(settings, 'contact').get('phone'))
    sms = section(settings, 'sms')
    provider = sms.get('provider')
    if provider == 'aliyun':
        validate_sms(dict(section(sms, provider), phone=phone))
    elif provider == 'https_gateway':
        gateway = section(sms, provider)
        url = urlsplit(required(gateway, 'url'))
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.fragment:
            raise ValueError('短信网关必须是 HTTPS URL，令牌请单独配置。')
        try:
            _ = url.port
        except ValueError:
            raise ValueError('短信网关端口有误。') from None
        if not isinstance(gateway.get('bearer_token', ''), str):
            raise ValueError('网关令牌应为文本。')
    else:
        raise ValueError('sms.provider 须为 aliyun 或 https_gateway。')
    for group, key in [('detection', 'hold_seconds'), ('voice', 'response_seconds')]:
        value = section(settings, group).get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f'{group}.{key} 应为正数。')
    return settings


def imported_yaml(text, template):
    """Translate pre-skill credentials once. Keep the old file and all other defaults."""
    import yaml
    legacy = json.loads(text)
    sms = validate_sms(legacy.get('sms', legacy))
    settings = yaml.safe_load(template)
    settings['contact']['phone'] = sms['phone']
    settings['sms']['aliyun'] = {key: sms[key] for key in settings['sms']['aliyun']}
    for key in ('voice', 'detection'):
        if key in legacy:
            settings[key].update(legacy[key])
    result = yaml.safe_dump(settings, sort_keys=False, allow_unicode=True)
    parse(result)
    return result


def write_private(path, text):
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
