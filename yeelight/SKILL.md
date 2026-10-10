---
name: yeelight
description: 通过本地易来 MCP 开关指定灯具或房间的灯。用户要求打开或关闭灯时使用。
---

# 易来灯控

使用 `set_power`，参数见 [manifest.json](manifest.json)，实现见 [src/skill.py](src/skill.py)。
`power` 为布尔值；`scope` 为 device、room 或 home。
指定灯具填写 device，可附加 room；指定整个房间填写 room，device 为空；
只有明确要求全屋时使用 home，room/device 均为空。
例如关闭厨房灯：`{"power":false,"scope":"room","room":"厨房","device":""}`。

规划前通过 MCP 获取真实房间和设备名称，执行时再次查询校验。
名称近音且清单中候选唯一时，生成使用真实名称的候选任务，由主程序强制询问确认；
用户确认前不能执行。多个相近候选或同名设备未指定房间时，整轮 tasks 留空并询问目标。
执行成功与状态已确认不同；仅 verified 的反馈可以播报“已打开/已关闭”。
未确认结果不自动重试。认证失效返回 auth_required，由主程序统一播报。
本版只注册开关动作；闪烁、亮度渐变、循环变色、场景尚未接入，不能虚构调用。
不访问 ASR、LLM、TTS 或机器人运动接口。
