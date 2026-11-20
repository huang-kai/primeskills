---
name: fall-alert
description: PrimeBot T1 的命令行倒地看护技能。双足或四足站稳时识别倒地，显示实时人体框，语音询问后按答复或两轮无回应通知一位家属。
---

# fall-alert · 倒地看护

在 T1 大脑板 Jetson 的技能目录填写 `cfg/config.yaml`，执行 `python3 main.py install` 安装依赖并部署显示程序。运行 `python3 main.py start` 开始一轮；另一终端可以执行 `python3 main.py status` 或 `python3 main.py stop`。问答结束后退出并恢复表情。

每个使用者在同一份 YAML 里设置联系人、短信发送方式和看护时间。配置、检测原理、命令和结果判断见 [README](README.md)。
