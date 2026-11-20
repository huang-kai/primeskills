# 安装与故障处理

## 连接和上传

1. 让电脑和 T1 连接同一网络，找到 T1 的局域网 IP，准备好有权登录两块板的机器人账号。双板 T1 通常经运控板接入，Jetson 的板内地址为 `10.1.1.101`；当前技能使用的运控板板内地址为 `10.1.1.100`。
2. 在电脑上打开存放 RPK 的目录，执行下列命令。把第一行引号内的文字换成这台机器人的实际 IP。首次 SSH 登录时，按设备提供的指纹核对主机身份。

   ```bash
   ROBOT_IP='替换为机器人的局域网IP'
   scp -o ProxyJump=run@"$ROBOT_IP" fall-alert-0.1.5.rpk run@10.1.1.101:~/
   ssh -J run@"$ROBOT_IP" run@10.1.1.101
   ```

3. 在新终端输入 `cat /robot/data/info/soc_index`。输出 `1` 表示已在 Jetson，输出 `0` 表示仍在运控板。确认后按[首页](../README.md#安装)解包。

## 安装器准备哪些依赖

| 位置 | 安装内容和条件 |
| --- | --- |
| Jetson 项目环境 | 首次创建 `/robot/persist/t1-fall-alert/venv`，通过系统 Python 复用固件自带库，用 pip 安装根目录 `requirements.txt` 中的 PyYAML。选择阿里云时额外安装 `scripts/requirements-aliyun.txt` 中的短信 SDK。已有满足版本的包可复用。 |
| Jetson 原厂环境 | 需要 ROS 2 Humble、原厂相机和语音接口、CUDA 12 运行库、TensorRT、OpenCV、NumPy、带 libx264 的 FFmpeg，以及 `/robot/software/algorithm/models/yolo26n-seg_640x480_fp16_nx.engine`。这些组件和固件配套，由安装器检查。 |
| 运控板 | 安装器通过 SSH 检查 FFmpeg、ROS 2 和 GStreamer 显示插件，再下发两个显示脚本至 `/robot/persist/t1-fall-alert/display/`。运行时使用原厂显示服务和 Rockchip 屏幕接口。 |
| 账号与网络 | Jetson 的 `run` 账号需要能免密登录运控板，主机密钥已经核对。两板需要已有的临时 systemd 服务启停权限。运行时 Jetson 需能访问选定的短信服务。 |

安装器可以重复运行。执行 `install` 只准备环境、检查配置和部署显示文件。只有 `start` 才会占用摄像头、接管屏幕并开始看护。

## 升级已有版本

先结束当前看护，将新 RPK 解压到**新的版本目录**。进入新目录，从上一版本复制 YAML，再安装。以下命令适用于原名 `t1-fall-alert` 的 0.1.5 安装。其他旧版本请替换第二行的来源目录。

```bash
cd ~/fall-alert-0.1.5
install -m 600 ~/t1-fall-alert-0.1.5/cfg/config.yaml cfg/config.yaml
python3 main.py install
```

更名后继续使用原来的机上数据目录，已有事件台账、运行环境和服务名称可继续使用。

0.1.1 的 YAML 位于运控板原技能目录，需要通过板内 SSH 将其迁入 Jetson。更早版本若只有 Jetson 本地的阿里云 JSON，可在新包的空白模板上运行 `python3 main.py install --import-legacy`。原有的 `configure` 命令仍可作为 `install` 的别名使用。

看护问答使用原厂 `BusinessAgentControl` 的 `graphical_programming` 会话。该模式由机器人上的应用共享，请错开其他正在使用图形化编程语音的应用。如果问答被手动停止，临时服务会归还原厂语音。若归还失败，排除板间连接问题后运行 `python3 main.py stop` 再试。

## 常见问题

| 现象 | 处理方法 |
| --- | --- |
| 提示应在 SoC1 上运行 | 从运控板执行 `ssh run@10.1.1.101`，确认 RPK 位于 Jetson。 |
| 提示缺少 venv 或 pip | 请设备管理员为系统 Python 安装 `python3-venv`，然后重新运行 `install`。能正常创建项目环境时可跳过此步骤。 |
| pip 无法下载依赖 | 检查 Jetson 的 DNS 和软件包源。离线时先准备匹配 T1 架构和 Python 版本的全部依赖 wheel，再用 `PIP_NO_INDEX=1 PIP_FIND_LINKS=/path/to/wheels python3 main.py install` 安装。 |
| 缺少模型、CUDA、ROS、原厂消息包或显示插件 | 请设备管理员按上表核对两块板的固件和配套组件，再运行 `install`。通用 PyPI 包无法提供 T1 的机载模型或原厂消息。 |
| 运控板安装失败或显示节点未启动 | 在 Jetson 执行 `ssh -o BatchMode=yes -o StrictHostKeyChecking=yes run@10.1.1.100 'cat /robot/data/info/soc_index'`，应输出 `0`。若登录失败，请管理员核对板间账号、已授权的 SSH 公钥和主机指纹。 |
| 终端停在 Running as unit | 正在等待本轮事件，另开 Jetson 终端查看 `journalctl -fu t1-fall-skill-watch.service -o cat`。屏幕端日志在运控板的 `journalctl -u t1-fall-skill-display.service`。 |
