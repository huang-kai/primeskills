# 易来灯控 yeelight

易来灯控通过本地 `yeelight-home` MCP 接口打开或关闭灯具、房间或全屋灯光。当前包版本 **0.2.0**，业务和命令行入口均可独立运行，不导入 PrimeBuddy。

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

配合包含 CLI 宿主适配的 PrimeBuddy 使用：

```bash
primebuddy skill install ./yeelight-0.2.0.rpk
primebuddy skill configure yeelight
```

Buddy 在自己的适配配置中选择已有的 `context`、预检、预览和控制命令，管理参数、任务资源和期限；技能不实现宿主 Context 或生命周期函数，`cfg/pkg.yaml` 仅保留包信息。详见 [宿主接入说明](references/buddy.md)。

## 开发与验证

`src/skill.py` 负责普通业务流程，`src/setup.py` 提供独立命令使用的配置向导，`src/integrations/mcp_client.py` 管理 MCP 连接和单次控制。Python 依赖见 `requirements.txt`。

```bash
python3 main.py --check
python3 -m unittest discover -s tests -v
```

测试只使用模拟 MCP，不连接家庭或灯具。由框架源码目录运行 `python3 src/primebuddy/tools/build_release.py --skill ../primebuddy-skills/yeelight` 可打 RPK；私有配置和外部账号不要放进发布包。

## 开源协议

采用 [MIT 协议](LICENSE)。`yeelight-home` 和第三方组件遵循各自协议。
