# 编写 Agent Skill

从 [通用模板](my-skill/SKILL.md) 开始。采用 Codex、Claude Code 和 OpenClaw 都熟悉的 Agent Skills 结构，模板提供写作提示，不增加 Buddy 专属函数要求。

## 目录与读取顺序

```text
my-skill/
├── SKILL.md          必需：名称、发现摘要、执行说明
├── scripts/          可选：可直接运行的程序
├── references/       可选：按需读取的详细资料
└── assets/           可选：任务使用的模板和静态资源
```

不要先创建所有目录。普通说明型技能只需要 `SKILL.md`；`README.md`、测试和依赖按实际需要添加。已有程序可以继续使用 `main.py`、`src/` 等组织方式，无需为了目录名称搬动代码。

模型分三层读取：

1. 发现技能时，只看 `name`、`description` 和位置。
2. 选中技能后，读取完整 `SKILL.md` 正文。
3. 遇到具体分支时，再读 `references/`；脚本和资产按任务需要使用。

因此，摘要短不代表正文也要短。把使用步骤、例子和失败处理写进正文，不把整个操作手册塞进 `description`。

## 使用模板

在本仓库根目录执行：

```bash
mkdir -p ../my-skills
cp -R templates/my-skill ../my-skills/hello
```

修改 `../my-skills/hello/SKILL.md`，使 `name: hello` 与目录名一致；替换模板提示并删除不适用章节。只把完成当前业务必需的信息留下。

Frontmatter 的通用必需字段只有：

```yaml
---
name: hello
description: 按用户提供的姓名生成中文问候。用于用户要求向某个人问好时。
---
```

`name` 使用小写字母、数字和单连字符，最多 64 字符；`description` 最多 1024 字符，写清“能做什么”和“何时使用”。有具体环境要求时可添加标准 `compatibility` 字段。正文标题是写作建议，不是安装器要求实现的接口。

## 正文应该写什么

模板中的章节对应开发者需要说明的实际问题：

| 内容 | 写清楚什么 |
| --- | --- |
| 何时使用 | 哪些请求应触发、需要哪些输入、何时澄清 |
| 准备 | 依赖与只读检查，缺少条件时如何处理 |
| 执行流程 | 调用哪个已有工具或程序、怎样选择参数 |
| 校验与反馈 | 如何确认真正完成，怎样报告未确认结果 |
| 失败与取消 | 已知错误、重试边界、已有的停止和清理方法 |
| 示例 | 用户请求、实际调用方式、结果判断 |
| 参考 | 在何种情况下再读取哪个补充文件 |

纯说明型技能可以只有适用条件、处理步骤和示例。详细接口、少见流程和设备差异可以放在 `references/`，从正文直接链接。链接的文件应随技能一起分发，不链接开发者机器上的绝对路径。

## 程序入口保持独立

需要代码时，提供普通 CLI、已有服务或工具接口即可，不要求 `import primebuddy`，也不要求 `run(context)`、`validate`、`preflight` 或 `shutdown`。

- 命令通过参数接收输入，stdout 返回业务结果，stderr 输出诊断；正常退出码和文本输出即可，不强制 Buddy JSON 格式。
- 写清入口、工作目录、配置位置和实际支持的参数。业务命令不要停在交互式 `input()`；登录或配置可以使用单独的交互命令。
- 长任务如果支持标准信号或自己的停止命令，说明怎样使用和检查结果。强杀进程不会执行 `finally`，也不能证明机器人上已经提交的播放或动作停止。
- 机器人型号识别、SDK 选择和设备状态检查由业务程序负责。框架不提供 `context.audio` 或代替技能封装 SDK。

点歌台已经有独立入口，例如在它的目录中运行：

```bash
python3 main.py play --artist 周杰伦 --seconds 30
```

该命令会实际播放，并沿用保存的联动开关；已开启联动时可能产生身体运动。只做文档校验时不要运行。模板本身不附带脚本，也不会创建新的设备操作。

## Buddy 当前的可选接入

Buddy 已支持普通 `SKILL.md` 和附带脚本，不要求 `cfg/pkg.yaml` 或 Python hooks。当前执行器通过通用 `script` 动作运行技能目录内的 `.py`、`.sh`、`.js` 文件，传入脚本路径和参数列表；不直接执行任意外部命令或 shell 字符串。已有 CLI、服务或工具接口可由技能内脚本调用，正文应说明该脚本的用法。

点歌台和易来使用独立 `main.py` CLI，`cfg/pkg.yaml` 只保存包信息。当前 Buddy 在自己的 `cfg/skill-adapters.example.yaml` 提供可选命令映射，用户通过 `~/.primebuddy/cfg/skill-adapters.yaml` 覆盖；技能无需实现 Python handler 或宿主上下文约定。细节见各技能的 `references/buddy.md`。

资源锁、硬期限、进程归属和终止升级是宿主执行策略。目前所有通用脚本共享 `skill.scripts` 资源，因此不同技能的通用脚本也会串行；宿主侧 CLI 适配可以声明扬声器、动作等具体资源。不能只改成脚本调用，就宣称已自动具有设备资源互斥与精确并行能力。

“把适配放在 Buddy 侧”表示由宿主选择独立命令、参数、资源、期限、预检和恢复检查。例如 Buddy 可以调用点歌台的 `status/recover`，但不会要求所有技能新增这些命令。没有适配的普通说明或脚本技能仍可使用，模板不要求开发者填写 Buddy 专属清单。旧原生 Python 适配仅作为兼容路径，不再作为新技能示例。

## 提交前检查

确认 frontmatter、实际入口和相对链接有效；示例必须对应程序已有参数。对有代码的技能，直接验证正常输入、错误输入和主要失败路径；长任务再验证停止与清理。

纯文档技能不需要为了安装而补 `main.py` 或空函数。可从框架教程中的 hello 示例验证发现和安装，再用真实用户请求检查模型是否正确选择技能。文档型测试不能替代设备能力验收。

编写依据：[Agent Skills 规范](https://agentskills.io/specification)、[Codex](https://learn.chatgpt.com/docs/build-skills)、[Claude Code](https://code.claude.com/docs/en/skills)、[OpenClaw](https://docs.openclaw.ai/tools/creating-skills)。这些产品的宿主专属字段和权限设置各有含义，通用模板只使用共同的基础格式。
