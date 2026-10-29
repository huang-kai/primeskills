# 易来灯控 yeelight

易来灯控通过本地 `yeelight-home` MCP 接口打开或关闭灯具、房间或全屋灯光。当前准备包版本 **0.3.1**，业务和命令行入口均可独立运行，不导入 PrimeBuddy。

当前只接入开关，不支持亮度、色温、渐变、闪烁、循环变色或场景。

## 安装与配置

需要 Python 3.10 或更新版本、可执行的 `yeelight-home`、已登录账号、家庭访问权限和家庭 ID。账号仍由 `yeelight-home` 管理，本技能不会代为安装或登录外部工具。

在技能目录执行：

```bash
python3 -m pip install -r requirements.txt
python3 main.py --check
python3 main.py configure
```

配置向导询问可执行文件、家庭 ID 和配置档名称（`default` 使用工具默认账号），只读取家庭确认连接，不发送开关灯命令。

## 命令

```bash
python3 main.py context
python3 main.py check-power --power false --scope room --room 厨房
python3 main.py preview-power --power false --scope room --room 厨房 --text 关闭厨房灯
python3 main.py set-power --power false --scope room --room 厨房
python3 main.py set-power --power on --scope device --room 客厅 --device 吸顶灯 --simulate
```

- `context`：只读查询真实房间和设备名称，不输出账号秘密。
- `check-power`：检查参数与必要配置，不访问设备或发送控制。
- `preview-power`：使用真实清单核对目标与用户原话，返回 `question`（无需确认时为 `null`），不发送开关。调用者必须处理该确认，命令本身不代替用户回答。
- `set-power`：执行一次开关，并核实设备状态。`--power` 接受 `true/false` 或 `on/off`；`--scope` 为 `device`、`room` 或 `home`；设备范围需 `--device`，可用 `--room` 消除重名；房间范围需 `--room`；全屋不附加局部目标。
- `--simulate` 可用于预检、预览或开关模拟，不连接易来，也不控制灯具。

业务命令通过 stdout 返回 JSON，stderr 输出诊断。`status=success` 且退出码 `0` 表示命令完成；认证、参数、连接或状态核实失败返回非零。控制请求已发送或进程正常退出，都不等于目标状态已确认。

## 配置路径

公共路径参数放在子命令前：

```bash
python3 main.py --config /path/config.yaml \
  --data-dir /path/data --runtime-dir /path/runtime context
```

| 参数 | 对应环境变量 | 内容 |
| --- | --- | --- |
| `--config` | `YEELIGHT_CONFIG` | 私有 YAML 配置，权限须为 `600` |
| `--data-dir` | `YEELIGHT_DATA` | 本技能私有数据目录 |
| `--runtime-dir` | `YEELIGHT_RUNTIME` | 本技能运行目录 |

默认保留 `~/.primebuddy/data/yeelight` 以兼容已有部署，不要求安装框架。配置优先使用显式路径或技能环境变量，否则读取数据目录已有 `config.yaml` 或旧 `~/.primebuddy/cfg/skills/yeelight/config.yaml`。旧 `PRIMEBUDDY_*` 环境变量只用于已有路径兼容。

```yaml
executable: yeelight-home
house_id: "你的家庭 ID"
profile: null
```

外部工具账号和登录数据仍由 `yeelight-home` 管理。

## 目标确认与失败处理

规划时先读取真实清单，执行时再次校验。同名设备未指定房间、未知名称或多个近似目标时先询问；唯一名称修正或补充房间也要先确认。只有用户明确要求全屋，才执行全屋范围。

只有真实状态已核实才能报告“已打开”或“已关闭”。部分成功、离线或读回不匹配时返回未确认；不重复发送开关命令。单灯可作有限的只读复核，房间与全屋未确认时不自动重试。认证失效提示重新登录。

SIGINT、SIGTERM、SIGHUP 使业务停止继续等待，关闭自身 MCP 进程；已发给云端的开关可能继续生效。取消和强杀都不表示撤销控制，不自动恢复灯具原状态。本技能没有持续运行的灯效，也没有通用 `stop/recover` 命令；需要核对实际状态后，再决定下一步。

## 接入 PrimeBuddy

本版本配套 PrimeBuddy 1.2.0。框架声明的易来兼容范围为 `>=0.3.0, <0.4.0`，推荐 `0.3.1`；后续以目标框架的 `release.json` 为准。首次安装技能：

```bash
primebuddy skill install ./yeelight-0.3.1.rpk
primebuddy skill configure yeelight
```

Buddy 在自己的适配配置中选择已有的 `context`、预检、预览和控制命令，管理参数、任务资源和期限；技能不实现宿主 Context 或生命周期函数，`cfg/pkg.yaml` 仅保留包信息。详见 [宿主接入说明](references/buddy.md)。

已有托管安装时，先退出语音会话，再用 `primebuddy update --check` 检查，使用 `primebuddy update` 联合更新框架与已安装技能；保留原配置和启用状态，不重新配置外部工具账号。旧框架 1.0.0 尚无更新命令，首次须在原安装板解压新框架包并运行 `source ./scripts/install.sh`。外部源码目录由用户自行更新，更新器只检查和提示。旧易来 0.2.0 不能继续使用新版 Buddy 的 CLI 适配；独立使用本技能不要求安装 Buddy。

## 开发与验证

`src/skill.py` 负责普通业务流程，`src/setup.py` 提供独立命令使用的配置向导，`src/integrations/mcp_client.py` 管理 MCP 连接和单次控制。Python 依赖见 `requirements.txt`。

```bash
python3 main.py --check
python3 -m unittest discover -s tests -v
```

测试只使用模拟 MCP，不连接家庭或灯具。由框架源码目录运行 `python3 src/primebuddy/tools/build_release.py --skill ../primebuddy-skills/yeelight` 可打 RPK；私有配置和外部账号不要放进发布包。

## 开源协议

采用 [MIT 协议](LICENSE)。`yeelight-home` 和第三方组件遵循各自协议。

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
