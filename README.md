# PrimeSkills

PrimeSkills 是 [PrimeBuddy](https://github.com/huang-kai/primebuddy) 的技能集合。PrimeBuddy 负责听懂请求、安排任务和自己的语音回复；技能以 `SKILL.md` 说明业务流程，设备程序自行调用 SDK。点歌台和易来均提供独立 CLI，可供终端、Buddy 或其他 Agent 调用。

每个技能都是一个独立目录，可以单独安装、配置和打包。当前版本配套 PrimeBuddy 1.1.0；通过语音使用时，由框架的发布清单检查兼容性，也可直接使用独立命令：

| 技能 | 版本 | 用途 | 使用前需要准备 |
| --- | --- | --- | --- |
| [点歌台 diangetai](diangetai/README.md) | [0.4.0](https://github.com/huang-kai/primeskills/releases/tag/diangetai-v0.4.0) | 播放 QQ 音乐，设置播放时长，可选机器人动作、表情和灯光联动 | T1/Q1 原厂 ROS 环境、QQ 音乐账号 |
| [易来 yeelight](yeelight/README.md) | [0.3.0](https://github.com/huang-kai/primeskills/releases/tag/yeelight-v0.3.0) | 打开或关闭指定灯具、房间或全屋灯光 | 已安装并登录的 `yeelight-home` 工具 |
| [成语接龙 chengyu-chain](chengyu-chain/README.md) | 0.1.0 开发中 | 同音接龙、解释、三级难度和整局独占语音 | 自有云模型配置、T1/Q1 原厂语音环境；新版独占交互宿主 |

成语接龙尚未加入正式发行清单，真机验收待完成。使用 Buddy 调用它时须采用支持技能包 `primebuddy.mode` / `primebuddy.cli` 的源码或后续正式版本；已发布的 PrimeBuddy 1.1.0 不能仅凭安装新技能获得该保证。

各技能独立维护版本记录：[点歌台 CHANGELOG](diangetai/CHANGELOG.md)、[易来 CHANGELOG](yeelight/CHANGELOG.md)、[成语接龙 CHANGELOG](chengyu-chain/CHANGELOG.md)。

## 安装技能

先按 [框架说明](https://github.com/huang-kai/primebuddy#安装与使用) 安装 PrimeBuddy，并配置模型服务。安装技能前，先退出正在运行的语音会话。

如果已经拿到单个技能的 RPK，在包所在目录执行：

```bash
primebuddy skill install ./diangetai-0.4.0.rpk
```

也可以直接从源码目录安装，无需先打包：

```bash
git clone https://github.com/huang-kai/primeskills.git primebuddy-skills
primebuddy skill install ./primebuddy-skills/diangetai
```

安装易来时，将目录换成 `./primebuddy-skills/yeelight`。本仓库根目录包含多个技能，安装时应传入其中的一个技能目录。

安装器会复制文件并安装声明的 Python 依赖。技能的配置向导由 Buddy 侧适配调用各自独立的 `configure` 命令；没有该适配时，进入技能目录手动配置。配置后运行 `primebuddy start`，唤醒机器人即可使用。

详细的配置和使用方法见各技能 README。点歌台开启动作联动后可能产生身体运动，请先阅读其中的联动说明。

## 管理技能

| 操作 | 命令示例 |
| --- | --- |
| 查看技能和安装位置 | `primebuddy skill list` |
| 重新配置 | `primebuddy skill configure diangetai` |
| 检查框架与已安装官方技能的更新 | `primebuddy update --check` |
| 一起更新框架和已安装的官方技能 | `primebuddy update` |
| 单独更新已有技能 | `primebuddy skill install ./diangetai-0.4.0.rpk --replace` |
| 暂时停用，保留代码和数据 | `primebuddy skill disable diangetai` |
| 重新启用 | `primebuddy skill enable diangetai` |

安装、更新、配置或启停后，重新启动语音会话生效。单独安装技能时，配置向导仍会让你确认本次设置。`primebuddy update` 则按目标框架的发布清单联合更新已安装的官方技能，不重新配置，保留原配置、登录数据和启用状态；未安装的技能不会自动安装。

PrimeBuddy 1.1.0 接受点歌台 `>=0.4.0, <0.5.0`、易来 `>=0.3.0, <0.4.0`，推荐本仓库的 `0.4.0` / `0.3.0`。旧版点歌台 `0.3.0`、易来 `0.2.0` 的原生接口与新版 CLI 适配不兼容；不要只升级框架而保留已启用的旧技能。后续兼容范围以对应框架版本的 `release.json` 为准，不按版本更高就推断兼容。技能本身仍不依赖 Buddy，独立 CLI 用法保持不变。

旧 PrimeBuddy 1.0.0 没有 `update` 命令，首次需解压新框架 RPK，在原安装板运行 `source ./scripts/install.sh`，由新安装器联合升级已安装的官方技能。更新发生中断时，`primebuddy update --recover` 只离线恢复未完成事务前的版本。外部、自定义技能目录只检查和提示，不自动覆盖。完整步骤见 [框架更新说明](https://github.com/huang-kai/primebuddy#更新框架与技能)。

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

现有点歌台和易来还使用 `main.py`、`src/`、`cfg/`、`tests/` 等目录，`cfg/pkg.yaml` 保存包信息。普通技能无需实现 Buddy 专属函数；需要代码时可提供独立 CLI。需要具名工具、资源锁或恢复检查时，可在包内声明 `primebuddy.cli`，或使用宿主的可选适配配置。成语接龙在包内声明 `primebuddy.mode: exclusive`，普通技能默认 `task`。完整教程见 [开发一个 skill](https://github.com/huang-kai/primebuddy#开发一个-skill)。

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
python3 chengyu-chain/main.py --check
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

默认输出到框架 `dist/`，名称由技能名称和版本生成，例如 `diangetai-0.4.0.rpk`。RPK 内容是 ZIP，包中包含该技能的 README 和 LICENSE。

需要把技能一起放进框架包时，增加 `--framework`，每个技能单独写一个 `--skill`：

```bash
python3 src/primebuddy/tools/build_release.py --framework \
  --skill ../primebuddy-skills/diangetai \
  --skill ../primebuddy-skills/yeelight
```

`--skill` 只接受源码目录。直接运行脚本会显示帮助；`--output-dir` 指定输出位置，`--overwrite` 用于重打已有同版本包。发布新版本前，修改相应技能的 `cfg/pkg.yaml`。

每个技能在自己的 `CHANGELOG.md` 中按版本号和日期记录变化，最新版本在前，使用 `Features`、`Fixes`、`Compatibility`、`Upgrade notes` 和 `Known issues` 分类，省略没有内容的类别。独立 Release 使用 `技能名-v版本号` 标签，正文取当前版本记录，并上传对应 RPK 和单独的 `CHANGELOG.md` 附件。

已经随框架发布的技能版本，独立 Release 复用同一份 RPK，以保持它与框架 `release.json` 中的 SHA-256 一致；补充的版本记录在仓库和独立附件中提供。

需要供 `primebuddy update` 联合升级时，还需在框架仓库更新 `release.json` 中的兼容范围和推荐版本，使用 `--release --framework` 并提供两个推荐技能源码。生成的 `release.json` 固定本次框架与技能包的下载地址和 SHA-256；发布说明中的兼容表由同一清单生成，避免手工维护两套版本要求。详细命令见 [框架打包发布说明](https://github.com/huang-kai/primebuddy#打包发布)。

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
