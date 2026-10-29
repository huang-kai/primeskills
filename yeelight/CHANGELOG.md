# Changelog

记录易来灯控技能自身的版本变化。框架、外部 `yeelight-home` 工具和账号服务分别维护。

## [0.3.1] - 2026-10-29

### Changes

- 声明 `primebuddy.version: 1`，将配置向导和业务 CLI 映射放入技能包自身，减少与 Buddy 内置适配表的耦合。
- 补充统一安装向导、私有路径、返回值及独立升级的开发约束；技能包包含 CHANGELOG。
- 配套 Buddy 1.2.0，保留既有业务 CLI、配置和私有数据。兼容的旧版技能不会因平台升级自动替换，需主动选择新包。

### Known issues

- 本次主要更新安装协议和文档，不表示此前的实机或外部服务限制已解决；详见 0.3.0 的 Known issues。

## [0.3.0] - 2026-10-23

### Features

- 提供不依赖 PrimeBuddy 的独立 CLI：`configure`、`context`、`check-power`、`preview-power`、`set-power` 和 `--check`。业务命令使用 stdout 返回 JSON，诊断输出到 stderr；开关参数支持 `true/false` 和 `on/off`。
- 独立管理配置路径和私有文件，支持 `--config`、`--data-dir`、`--runtime-dir` 及 `YEELIGHT_CONFIG`、`YEELIGHT_DATA`、`YEELIGHT_RUNTIME` 环境变量；配置以原子方式写入，读取时检查私有权限和重复键。
- 独立入口接入 SIGINT、SIGTERM、SIGHUP 取消处理，业务等待可响应取消并沿用原有 MCP 连接退出清理；控制模拟通过 `--simulate` 执行，不连接灯具。

### Fixes

- 将业务参数校验提前到 `check-power` 预检，提前拒绝过长、含控制字符或范围与目标矛盾的参数；该预检不创建 MCP 连接。

### Compatibility

- 需要 Python 3.10 或更新版本，新增独立 Python 依赖 `PyYAML==6.0.3`。仍需用户准备已登录的 `yeelight-home`、家庭访问权限和家庭 ID。
- 移除技能对 PrimeBuddy 模块、宿主 Context 和原生 hooks 的依赖；`cfg/pkg.yaml` 仅保留包信息。接入 PrimeBuddy 时由框架提供 CLI 适配，0.3.0 配套 PrimeBuddy 1.1.0，其声明的技能兼容范围为 `>=0.3.0, <0.4.0`。独立使用无需安装框架。

### Upgrade notes

- 从 0.2.0 升级到此版本时，自定义宿主应改用上述 CLI 命令，不再调用旧的 `entrypoint/actions` 或 `set_power(context, ...)` 接口。目标预览返回非空 `question` 时，宿主仍须先取得用户确认。
- PrimeBuddy 1.1.0 托管安装可在退出语音会话后执行 `primebuddy skill install ./yeelight-0.3.0.rpk --replace`，或使用 `primebuddy update` 联合升级。旧框架 1.0.0 须先按新框架包说明升级。独立部署更新技能文件后执行 `python3 -m pip install -r requirements.txt`，再用 `python3 main.py --check` 检查入口。
- 保留旧的 `~/.primebuddy/data/yeelight`、`~/.primebuddy/cfg/skills/yeelight/config.yaml` 和 `PRIMEBUDDY_*` 路径兼容。外部工具的账号与登录数据仍由 `yeelight-home` 管理，勿用发布包替换私有配置或账号目录。

### Known issues

- 仍仅支持开关，不支持亮度、色温、渐变、闪烁、循环变色或场景；没有通用 `stop`、`status`、`recover` 命令。
- 取消、终止进程或关闭 MCP 连接不保证撤销已经发出的云端控制，也不会自动恢复灯具原状态。状态未确认时不自动重发开关；单灯只作有限的只读复核。
- 本次 CLI 与目标确认回归使用模拟 MCP，未据此宣称真实家庭、账号或灯具已完成验收。

## [0.2.0] - 2026-10-14

历史记录依据 [GitHub Release `yeelight-v0.2.0`](https://github.com/huang-kai/primeskills/releases/tag/yeelight-v0.2.0)，对应提交 `fd88191`。

### Features

- 为 PrimeBuddy 1.0.0 提供基于本地 `yeelight-home` 的灯具、房间和明确全屋范围的语音开关能力，使用框架原生工具适配。
- 已具备真实房间与设备清单校验、模糊目标澄清、近似名称确认、实际控制结果核实，以及未确认写入不自动重试。
- 配置向导只读验证家庭和设备连接；账号登录由外部工具管理。该版本无额外 Python 第三方依赖。
- 发布 `yeelight-0.2.0.rpk`，采用 MIT 协议；原发布记录确认 6 项离线测试通过，发布准备未连接真实家庭或控制灯具。

[0.3.0]: https://github.com/huang-kai/primeskills/compare/yeelight-v0.2.0...yeelight-v0.3.0
[0.2.0]: https://github.com/huang-kai/primeskills/releases/tag/yeelight-v0.2.0
