# yeelight · PrimeBuddy 灯控 skill

版本0.2.0，使用标准 SKILL.md 和 PrimeBuddy 0.4.0 的可选原生工具适配。主框架不需要了解易来家庭、账号或 MCP 参数。

安装 ZIP 或 Git 仓库时自动调用 `src/setup.py`：配置本地 `yeelight-home` 可执行文件、家庭 ID 和配置档，通过只读 MCP 实体查询确认连接。账号登录沿用现有易来工具。本技能没有新增账号扫码协议，也不安装 Node.js。

```bash
git clone https://github.com/huang-kai/primeskills.git primebuddy-skills
python3 ~/.primebuddy/src/primebuddy/tools/build_release.py --skill ./primebuddy-skills/yeelight --output-dir ./dist
primebuddy skill install ./dist/yeelight-0.2.0.rpk
primebuddy skill configure yeelight
```

运行入口 `src/skill.py` 负责目标校验、灯名澄清、开关控制及状态读回。业务配置保存在 `PRIMEBUDDY_HOME/cfg/skills/yeelight/config.yaml`。实际灯状态未确认时不会返回成功；安装引导不会发送灯控命令。

源码位于 [huang-kai/primeskills 的 yeelight 目录](https://github.com/huang-kai/primeskills/tree/main/yeelight)。本仓库包含多个技能，安装时使用上面的单技能 ZIP；ZIP 根目录包含 `cfg/pkg.yaml`、`SKILL.md`、`README.md` 与 `src/`。技能使用现有原生 `yeelight-home` 工具，通过 stdio MCP 调用，框架本身无需易来依赖。

## 本地目录与验证

根目录保留 `main.py`、`README.md`、`SKILL.md`、`requirements.txt` 和 Git 忽略文件。配置在 `cfg`，实现代码在 `src`，静态资源在 `resource`，主要功能测试在 `tests`。`docs` 是可选的本地开发文档，暂不提交 Git。

```bash
python3 main.py --check
```

该 --check 入口仅校验本地说明和文件路径，不执行业务；python3 main.py configure 可独立运行 Console 向导。开发时使用相邻框架源码或通过 PYTHONPATH 提供框架；安装到 ~/.primebuddy/skills 后自动找到同一目录下的框架。框架测试脚本会执行本技能的离线回归；本次重构没有部署或进行实机验收。默认配置见 `cfg/config.yaml`；原生业务入口为 set_power(context, power, scope, room, device)。

包元信息使用 cfg/pkg.yaml，与工作区启元技能样例保持字段风格：user_id、version、sdk_version、package_name、author、description。作者为 PrimeBuddy，user_id 为 robot_local，sdk_version 为 v1.0.0；SDK 字段是开发参考信息，易来仍不直接调用机器人 SDK。PrimeBuddy 运行声明集中在 primebuddy 节点。依赖以 requirements.txt 为准，SKILL.md 在根目录。

也可直接 primebuddy skill install ./yeelight，或把目录复制到 PRIMEBUDDY_HOME/skills/yeelight 后配置并重启会话。Agent 先读取 SKILL.md 和真实目标上下文，再提交控制；名称修正须确认，写入后仍核实实际状态。本次未控制真实灯具。
