"""北京地域的常用中文系统音色；自定义音色仍由用户填写。"""

# 官方名称、ID 和模型归属核对于 2026-10-24。仅列常用中文音色，非完整目录。
# https://help.aliyun.com/zh/model-studio/qwen-audio-tts-voice-list
# 3.0 Flash 的部分音色确实使用 _v3.6；不能按模型名推导音色 ID。
SOURCE_URL = 'https://help.aliyun.com/zh/model-studio/qwen-audio-tts-voice-list'
VOICES = {
    'qwen-audio-3.0-tts-plus': (
        ('longanlingxin', '龙安灵心 · 温暖女声'),
        ('longanlufeng', '龙安鲁风 · 开朗男声'),
    ),
    'qwen-audio-3.0-tts-flash': (
        ('longanfengyue', '龙安风悦 · 亲切女声'),
        ('longanxiaoxin', '龙安小昕 · 活泼女声'),
        ('longanlingxi', '龙安灵希 · 甜美女声'),
        ('longjielidou_v3.6', '龙杰力豆 · 天真男童'),
        ('longpaopao_v3.6', '龙泡泡 · 可爱女童'),
        ('longhuohuo_v3.6', '龙火火 · 顽皮少年'),
    ),
    'qwen-audio-3.1-tts-plus': (
        ('longanlingxin_v3.1', '龙安灵心 · 女声'),
        ('xunanchuan_v3.1', '许南川 · 男声'),
        ('longanhuan_v3.1', '龙安欢 · 女声'),
        ('longanfengyue_v3.1', '龙安风悦 · 女声'),
    ),
    'qwen-audio-3.1-tts-flash': (
        ('longanlingxin_v3.1', '龙安灵心 · 女声'),
        ('xunanchuan_v3.1', '许南川 · 男声'),
        ('longanhuan_v3.1', '龙安欢 · 女声'),
        ('longanfengyue_v3.1', '龙安风悦 · 女声'),
        ('wenhuaiqing_v3.1', '温怀清 · 柔和女声'),
        ('yezhiqing_v3.1', '叶知晴 · 轻快女声'),
        ('anmingyuan_v3.1', '安明远 · 清亮男声'),
        ('longjielidou_v3.1', '龙杰力豆 · 天真男童'),
        ('longpaopao_v3.1', '龙泡泡 · 女童'),
        ('longniuniu_v3.1', '龙牛牛 · 阳光男童'),
    ),
}
TTS_DEFAULT_VOICES = {model: choices[0][0] for model, choices in VOICES.items()}


def voice_label(model, voice):
    return dict(VOICES[model]).get(voice, '自定义音色 · ' + voice)
