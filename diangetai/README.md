# diangetai · PrimeBuddy 官方点歌台 skill

版本 **0.3.0**。独立技能项目，使用标准 SKILL.md 和 PrimeBuddy 0.4.0 的可选原生工具适配、系统 Python 3.10+ 和机器人已有 ROS 2。QQ 音乐登录、二维码、选曲、播放时长、舞蹈、表情与灯光都由本技能管理；框架管理 ASR、LLM、TTS、音频互斥和 Agent 模式恢复。

## 安装与重新配置

源码位于 [huang-kai/primeskills 的 diangetai 目录](https://github.com/huang-kai/primeskills/tree/main/diangetai)。从技能集合生成本技能 ZIP 后安装：

```bash
git clone https://github.com/huang-kai/primeskills.git primebuddy-skills
git -C primebuddy-skills archive --format=zip --output=../diangetai-skill-0.3.0.zip HEAD:diangetai
```

```bash
primebuddy skill install ./diangetai-skill-0.3.0.zip
# 已安装旧版时：
primebuddy skill install ./diangetai-skill-0.3.0.zip --replace
# 以后修改时长或联动开关：
primebuddy skill configure diangetai
```

框架支持 ZIP 和根目录包含清单的单技能 Git 仓库；本技能位于多技能仓库中，使用上面的 ZIP 安装方式。安装与重新配置调用相同的技能向导：

1. 有效 QQ 登录直接复用；否则由技能自己的 `src/configuration/qr.py` 展示登录二维码，并向服务端确认登录成功。图片权限为600，终端过窄或渲染失败时显示私有图片路径；成功、失败或取消都会清理图片。
2. 填写每首播放的整数秒数：`1～600`，`0` 表示整首。首次预填60，重新配置预填已保存的数值。歌曲先自然结束时不会补足时长。
3. 选择是否启用音乐联动。开启后每次点歌会先将四足趴卧状态准备为四足站立；已站立则保持原双足/四足模式，再执行舞蹈、喜悦表情和循环灯光。配置过程不产生运动；点歌前须场地空旷并可随时急停。

修改后重新启动 PrimeBuddy 语音会话生效。旧配置没有新增字段时，仍整首播放且不启用联动；不会因升级自动跳舞。模型可读取当前时长和开关，但不能自行更改配置或选择其他运动。

PrimeBuddy 0.4.0 使用 `primebuddy start` 启动，并随包提供点歌台0.3.0。框架向导会选中较新的随包技能供一起更新，已保存且有效的 QQ 登录态继续复用。点歌台向导只依赖通用 UI 控件，不调用 `ui.qr()`。

配置位于 `PRIMEBUDDY_HOME/cfg/skills/diangetai/config.yaml`，包含 `play_seconds` 和 `music_effects`。可直接修改 YAML，退出并重新启动平台后生效；`play_seconds: 0` 表示整首，`music_effects: false` 关闭联动。登录态仍在 `PRIMEBUDDY_HOME/data/diangetai/credential.json`，升级与改时长不会覆盖登录态。QQ 账号须有对应歌曲的播放权限；不绕过权限或将试听当整曲。

## 播放与联动

当前续听修复须与提供 `context.audio` 的框架一起更新。点歌台只向 `context.audio.play(url, max_seconds=limit, expected_seconds=song.interval)` 提交音源和时长；框架负责转码及原厂播放。原厂在播放期间保持已有会话，播完后重新开始接话计时；不再直接向 PulseAudio 推流。首次仍需唤醒，已经空闲超时的会话不能靠播放重新激活。

框架按片段本身的 PCM 帧数限定播放时长，搜索、缓冲和提示语不计时。停止只针对本曲 trace，确认后清理临时文件，才返回成功。歌结束、到时、取消或出错都会停歌并尝试清理联动。适配原厂媒体格式会降低音乐带宽；长时间网络等待仍可能导致会话超时。

- 双足站立使用 `BIPED_MIMIC_POPPING`；四足站立使用 `QUAD_MIMIC`。四足趴卧可起身到四足站立，已站立则保持原模式，不执行双足/四足变形或跌倒恢复。
- 开播前检查当前姿态、反馈新鲜度和可打断动作资源。仅允许四足形态的 PASSIVE_DEFAULT（1）或四足趴下（110）起身；歌曲缓冲成功后再次核对，发送一次四足站立（101），等待请求后的新站立反馈，再开始播放。缺接口、坐下/未知状态、形态不匹配、正在移动、跌倒、被提起或手持时拒绝准备。
- 确认歌曲开始后启动舞蹈和一组喜悦表情，灯条按节奏换色。动作完成可续播，运行中的动作不叠加。
- 结束时回到本轮准备好的站立模式、恢复原灯光，并请求空表情；从趴卧起身的会保持四足站立，不自动趴回去。姿态和灯光等待反馈；表情接口只能确认请求被接受，不能证明屏幕已显示。
- 发现外部姿态接管，不强制覆盖。起身结果未确认或恢复失败时保留 `PRIMEBUDDY_RUNTIME/quad_effects_music.json` 并拒绝下一次联动，不盲目重发控制；不要直接删除该记录绕过检查。

`src/setup.py` 为引导，`configuration/qr.py` 管理二维码展示和临时图片，`configuration/playback.py` 为配置校验，`skill.py` 编排业务，`effects/music_effects.py` 管理动作/灯光/表情，`effects/session.py` 管理本技能独立 ROS 节点及清理。技能不会重入语音线程的执行器，不切换 Agent 模式，不创建虚拟环境。`rclpy` 和 `aimdk_msgs` 使用 T1 已安装版本；不打包或替换厂商 SDK。

## 开发与发布

根目录 `requirements.txt` 的依赖为 `qqmusic-api-python==0.7.3` 和 `Pillow==11.3.0`，由框架装进技能私有依赖目录。将框架克隆到 `primebuddy/`、本仓库克隆到相邻的 `primebuddy-skills/` 后，从它们的父目录执行：

```bash
python3 primebuddy/src/primebuddy/tools/build_release.py --skill-only primebuddy-skills/diangetai --output /tmp/diangetai-skill-0.3.0.zip
PYTHONPATH=primebuddy/src python3 -m unittest discover -s primebuddy-skills/diangetai/tests -v
```

发布包不包含账号、运行记录、虚拟环境或 `_deps`。本地测试覆盖配置重入、播放器时限、技能生命周期及联动恢复，云端和机器人控制均使用模拟接口；实机只读核对不代表实际舞蹈和灯光已验收。


0.2.1 修复选歌错误提示：搜索无结果、把歌名当歌手、资源不可用等已知业务错误会直接说明原因；未知第三方异常只输出阶段与异常类型，不输出签名 URL 或凭据。按歌名点播可说“播放歌曲《甲乙丙丁》”，按歌手点播可说“播放周杰伦的歌”；分类不匹配时不会静默改播。

0.2.2 增加四足趴卧起身准备；实机只读确认接口与准备条件，尚未执行实际起身测试。

## 本地目录与验证

根目录保留 `main.py`、`README.md`、`SKILL.md`、`requirements.txt` 和 Git 忽略文件。配置在 `cfg`，实现代码在 `src`，静态资源在 `resource`，主要功能测试在 `tests`。`docs` 是可选的本地开发文档，暂不提交 Git。

```bash
python3 main.py --check
```

该 --check 入口仅校验本地说明和文件路径，不执行业务；python3 main.py configure 可独立运行 Console 向导。开发时使用相邻框架源码或通过 PYTHONPATH 提供框架；安装到 ~/.primebuddy/skills 后自动找到同一目录下的框架。框架测试脚本会执行本技能的离线回归；本次重构没有部署或进行实机验收。默认配置见 `cfg/config.yaml`；原生业务入口为 play(context, artist, song)，不再使用旧 run 契约。

包元信息使用 cfg/pkg.yaml，与工作区启元技能样例保持字段风格：user_id、version、sdk_version、package_name、author、description。作者为 PrimeBuddy，user_id 为 robot_local，sdk_version 为 v1.0.0；SDK 字段是开发参考信息，易来仍不直接调用机器人 SDK。PrimeBuddy 运行声明集中在 primebuddy 节点。依赖以 requirements.txt 为准，SKILL.md 在根目录。

也可直接 primebuddy skill install ./diangetai，或把目录复制到 PRIMEBUDDY_HOME/skills/diangetai 后配置并重启会话。平台按需读取 SKILL.md，查询播放上下文、执行 play 并回传真实结果；音频与联动清理逻辑保留。此次 Agent 工具循环尚未实机验收。
