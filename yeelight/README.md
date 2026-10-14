# 易来灯控 yeelight

易来灯控让 PrimeBuddy 通过语音打开或关闭灯具。可以操作一盏灯、一个房间的灯，或明确指定全屋灯光。

当前版本为 **0.2.0**，配合 **PrimeBuddy 1.0.0** 使用。本技能通过本地 `yeelight-home` 工具访问家庭和设备，不直接连接灯具。目前接入的是开关功能。

## 使用前准备

先安装并配置 PrimeBuddy，在运行框架的机器上准备好：

- 可执行的 `yeelight-home` 工具。
- 该工具已登录的易来账号和对应家庭访问权限。
- 要控制的家庭 ID；使用多个账号配置档时，还需要配置档名称。

账号登录由 `yeelight-home` 管理，安装本技能不会自动安装或登录该工具。

## 安装与配置

拿到本技能的 RPK 后，执行：

```bash
primebuddy skill install ./yeelight-0.2.0.rpk
```

也可以直接安装源码目录：

```bash
git clone https://github.com/huang-kai/primeskills.git primebuddy-skills
primebuddy skill install ./primebuddy-skills/yeelight
```

安装向导会询问：

| 配置项 | 填写内容 |
| --- | --- |
| 可执行文件 | `yeelight-home` 命令名，或它的绝对路径 |
| 家庭 ID | 要控制的家庭 |
| 配置档名称 | `default` 使用工具默认账号，也可填写已有配置档 |

向导会读取家庭和设备列表来确认连接，不发送开关灯命令。配置失败时，可以检查账号授权、家庭 ID 和可执行文件路径后重试。

以后重新配置，使用：

```bash
primebuddy skill configure yeelight
```

完成后，重新启动语音会话生效。

## 怎么使用

```bash
primebuddy start
```

唤醒机器人后，可以说：

- “打开客厅的吸顶灯。”
- “关闭厨房的灯。”
- “关闭全屋灯光。”

房间和灯具名称以家庭中的实际名称为准。多个设备同名或名称不明确时，机器人会询问目标；发现唯一的近似名称时，也会先确认。只有明确要求全屋操作时，才会控制整个家庭。

技能会核实控制结果，实际状态未确认时不会报告“已打开”或“已关闭”，也不会自动重复控制。目前尚未接入亮度、色温、渐变、闪烁或场景等功能。

## 配置与管理

默认业务配置在 `~/.primebuddy/cfg/skills/yeelight/config.yaml`：

```yaml
executable: yeelight-home
house_id: "你的家庭 ID"
profile: null
```

`profile: null` 表示工具默认账号。配置向导填入 `default` 时会保存为这个值。账号登录数据仍由 `yeelight-home` 管理。

更新已有技能：

```bash
primebuddy skill install ./yeelight-0.2.0.rpk --replace
```

临时不用时执行 `primebuddy skill disable yeelight`；删除技能的步骤见 [管理技能](https://github.com/huang-kai/primeskills#管理技能)。删除本技能不会注销外部 `yeelight-home` 的账号。

## 工作原理与开发

框架读取 `SKILL.md` 后，将开关请求交给 `set_power`。技能通过本地 MCP 通信查询真实目标，再调用 `yeelight-home` 控制接口，并根据反馈核实结果。

| 位置 | 职责 |
| --- | --- |
| `SKILL.md`、`cfg/pkg.yaml` | 技能说明、包信息和业务入口声明 |
| `src/skill.py` | `set_power` 入口及目标上下文 |
| `src/setup.py`、`src/configuration.py` | 配置和连接验证 |
| `src/business/` | 参数校验和控制结果处理 |
| `src/integrations/mcp_client.py` | 本地 MCP 连接与灯控调用 |
| `tests/` | 目标解析、控制反馈等主要测试 |

本技能没有额外的 Python 第三方依赖；外部工具的安装和依赖由该工具自身负责。普通技能开发方法见 [框架教程](https://github.com/huang-kai/primebuddy#开发一个-skill)。

框架与技能仓库相邻时，从技能仓库根目录执行：

```bash
python3 yeelight/main.py --check
PYTHONPATH=../primebuddy/src python3 -m unittest discover -s yeelight/tests -v
```

测试使用模拟接口，不连接真实家庭或控制灯具。

从框架源码目录打包：

```bash
python3 src/primebuddy/tools/build_release.py --skill ../primebuddy-skills/yeelight
```

输出为 `dist/yeelight-0.2.0.rpk`，其中包含 README 和 LICENSE。发布新版本前修改 `cfg/pkg.yaml` 的 `version`；真实账号和私有配置不要放进发布包。贡献步骤见 [仓库说明](https://github.com/huang-kai/primeskills#参与贡献)。

## 开源协议

本技能采用 [MIT 协议](LICENSE)。独立分发时请保留 LICENSE；`yeelight-home` 和其他第三方组件遵循各自的协议。
