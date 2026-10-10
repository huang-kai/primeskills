# yeelight · PrimeBuddy 灯控 skill

独立技能项目，使用 PrimeBuddy skill API v1。主框架不需要了解易来家庭、账号或 MCP 参数。

安装 ZIP 或 Git 仓库时自动调用 `src/setup.py`：配置本地 `yeelight-home` 可执行文件、家庭 ID 和配置档，通过只读 MCP 实体查询确认连接。账号登录沿用现有易来工具。本技能没有新增账号扫码协议，也不安装 Node.js。

```bash
git clone https://github.com/huang-kai/primeskills.git primebuddy-skills
git -C primebuddy-skills archive --format=zip --output=../yeelight-skill.zip HEAD:yeelight
primebuddy skill install ./yeelight-skill.zip
primebuddy skill configure yeelight
```

运行入口 `src/skill.py` 负责目标校验、灯名澄清、开关控制及状态读回。业务配置保存在 `PRIMEBUDDY_HOME/data/yeelight/config.json`。实际灯状态未确认时不会返回成功；安装引导不会发送灯控命令。

源码位于 [huang-kai/primeskills 的 yeelight 目录](https://github.com/huang-kai/primeskills/tree/main/yeelight)。本仓库包含多个技能，安装时使用上面的单技能 ZIP；ZIP 根目录包含 `manifest.json`、`SKILL.md`、`README.md` 与 `src/`。技能使用现有原生 `yeelight-home` 工具，通过 stdio MCP 调用，框架本身无需易来依赖。
