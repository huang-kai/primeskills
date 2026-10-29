# 点歌台 diangetai

点歌台支持启元 T1/Q1，可独立播放 QQ 音乐，也可由语音助手调用。指定歌手或歌名即可点歌，播放时长和可选舞蹈、表情、灯光联动使用技能自己的配置。

当前准备包版本 **0.4.1**。业务程序不导入 PrimeBuddy，也不要求宿主传入 Context 或播放器。原厂 SDK、歌曲转码、播放生命周期与清理由技能自己管理。

## 独立安装与配置

需要 Python 3.10 或更新版本、目标机器人原厂 ROS 2 环境、原厂消息包、GStreamer 音频解码环境和可用网络。Python 依赖见 `requirements.txt`；`rclpy`、`aimdk_msgs` 使用机器人已有环境，不替换原厂消息包。

在技能目录安装依赖并加载机器人环境后：

```bash
python3 -m pip install -r requirements.txt
python3 main.py --check
python3 main.py configure
```

配置会复用有效 QQ 登录态，过期时先自动续期；只有首次登录、缺少刷新凭据或刷新授权已失效时才提示扫码，扫码后向服务器核实登录。随后设置 `0～600` 秒的播放时限（`0` 表示整首）及联动开关。配置过程不执行动作。

联动开启后点歌可能让机器人起身或跳舞，操作前须确认现场空旷、姿态合适且可急停。只听歌时关闭联动。Q1 A/B/C 连续轮换、音乐自然结束后的归位已通过实机测试；提前停止音乐的原厂接口已出现返回成功但未停止的情况，尚不可靠。

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

同一台设备的配置和播放须使用同一个数据目录。升级或重装时保留其中的 `device.json`（设备身份与会话）和 `credential.json`（完整登录及刷新凭据）；不要把设备文件当作临时缓存删除，也不要把同一身份复制给其他机器。文件权限为 `600`，目录权限为 `700`，发布包不包含这些私有数据。

配置检查和取流遇到登录过期时会使用原设备身份续期，成功后原子保存完整新凭据并更新当前客户端；取流最多重试一次。配置、扫码和取流共用数据目录中的 `account.lock`，等待最多 30 秒且可取消，防止多个进程同时刷新或覆盖登录态。刷新成功后的后续检查即使断网，也保留已轮换的新凭据。

网络超时、设备超限、频率限制和其他业务拒绝不会自动转为扫码。设备超限时停止本次配置，请按 QQ 音乐 App 的设备管理提示处理既有记录；代码不能自动清除后台设备记录。仅刷新授权明确失效（`1000/104401/104400`）或没有可用刷新字段时，已有账号才进入重新扫码流程。服务端仍可撤销授权，本技能不承诺永久免登录。

配置示例：

```yaml
play_seconds: 30
music_effects: false
```

T1/Q1 型号从原厂身份信息识别，不限定部署在哪块板。原厂媒体服务主机用 `PRIMEBOT_AUDIO_HOST=local` 或 `user@host` 指定；缺省采用已确认的 SoC 0 服务拓扑。本地不走 SSH；跨机播放需要先准备服务端音频文件，不能假设文件系统共享。

## 播放、续听与取消

媒体通过原厂 Agent 的 `PlayMediaFile` 播放，按本次唯一 trace 等待真实 PLAYING/END。最长时长先编码进音频文件，搜索和缓冲时间不计入播放时长；歌曲提前结束时随之结束。QQ 搜索成功不代表取得音源，取流错误不能单凭错误码判断 VIP 原因，不绕过权限。

独立命令不切换 Agent 模式、不接管麦克风。原厂会话须已被唤醒，音乐不会从 IDLE 主动唤醒。语音宿主应等待音乐与清理结束，再安排播报和收音；不要改用直接 Pulse/TCP 或 `PlayAudioFile` 后宣称具有相同续听生命周期。本版 Buddy 在音乐播放期间不响应唤醒词业务指令，等播完后再说下一句；唤醒打断留待后续版本，原厂仍可能自行应答。跨轮“再来一首”的点歌上下文仍需宿主修复。Q1 已观察到 `StopTtsTraceId` 接受请求后音乐仍自然播完，不能把取消请求成功当作声音已停；未确认终态时保留记录并阻止再次接管。

SIGINT、SIGTERM、SIGHUP 会触发协作取消，停止本次 trace 并完成已开启的联动清理。强杀不会运行 `finally`；媒体请求提交前会保存恢复记录，只有收到本次 END 或目标消息明确声明的 STOP/STOPPED 才清除；ERROR 等失败反馈不能作为已停止的证据。恢复使用与原任务相同的运行目录：

```bash
python3 main.py --runtime-dir /path/runtime status
python3 main.py --runtime-dir /path/runtime recover
```

停止请求提交前也会保存标记；新 `recover` 进程读到已请求停止时只等待确认，不重复发送未知结果的停止请求。如果停止未确认，记录保留；不要据超时或删除记录判断停止成功。若存在联动恢复记录，`recover` 只报告需要现场核对，不自动重放动作或删除记录。原厂确认反馈已错过等情况也可能无法自动确认，需要结合现场和原厂状态处理。旧 `runtime/diangetai/` 子目录中的恢复记录也会阻止误报资源已释放。

## 音乐联动

- T1 保持已稳定的双足/四足模式，分别选择对应原厂舞蹈和表情。四足趴卧只在歌曲缓冲完成后准备起身，站稳才播放；不自动进行形态切换、坐下、趴下或跌倒恢复。
- Q1 只接受稳定 `BIPED_WALK_RUN(1002)`。音乐真实开始后切入原厂全身控制模式，随机轮换预设跳舞 A/B/C（3046/3047/3048），每段原厂任务确认完成才开始下一段，不连续重复同一段。音乐结束后提交默认姿态并确认完成，再恢复 1002 静止；不从被动或躺姿自动起身。2026-10-20 的 60 秒正式 CLI 实测连续执行 13 段并成功归位，用户确认三个动作幅度合适、全程与结束站立正常。该轮未重测宿主语音和播完续听。原厂接口和限制见 [Q1 预设联动](references/q1-motion.md)。
- 正常退出时恢复本轮入口模式、表情和自身控制的灯光；从趴卧起身后保持站立。恢复未确认时拒绝再次联动，保留记录。

## 接入 PrimeBuddy

本版本配套 PrimeBuddy 1.2.0。框架声明的点歌台兼容范围为 `>=0.4.0, <0.5.0`，推荐 `0.4.1`；后续以目标框架的 `release.json` 为准。首次安装技能：

```bash
primebuddy skill install ./diangetai-0.4.1.rpk
primebuddy skill configure diangetai
```

宿主适配定义在 Buddy 自己的配置中，将参数、资源、期限、预检和恢复命令映射到本技能已有 CLI。技能的 `cfg/pkg.yaml` 只保留包信息，不声明 Buddy Python 入口。详细分工见 [宿主接入说明](references/buddy.md)。

已有托管安装时，先退出语音会话，再用 `primebuddy update --check` 检查，使用 `primebuddy update` 联合更新框架与已安装技能；原配置、QQ 登录、设备身份和启用状态保持。旧框架 1.0.0 尚无更新命令，首次须在原安装板解压新框架包并运行 `source ./scripts/install.sh`。外部源码目录由用户自行更新，更新器只检查和提示。旧点歌台 0.3.0 不能继续使用新版 Buddy 的 CLI 适配；独立使用本技能不要求安装 Buddy。

## 开发与验证

`src/skill.py` 是普通业务流程，`src/music/` 负责 QQ，`src/robot/` 负责原厂媒体与音频准备，`src/effects/` 负责 T1/Q1 联动，`src/recovery.py` 负责操作记录。业务代码不要求实现宿主函数。

```bash
python3 main.py --check
python3 -m unittest discover -s tests -v
```

每个播放进程在提交 RPC 前等待媒体状态订阅匹配；Humble 缺少 Python 匹配计数接口时使用同版公开 rcl 只读 API，不能取得匹配证据时不播放。测试使用模拟接口，不登录账号或控制机器人；新子进程的 DDS 匹配与取消终态仍需实机回归，模拟通过不能当作实机验收。由框架源码目录运行 `python3 src/primebuddy/tools/build_release.py --skill ../primebuddy-skills/diangetai` 可打 RPK；包内携带 README 和 LICENSE，不含私有配置与登录态。

## 开源协议

采用 [MIT 协议](LICENSE)。第三方依赖和原厂 SDK 遵循各自协议。

## Buddy 技能安装协议 v1（开发约束）

新平台技能在 `cfg/pkg.yaml` 声明 `primebuddy.version: 1`，并必须提供 `primebuddy.cli.configure`。这是统一入口；不要求所有作者使用同一个文件名，作者可自行实现交互向导。例如：

```yaml
primebuddy:
  version: 1
  mode: task  # 多轮独占应用使用 exclusive
  cli:
    configure: [main.py, --config, '{config}', --data-dir, '{data}', --runtime-dir, '{runtime}', configure]
```

- `configure` 是非空参数列表，第一项必须是技能包内实际存在的脚本；不得越界、使用 shell 拼接命令或动态生成脚本路径。缺少入口、文件不存在、参数模板非法或协议不受支持时拒绝安装该协议包。
- Buddy 在子进程中调用作者的向导，并传入该技能专属的配置、持久数据和运行时路径。向导自行呈现问题、保存配置和验证；成功退出码为 0，失败或取消返回非 0。即使无需配置，也须提供可成功退出的入口。
- 向导须可重复运行，保留已有配置和登录态，取消后不得伪装成成功；不在安装阶段起身或执行身体动作。新装配置失败会保留待配置文件但不启用；替换现有版本时配置失败回滚旧安装。平台未勾选的技能不会执行向导或安装依赖。
- 协议包以自身 CLI 声明为准，不受 Buddy 内置旧技能映射覆盖；用户 `cfg/skill-adapters.yaml` 的显式覆盖仍优先，但须保留配置入口。独占应用还须提供 `status`、`recover` 和前台启动动作，并遵循资源释放约定。
- 技能通过 CLI 和文件交换数据，不导入 Buddy 内部 Python 模块。配置和持久数据保存在传入目录中，不写入随升级替换的源码目录；依赖固定在技能自己的 `requirements.txt`，由平台隔离安装到技能 `_deps`。
- `primebuddy.version` 表示宿主交互契约，独立于包顶层的业务 `version`。Buddy 的兼容升级应继续支持 v1；破坏参数、退出码或资源管理语义时必须引入新协议并提供迁移路径。已安装的旧文档技能、旧 setup/适配器继续兼容；正式发行内置技能必须采用 v1，新技能不要依赖旧适配器。

平台升级保留兼容技能的代码、配置和数据，不因 `recommended_version` 更高就替换。未安装技能保持未安装。目标平台不兼容的托管官方技能仍按清单执行事务性配套更新；外部目录不被覆盖，不兼容的启用技能会阻止升级。主动更新兼容技能可在 `primebuddy onboard` 中勾选随包新版本，或用 `primebuddy skill install <包> --replace` 调用其向导。协议兼容不代表不同机器人固件或外部服务已自动通过验收。


### 机载 SDK 与机型适配

T1/Q1 统一使用 `/robot/software/interface/aimdk_msgs` 的 Python 消息、类型支持动态库和包环境钩子；标准包缺失时明确失败，不加载 housekeeper/app_proxy 的副本。独立 `python3 main.py` 与 Buddy 托管启动遵守同一规则。

动作分别选择 Q1、T1 双足和 T1 四足策略，不自动切换双足/四足。T1 双足 201 是过渡到稳定控制 300 的入口，结束后保持 300；四足保持原四足形态。T1 资源查询显式指定形态；已核对的命名 Mimic 可接受资源类型 `NONE=0` 或 `MIMIC=2`，仍须存在且允许打断，其他类型拒绝。

资源查询通过不代表已完成实体动作验收。未知任务结果不重发；检测到外部任务接管时不强制恢复。身体动作须在有人看护、场地和姿态适合时按机型分别验证。
