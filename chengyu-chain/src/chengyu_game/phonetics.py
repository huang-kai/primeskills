"""本地读音核验；只放宽声调和指定鼻音，不判断成语真伪或用户意图。"""
import re
import unicodedata

from pypinyin import lazy_pinyin


def sound(value):
    """规范单音节；保留 u/ü，仅合并 an/ang、en/eng、in/ing。"""
    if not isinstance(value, str) or not value.strip() or len(value) > 12:
        raise ValueError('拼音格式无效')
    value = re.sub(r'[1-5]$', '', value.strip().lower().replace('u:', 'v'))
    result = ''
    for char in unicodedata.normalize('NFD', value):
        if char == '\u0308':
            if not result.endswith('u'):
                raise ValueError('拼音格式无效')
            result = result[:-1] + 'v'
        elif char not in '\u0304\u0301\u030c\u0300':
            result += char
    if not re.fullmatch(r'[a-z]{1,6}', result):
        raise ValueError('拼音格式无效')
    nasal = re.fullmatch(r'(zh|ch|sh|[bpmfdtnlgkhjqxrzcsyw]?)(ang|eng|ing)', result)
    return result[:-1] if nasal else result


def _pronunciation(text):
    # 默认词组读音；不枚举多音字来拼凑答案，未知字保留原文并校验失败。
    values = lazy_pinyin(text, errors='default')
    if len(values) != len(text):
        return None
    try:
        return tuple(sound(value) for value in values)
    except ValueError:
        return None


def matching_transcription(raw_text, idiom_text):
    """核验一个完整同音转写片段；语言歧义和成语真伪仍交由游戏裁判。"""
    if (not isinstance(raw_text, str) or not raw_text or len(raw_text) > 4000
            or not isinstance(idiom_text, str)
            or not re.fullmatch(r'[\u3400-\u9fff]{2,12}', idiom_text)):
        return False
    text = ''.join(char for char in raw_text
                   if not char.isspace() and not unicodedata.category(char).startswith('P'))
    size = len(idiom_text)
    fragments = {text[index:index + size] for index in range(len(text) - size + 1)
                 if re.fullmatch(r'[\u3400-\u9fff]+', text[index:index + size])}
    matches = {idiom_text} if idiom_text in fragments else set()
    expected = _pronunciation(idiom_text)
    if expected is not None:
        matches.update(fragment for fragment in fragments if fragment != idiom_text
                       and _pronunciation(fragment) == expected)
    return len(matches) == 1
