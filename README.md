# PrimeSkills

PrimeSkills 是 [PrimeBuddy](https://github.com/huang-kai/primebuddy) 的技能集合。PrimeBuddy 负责听懂请求、安排任务和自己的语音回复；技能以 `SKILL.md` 说明业务流程，设备程序自行调用 SDK。点歌台和易来均提供独立 CLI，可供终端、Buddy 或其他 Agent 调用。

每个技能都是一个独立目录，可以单独安装、配置和打包。通过语音使用时，需要包含 CLI 宿主适配的 PrimeBuddy；也可直接使用独立命令：

| 技能 | 版本 | 用途 | 使用前需要准备 |
| --- | --- | --- | --- |
| [点歌台 diangetai](diangetai/README.md) | 0.3.0 | 播放 QQ 音乐，设置播放时长，可选机器人动作、表情和灯光联动 | T1/Q1 原厂 ROS 环境、QQ 音乐账号 |
| [易来 yeelight](yeelight/README.md) | 0.2.0 | 打开或关闭指定灯具、房间或全屋灯光 | 已安装并登录的 `yeelight-home` 工具 |

## 安装技能

先按 [框架说明](https://github.com/huang-kai/primebuddy#安装与使用) 安装 PrimeBuddy，并配置模型服务。安装技能前，先退出正在运行的语音会话。

如果已经拿到单个技能的 RPK，在包所在目录执行：

```bash
primebuddy skill install ./diangetai-0.3.0.rpk
```

也可以直接从源码目录安装，无需先打包：

```bash
git clone https://github.com/huang-kai/primeskills.git primebuddy-skills
primebuddy skill install ./primebuddy-skills/diangetai
```

安装易来时，将目录换成 `./primebuddy-skills/yeelight`。本仓库根目录包含多个技能，安装时应传入其中的一个技能目录。

安装器会复制文件并安装声明的 Python 依赖。当前两个技能的配置向导由 Buddy 侧适配调用各自独立的 `configure` 命令；没有该适配时，进入技能目录手动配置。配置后运行 `primebuddy start`，唤醒机器人即可使用。

详细的配置和使用方法见各技能 README。点歌台开启动作联动后可能产生身体运动，请先阅读其中的联动说明。

## 管理技能

| 操作 | 命令示例 |
| --- | --- |
| 查看技能和安装位置 | `primebuddy skill list` |
| 重新配置 | `primebuddy skill configure diangetai` |
| 更新已有技能 | `primebuddy skill install ./diangetai-0.3.0.rpk --replace` |
| 暂时停用，保留代码和数据 | `primebuddy skill disable diangetai` |
| 重新启用 | `primebuddy skill enable diangetai` |

安装、更新、配置或启停后，重新启动语音会话生效。更新技能会保留已有业务配置和登录数据，配置向导仍会让你确认本次设置。

当前框架没有 `skill uninstall` 命令。删除技能时，先退出会话，再清理 `cfg/skills.yaml` 中对应的安装记录和技能代码目录；是否清除配置与登录数据由你决定。完整步骤见 [框架的删除技能说明](https://github.com/huang-kai/primebuddy#删除技能)。

默认安装后，代码、配置和数据分别在：

```text
~/.primebuddy/
├── skills/<name>/              技能代码、资源和依赖
├── cfg/skills/<name>/          技能业务配置
└── data/<name>/                技能私有数据，例如 QQ 登录态
```

自定义框架安装位置时，这些路径随安装目录变化。

## 代码如何组织

新技能可以从 [通用模板](templates/my-skill/SKILL.md) 开始，编写方法见 [Agent Skill 编写指南](templates/README.md)。采用 Codex、Claude Code、OpenClaw 熟悉的目录习惯：

```text
<skill>/
├── SKILL.md              必需：发现摘要和执行说明
├── scripts/              可选：普通命令行程序
├── references/           可选：按需读取的接口、设备或特殊流程资料
└── assets/               可选：任务使用的模板与资源
```

框架先读取 `name` 和 `description`，选中后读取完整正文，再按需读取参考文件。摘要保持简短，正文写清适用场景、前置条件、操作步骤、示例、结果校验和失败处理。模板中的标题是写作建议，不是固定协议。

现有点歌台和易来还使用 `main.py`、`src/`、`cfg/`、`tests/` 等目录，`cfg/pkg.yaml` 只保存包信息。普通技能无需实现 Buddy 专属函数；需要代码时可提供独立 CLI。宿主需要具名工具、资源锁或恢复检查时，在自己的可选适配配置中映射现有命令。完整教程见 [开发一个 skill](https://github.com/huang-kai/primebuddy#开发一个-skill)。

## 开发与测试

联合开发时，把框架和技能仓库放在相邻目录：

```bash
git clone https://github.com/huang-kai/primebuddy.git primebuddy
git clone https://github.com/huang-kai/primeskills.git primebuddy-skills
```

按 [框架开发环境说明](https://github.com/huang-kai/primebuddy#参与贡献) 准备 Python 环境，再运行：

```bash
cd primebuddy
bash scripts/test.sh
```

该命令会运行框架和相邻技能仓库的测试。两个技能的测试使用模拟接口，无需真实账号、机器人或灯具；实际设备能力仍需要在对应环境中验证。

从技能仓库目录可以单独检查说明和入口文件：

```bash
python3 diangetai/main.py --check
python3 yeelight/main.py --check
```

新增技能时，先写清触发条件和使用步骤，再实现业务代码。普通命令可直接在终端运行；当前 Buddy 通用执行器通过技能内现有 Python、Bash 或 JavaScript 脚本调用，不执行模型生成的 shell 字符串。依赖、配置、资源和测试按实际需要添加。技能名称保持唯一，`cfg/pkg.yaml` 的 `package_name` 应与 `SKILL.md` 的 `name` 一致。

## 打包发布

已有 RPK 直接安装或发布即可。需要把源码打成包时，从框架源码目录执行：

```bash
# 单独打点歌台
python3 src/primebuddy/tools/build_release.py --skill ../primebuddy-skills/diangetai

# 单独打易来
python3 src/primebuddy/tools/build_release.py --skill ../primebuddy-skills/yeelight
```

默认输出到框架 `dist/`，名称由技能名称和版本生成，例如 `diangetai-0.3.0.rpk`。RPK 内容是 ZIP，包中包含该技能的 README 和 LICENSE。

需要把技能一起放进框架包时，增加 `--framework`，每个技能单独写一个 `--skill`：

```bash
python3 src/primebuddy/tools/build_release.py --framework \
  --skill ../primebuddy-skills/diangetai \
  --skill ../primebuddy-skills/yeelight
```

`--skill` 只接受源码目录。直接运行脚本会显示帮助；`--output-dir` 指定输出位置，`--overwrite` 用于重打已有同版本包。发布新版本前，修改相应技能的 `cfg/pkg.yaml`。

## 参与贡献

欢迎改进现有技能、修复问题、完善文档或增加新技能。

1. 在 [Issues](https://github.com/huang-kai/primeskills/issues) 描述问题或功能需求。小的文档修正可以直接提交 PR。
2. Fork 仓库，在自己的分支上修改，让一个 PR 解决一个明确的问题。
3. 运行相关测试；行为变化应补充主要用例，文档修改应核对命令、路径和版本。
4. 向 `main` 分支提交 PR，说明修改原因、结果和实际验证方式。

源码和公开配置放在技能目录内。真实账号、密钥、登录态和运行数据保存在框架的配置、数据目录中，避免混入源码或发布包。`docs/` 暂时作为本地开发资料，测试录音和生成的包也不提交 Git。

涉及机器人动作或真实灯控的验证，请说明测试环境和实际观察结果，避免把模拟测试当成实机验收。

## 开源协议

本仓库中的两个技能采用 [MIT 协议](LICENSE)。各技能目录也包含一份 LICENSE，独立打包、分发时请一并保留。第三方工具、依赖和机器人 SDK 遵循各自的协议。
