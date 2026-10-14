---
name: diangetai
description: 搜索 QQ 音乐，按用户配置时长播放一首歌曲，可联动当前形态的舞蹈、表情和灯光。
compatibility: 需要 PrimeBuddy 原生音频工具、T1 ROS 环境和有效的 QQ 音乐登录。
---

# 点歌台

先用 `get_skill_context` 查询本技能的播放时长和联动设置。
通过 `execute_tasks` 调用本技能的原生 `play` 工具，参数仅为 `artist`、`song` 两个字符串，至少一个非空。
例如“听周杰伦的歌”：`{"artist":"周杰伦","song":""}`。
用户明确说“歌曲”“歌名”或用书名号指出标题时，名称填入 song，不要当作歌手。
例如“播放歌曲《甲乙丙丁》”：`{"artist":"","song":"甲乙丙丁"}`。
用户只给出名称且不能确定是歌手还是歌名时，应询问澄清，不凭名称猜测歌手。
执行契约见 [cfg/pkg.yaml](cfg/pkg.yaml)，实现入口见 [src/skill.py](src/skill.py)。

上下文提供用户保存的 `play_seconds`（0 为整首）和 `music_effects`。
任务持续到歌曲结束或配置时限，等待播放器关闭及已开启的联动清理后才返回成功。
可与家庭灯控同批执行；“播放完再关灯”让灯控任务的 after 包含点歌任务 id。
执行结果会回传；失败或未确认时不重新点歌，也不继续依赖任务。

联动打开时，四足趴卧会先起身到四足站立；已稳定站立则保留当前双足/四足模式，再执行对应舞蹈、喜悦表情和灯光。
仅在歌曲缓冲成功后请求起身，站稳才播放；不会双足/四足互相变形，不会自动坐下、趴下或跌倒恢复。
联动未开启时不要声称机器人会跳舞。用户要求改变时长或开关时，提示在终端运行
`primebuddy skill configure diangetai`；模型不能修改配置、增添运动参数或绕过安全检查。
需要有效的 QQ 登录。只返回实际结果，不负责 ASR、LLM、TTS、Agent 模式或麦克风。
配置使用 `primebuddy skill configure diangetai`，也可在技能目录运行 `python3 main.py configure`。
