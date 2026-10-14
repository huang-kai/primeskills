# 点歌台 diangetai

点歌台让启元 T1 通过语音播放 QQ 音乐。你可以指定歌名或歌手，设置每首歌的播放时长，也可以选择播放时联动机器人动作、表情和灯光。

当前版本为 **0.3.0**，配合 **PrimeBuddy 1.0.0** 使用。框架负责语音识别、任务安排和音频播放；本技能负责登录 QQ 音乐、选歌、播放设置及联动。

## 安装与配置

先在 T1 业务板 SoC 1 安装并配置 PrimeBuddy，加载原厂 ROS 2 环境。QQ 音乐登录和歌曲播放需要网络连接。

拿到本技能的 RPK 后，执行：

```bash
primebuddy skill install ./diangetai-0.3.0.rpk
```

也可以直接安装源码目录：

```bash
git clone https://github.com/huang-kai/primeskills.git primebuddy-skills
primebuddy skill install ./primebuddy-skills/diangetai
```

安装向导会依次完成：

1. **登录 QQ 音乐。** 有效的登录态会直接复用；没有有效登录时，显示二维码，扫码并完成授权。
2. **设置播放时长。** 填写 `0～600` 的整数秒，`0` 表示整首播放。首次向导建议 60 秒。
3. **选择音乐联动。** 仅听歌时请选择关闭；开启后，点歌可能让机器人起身或跳舞，请确保周围空旷且可随时急停。配置向导本身不执行动作。

以后修改时长或联动开关，重新运行：

```bash
primebuddy skill configure diangetai
```

配置完成后，重新启动语音会话生效。

## 怎么使用

```bash
primebuddy start
```

用机器人的原厂唤醒词唤醒后，可以说：

- “播放周杰伦的歌。”
- “播放歌曲《晴天》。”
- “播放周杰伦的《晴天》。”

名称无法确定是歌手还是歌名时，机器人会询问。播放时长和联动开关使用终端中保存的配置，语音点歌不会修改这些设置。

播放时长从歌曲实际开始播放时计算，搜索和缓冲时间不计入。如果歌曲提前结束，播放也随之结束。QQ 账号需要有对应歌曲的播放权限；搜索无结果或音源不可用时，会返回实际原因。

首次使用以及空闲超时后需要重新唤醒。歌曲播完后，框架继续处理后续语音请求。

## 音乐联动

开启联动时，机器人会根据当前姿态处理：

- 已稳定站立：保持当前双足或四足形态，执行对应舞蹈、表情和灯光。
- 四足趴卧：歌曲缓冲成功后，先起身到四足站立，确认站稳后再播放。
- 姿态不支持、正在移动、跌倒或状态无法确认：拒绝开始联动。

联动结束后会停止本轮舞蹈、清理表情并恢复灯光。从趴卧起身的机器人会保持站立，不会自动趴回去，也不会在双足和四足之间转换。

如果起身或恢复状态无法确认，技能会保留恢复记录并拒绝下一次联动。应先核对机器人状态和日志，不要删除记录来绕过检查。

## 配置与数据

默认位置如下；自定义框架安装目录时，相应替换路径：

| 内容 | 位置 |
| --- | --- |
| 技能代码和依赖 | `~/.primebuddy/skills/diangetai/` |
| 播放设置 | `~/.primebuddy/cfg/skills/diangetai/config.yaml` |
| QQ 音乐登录态 | `~/.primebuddy/data/diangetai/credential.json` |

例如，先测试 10 秒播放并关闭联动，可以保存：

```yaml
play_seconds: 10
music_effects: false
```

更新已有技能时使用：

```bash
primebuddy skill install ./diangetai-0.3.0.rpk --replace
```

更新和重新配置会复用有效登录态。临时不用时执行 `primebuddy skill disable diangetai`；删除代码及账号数据的步骤见 [管理技能](https://github.com/huang-kai/primeskills#管理技能)。

## 开发与打包

代码按用途分为：

| 位置 | 职责 |
| --- | --- |
| `SKILL.md`、`cfg/pkg.yaml` | 技能说明、包信息和业务入口声明 |
| `src/skill.py` | `play` 入口和业务流程 |
| `src/setup.py`、`src/configuration/` | 登录、二维码和播放设置向导 |
| `src/music/` | QQ 音乐登录态、搜索和音源获取 |
| `src/effects/` | 动作、表情和灯光联动 |
| `tests/` | 配置、播放及联动恢复的主要测试 |

Python 依赖以根目录 `requirements.txt` 为准，安装器会放到技能自己的 `_deps/`。`rclpy` 和 `aimdk_msgs` 使用机器人已有环境。

框架与技能仓库相邻时，从技能仓库根目录执行：

```bash
python3 diangetai/main.py --check
PYTHONPATH=../primebuddy/src python3 -m unittest discover -s diangetai/tests -v
```

`--check` 校验本地技能说明和入口文件。测试使用模拟接口，不登录真实账号或控制机器人。

从框架源码目录打包：

```bash
python3 src/primebuddy/tools/build_release.py --skill ../primebuddy-skills/diangetai
```

输出为 `dist/diangetai-0.3.0.rpk`，其中包含 README 和 LICENSE。发布新版本前修改 `cfg/pkg.yaml` 的 `version`；私有配置和登录数据保存在框架的数据目录中。贡献步骤见 [仓库说明](https://github.com/huang-kai/primeskills#参与贡献)。

## 开源协议

本技能采用 [MIT 协议](LICENSE)。独立分发时请保留 LICENSE；第三方依赖遵循各自的协议。
