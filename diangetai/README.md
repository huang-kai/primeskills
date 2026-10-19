# 点歌台 diangetai

点歌台支持启元 T1/Q1，可独立播放 QQ 音乐，也可由语音助手调用。指定歌手或歌名即可点歌，播放时长和可选舞蹈、表情、灯光联动使用技能自己的配置。

当前包版本 **0.3.0**。业务程序不导入 PrimeBuddy，也不要求宿主传入 Context 或播放器。原厂 SDK、歌曲转码、播放生命周期与清理由技能自己管理。

## 独立安装与配置

需要 Python 3.10 或更新版本、目标机器人原厂 ROS 2 环境、原厂消息包、GStreamer 音频解码环境和可用网络。Python 依赖见 `requirements.txt`；`rclpy`、`aimdk_msgs` 使用机器人已有环境，不替换原厂消息包。

在技能目录安装依赖并加载机器人环境后：

```bash
python3 -m pip install -r requirements.txt
python3 main.py --check
python3 main.py configure
```

配置会复用有效 QQ 登录态，否则显示二维码，扫码后向服务器核实登录；随后设置 `0～600` 秒的播放时限（`0` 表示整首）及联动开关。配置过程不执行动作。

联动开启后点歌可能让机器人起身或跳舞，操作前须确认现场空旷、姿态合适且可急停。只听歌时关闭联动。Q1 舞蹈的执行、恢复和打断兼容仍未完成完整实机验收。

## 命令

以下命令在技能目录运行，公共路径参数放在子命令前：

```bash
python3 main.py context
python3 main.py check-play --artist 周杰伦
python3 main.py play --artist 周杰伦 --song 晴天
python3 main.py play --song 小星星 --seconds 30
python3 main.py play --song 小星星 --simulate
python3 main.py status
```

- `context`：读取保存的播放时长、联动开关和机型适配说明，不接管麦克风或查询 QQ 账号秘密。
- `check-play`：检查歌手/歌名、播放设置及待恢复记录，不取流、不播放、不执行动作。
- `play`：歌手、歌名至少填一个。`--seconds` 覆盖本次时长，不修改保存配置；`--simulate` 不连接机器人或 QQ。命令沿用保存的联动开关，开启时可能产生动作；已有 `--effects` 仅供操作者明确要求本次启用联动。
- `status`：检查当前进程锁和恢复记录，不调用机器人；`resources_released: true` 才表示没有本技能已知的未确认操作。
- `recover`：在没有点歌进程持锁时，尝试停止恢复记录中的自有媒体 trace，并等待原厂终态。它不自动执行动作、表情或灯光恢复。

业务命令通过 stdout 返回 JSON，stderr 输出诊断；`status=success` 且退出码为 `0` 表示该命令完成，失败和取消返回非零。点歌结果还包含 `resources_released`，它与歌曲是否播放成功是两件事。状态未确认时保留记录，不能据退出码或进程消失声称机器人已停止。

## 配置与运行目录

```bash
python3 main.py --config /path/config.yaml \
  --data-dir /path/data --runtime-dir /path/runtime play --artist 周杰伦
```

| 参数 | 对应环境变量 | 内容 |
| --- | --- | --- |
| `--config` | `DIANGETAI_CONFIG` | 私有 YAML 播放设置，文件权限须为 `600` |
| `--data-dir` | `DIANGETAI_DATA` | QQ 登录态与私有数据 |
| `--runtime-dir` | `DIANGETAI_RUNTIME` | 本技能最终运行目录，存放进程锁和恢复记录 |

默认数据目录保留 `~/.primebuddy/data/diangetai`，以复用已有登录；该路径名称不代表需要安装框架。运行目录默认是数据目录下的 `runtime/`。配置优先使用显式路径或技能环境变量，否则复用数据目录已有 `config.yaml` 或旧 `~/.primebuddy/cfg/skills/diangetai/config.yaml`。旧 `PRIMEBUDDY_*` 环境变量仅用于已有部署的数据兼容。

配置示例：

```yaml
play_seconds: 30
music_effects: false
```

T1/Q1 型号从原厂身份信息识别，不限定部署在哪块板。原厂媒体服务主机用 `PRIMEBOT_AUDIO_HOST=local` 或 `user@host` 指定；缺省采用已确认的 SoC 0 服务拓扑。本地不走 SSH；跨机播放需要先准备服务端音频文件，不能假设文件系统共享。

## 播放、续听与取消

媒体通过原厂 Agent 的 `PlayMediaFile` 播放，按本次唯一 trace 等待真实 PLAYING/END。最长时长先编码进音频文件，搜索和缓冲时间不计入播放时长；歌曲提前结束时随之结束。QQ 搜索成功不代表取得音源，取流错误不能单凭错误码判断 VIP 原因，不绕过权限。

独立命令不切换 Agent 模式、不接管麦克风。原厂会话须已被唤醒，音乐不会从 IDLE 主动唤醒。语音宿主应等待音乐与清理结束，再安排播报和收音；不要改用直接 Pulse/TCP 或 `PlayAudioFile` 后宣称具有相同续听生命周期。当前不保证播放中再次唤醒会取消歌曲；跨轮“再来一首”的点歌上下文仍需宿主修复。

SIGINT、SIGTERM、SIGHUP 会触发协作取消，停止本次 trace 并完成已开启的联动清理。强杀不会运行 `finally`；媒体请求提交前会保存恢复记录，只有收到本次 END 或目标消息明确声明的 STOP/STOPPED 才清除；ERROR 等失败反馈不能作为已停止的证据。恢复使用与原任务相同的运行目录：

```bash
python3 main.py --runtime-dir /path/runtime status
python3 main.py --runtime-dir /path/runtime recover
```

停止请求提交前也会保存标记；新 `recover` 进程读到已请求停止时只等待确认，不重复发送未知结果的停止请求。如果停止未确认，记录保留；不要据超时或删除记录判断停止成功。若存在联动恢复记录，`recover` 只报告需要现场核对，不自动重放动作或删除记录。原厂确认反馈已错过等情况也可能无法自动确认，需要结合现场和原厂状态处理。旧 `runtime/diangetai/` 子目录中的恢复记录也会阻止误报资源已释放。

## 音乐联动

- T1 保持已稳定的双足/四足模式，分别选择对应原厂舞蹈和表情。四足趴卧只在歌曲缓冲完成后准备起身，站稳才播放；不自动进行形态切换、坐下、趴下或跌倒恢复。
- Q1 只接受稳定 `BIPED_WALK_RUN(1002)`，使用登记的 `DANCE_LOCKING`、`MIMIC(1003)` 与对应表情；不要求 T1 形态字段，不自动起身或切换形态。不能承诺随时打断或已稳定通过实机运动验收。
- 正常退出时恢复本轮入口模式、表情和自身控制的灯光；从趴卧起身后保持站立。恢复未确认时拒绝再次联动，保留记录。

## 接入 PrimeBuddy

配合包含 CLI 宿主适配的 PrimeBuddy 使用：

```bash
primebuddy skill install ./diangetai-0.3.0.rpk
primebuddy skill configure diangetai
```

宿主适配定义在 Buddy 自己的配置中，将参数、资源、期限、预检和恢复命令映射到本技能已有 CLI。技能的 `cfg/pkg.yaml` 只保留包信息，不声明 Buddy Python 入口。详细分工见 [宿主接入说明](references/buddy.md)。

## 开发与验证

`src/skill.py` 是普通业务流程，`src/music/` 负责 QQ，`src/robot/` 负责原厂媒体与音频准备，`src/effects/` 负责 T1/Q1 联动，`src/recovery.py` 负责操作记录。业务代码不要求实现宿主函数。

```bash
python3 main.py --check
python3 -m unittest discover -s tests -v
```

每个播放进程在提交 RPC 前等待媒体状态订阅匹配；Humble 缺少 Python 匹配计数接口时使用同版公开 rcl 只读 API，不能取得匹配证据时不播放。测试使用模拟接口，不登录账号或控制机器人；新子进程的 DDS 匹配与取消终态仍需实机回归，模拟通过不能当作实机验收。由框架源码目录运行 `python3 src/primebuddy/tools/build_release.py --skill ../primebuddy-skills/diangetai` 可打 RPK；包内携带 README 和 LICENSE，不含私有配置与登录态。

## 开源协议

采用 [MIT 协议](LICENSE)。第三方依赖和原厂 SDK 遵循各自协议。
