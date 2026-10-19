# PrimeBuddy 中的易来灯控

技能提供独立 CLI，不实现 Buddy Context 或宿主函数。可选工具映射、资源与期限放在 Buddy 自己的 `cfg/skill-adapters.example.yaml`；用户覆盖文件为 `~/.primebuddy/cfg/skill-adapters.yaml`，`cfg/pkg.yaml` 仅保存技能包信息。

## 参数与流程

宿主先执行 `context` 只读取得真实房间、设备名，再用 `check-power` 预检及 `preview-power --text 用户原话` 核对目标。预览返回 `question` 非空时必须让用户确认；未知目标、重名或上下文不可用时不能先执行。

普通开关命令：

```bash
python3 main.py --config /path/config.yaml --data-dir /path/data --runtime-dir /path/runtime \
  set-power --power false --scope device --room 客厅 --device 吸顶灯
```

`--power` 为 `true/false` 或 `on/off`。`device` 范围需要设备名，可用房间消除重名；`room` 范围只提供房间；`home` 仅在用户明确要求全屋时使用，房间和设备为空。参数由宿主独立映射，业务程序不导入宿主模块。

## 结果、依赖与取消

多个操作共享家庭灯控资源时，由宿主声明先后。“音乐播完再关灯”由宿主安排灯控等待点歌成功结束；不要自动重发状态未确认的开关。

只有真实状态已核实才能报告“已打开/已关闭”。`auth_required` 表示需重新认证，`clarification_required` 表示需补充或确认目标。命令取消会结束等待并关闭自身 MCP；已经发出的控制不保证撤销，也不自动恢复原状态。

本技能没有持续灯效或独立停止命令，不能虚构 `stop/status/recover` 调用。配置和账号交给用户在终端处理，不能在语音控制任务中启动交互向导。

命令和测试见 [README](../README.md)，实际入口见 [main.py](../main.py)，业务见 [src/skill.py](../src/skill.py)。
