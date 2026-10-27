"""可配置的儿童播音词；模板只允许当前事件的简单占位符。"""
from string import Formatter
import unicodedata


class SpeechError(ValueError):
    pass


DEFAULT_SPEECH = {
    'hint': '请接“{required_char}”或同音字开头的成语。',
    'opening': '这局挑战{level}，接对{target}次就过关。同音也可以，不用管声调。我先说“{last_idiom}”。',
    'resume': '好，我们继续，当前成语是“{last_idiom}”。',
    'correct': '接对啦，真棒！本局已接对{successes}次，目标是{target}次。我接“{next_idiom}”。',
    'correction': '我听到的是“{answer}”。',
    'win': '太棒了！你已经接对{target}次，赢得了{level}挑战！',
    'win_next': '我们提高难度，挑战{next_level}吧！准备好了就说“继续”。',
    'win_all_next': '高级挑战成功啦！想回到初级再玩一局吗？说“继续”就可以开始！',
    'retry_next': '想再来一局{next_level}吗？说“继续”就可以开始！',
    'lose': '这局没能过关，没关系，下次再试！',
    'timeout_loss': '这次思考时间到了。',
    'assist': '我来帮你一次，可以接“{answer}”。我再接“{next_idiom}”，这次轮到你。',
    'assist_error': '这次我暂时没准备好示范答案，不扣分。再给你一次思考时间。',
    'exit': '好，我们先玩到这里，下次再一起玩吧。',
    'inactivity': '两分钟没有听到你的回答，成语接龙先结束啦。下次再一起玩吧！',
    'provider_error': '语音服务暂时不可用，成语接龙先结束啦。下次再一起玩吧！',
    'asr_retry': '刚才没有听清，不算答错，请再说一次。',
    'start_error': '这次暂时无法开始成语接龙，稍后再来玩吧。',
    'restart_error': '暂时没准备好新题，请稍后再试。',
    'not_started': '游戏还没有开始。',
    'judge_retry': '这次我没判断清楚，请再说一次。',
    'round_over': '这一局已经结束啦。',
    'current_idiom': '现在还是“{last_idiom}”。',
    'level_same': '现在已经是{level}，我们继续。',
    'level_changed': '好，从{level}重新开始挑战，接对{target}次就过关。我下次接词时会调整难度。',
    'not_idiom': '这还不是一个成语，再试一次吧。',
    'uncertain': '这个成语我还没听明白，请完整地再说一次。',
    'wrong_sound': '“{answer}”是成语，不过这次开头的读音没有接上。这局已经有{wrong_attempts}次没有接上了。',
    'wrong_limit': '三次机会用完啦。',
    'motion_unavailable': '动作暂时用不了，我们先玩语音接龙吧。',
}

# 用户配置只允许覆盖这四项；DEFAULT_SPEECH 中其余事件为内部固定文案。
CONFIGURABLE_SPEECH = {key: DEFAULT_SPEECH[key] for key in ('correct', 'win', 'win_next', 'lose')}

_FIELDS = {'correct': {'successes', 'target', 'next_idiom'}, 'win': {'level', 'target'},
           'win_next': {'next_level'}, 'lose': set()}


def validate_speech(overrides=None):
    if overrides is None:
        return {}
    if not isinstance(overrides, dict):
        raise SpeechError('播音词 speech 须为键值对象。')
    for key, value in overrides.items():
        if key not in CONFIGURABLE_SPEECH:
            raise SpeechError('播音词配置含未知键。')
        message = '播音词 ' + key + ' 无效。'
        if (not isinstance(value, str) or not value.strip() or len(value) > 400
                or any(unicodedata.category(char).startswith('C') for char in value)):
            raise SpeechError(message)
        try:
            parts = list(Formatter().parse(value))
            for _, name, spec, conversion in parts:
                if name is not None and (name not in _FIELDS.get(key, set()) or spec or conversion is not None):
                    raise ValueError
            canonical = ''.join(literal.replace('{', '{{').replace('}', '}}') +
                                ('{' + name + '}' if name is not None else '')
                                for literal, name, _, _ in parts)
            if canonical != value:
                raise ValueError
        except ValueError:
            raise SpeechError(message) from None
    return dict(overrides)


def render_speech(overrides, key, **values):
    """渲染已通过 validate_speech 的配置；缺少的事件使用默认词。"""
    if key not in DEFAULT_SPEECH:
        raise SpeechError('播音词配置含未知键。')
    template = (overrides or {}).get(key, DEFAULT_SPEECH[key])
    try:
        return template.format_map(values)
    except (KeyError, ValueError, TypeError, IndexError, AttributeError):
        raise SpeechError('播音词 ' + key + ' 无法填入。') from None
