# PrimeSkills

[PrimeBuddy](https://github.com/huang-kai/primebuddy) 的独立技能集合。每个技能以根目录 SKILL.md 提供使用说明，配置与业务代码按需添加；本仓库两个设备技能采用可选原生工具适配，复用平台音频和调度。

| 技能 | 版本 | 能力与依赖 |
| --- | --- | --- |
| [diangetai](diangetai/README.md) | 0.3.0 | 官方默认点歌台：QQ 音乐登录、歌曲播放、时长配置及可选舞蹈/表情/灯光联动；使用 T1 原生 ROS 2 |
| [yeelight](yeelight/README.md) | 0.2.0 | 可选灯控适配：需另行安装并登录 `yeelight-home`，通过其本地 stdio MCP 控制灯具 |

安装技能时进入技能自己的向导，配置完成后才会启用。放入平台 skills 目录或显式配置的集合目录即可发现；技能自己的配置和依赖须先准备好。普通 skill 无需五个 Python 契约函数。

## 安装一个技能

先安装并配置 PrimeBuddy。然后从本仓库生成单个技能的 ZIP：

```bash
git clone https://github.com/huang-kai/primeskills.git primebuddy-skills
git -C primebuddy-skills archive --format=zip --output=../diangetai-skill.zip HEAD:diangetai
primebuddy skill install ./diangetai-skill.zip
```

安装易来时，将上面的目录 `HEAD:diangetai` 换成 `HEAD:yeelight`，并修改 ZIP 文件名。安装新版本前先退出平台，再在安装命令末尾加 `--replace`；重新配置使用：

```bash
primebuddy skill configure diangetai
primebuddy skill list
primebuddy start
```

本仓库根目录是技能集合，不能直接作为单个 skill 传给 `primebuddy skill install`。该命令的 Git 来源要求仓库根目录包含 `SKILL.md`；本集合使用上面的单技能 ZIP 安装方式。也可以在 GitHub 下载仓库源码 ZIP，解压后用框架的打包工具生成指定技能包。

点歌台启用音乐联动后会产生机器人动作，具体姿态检查与行为见其 README。配置向导本身不会播放歌曲或控制机器人动作。

## 联合开发与打包

为沿用现有测试和默认打包路径，工作区使用下面两个相邻目录：

```bash
git clone https://github.com/huang-kai/primebuddy.git primebuddy
git clone https://github.com/huang-kai/primeskills.git primebuddy-skills

cd primebuddy
bash scripts/test.sh
python3 src/primebuddy/tools/build_release.py --skill-only ../primebuddy-skills/diangetai --output /tmp/diangetai-skill-0.3.0.zip
```

`primeskills` 是 GitHub 仓库名，`primebuddy-skills` 是上述工作区目录名。每个技能可以独立修改和打包；技能接口见 [skill 开发契约](../PrimeBuddy/docs/skill-development.md)。测试使用模拟业务接口，不要求机器人或真实账号；框架的 SDK 夹具测试在缺少厂商 SDK 时会明确跳过。

源码不包含账号配置、运行记录、第三方安装目录或厂商 SDK。许可证尚未选定，当前未附 LICENSE 文件。

本地开发也可直接安装单技能目录：primebuddy skill install ./diangetai。根目录 main.py 支持 --check 与 configure。完整说明见框架 docs/skill-development.md。0.3.0点歌台、0.2.0易来与0.4.0框架须一起更新；新版业务函数入口尚未部署到机器人。
