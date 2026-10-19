# PrimeBuddy 中的点歌台

本技能提供普通 CLI；Buddy 的可选适配位于宿主自己的 `cfg/skill-adapters.example.yaml`，用户可在 `~/.primebuddy/cfg/skill-adapters.yaml` 覆盖。`cfg/pkg.yaml` 仅为包信息，不注册 Python handler 或宿主函数。

## 调用与配置

宿主通过 `context` 读取真实保存的 `play_seconds`、`music_effects` 与姿态说明，使用稳定的配置、数据与运行目录。配置和登录运行独立 `configure`，不在语音业务中启动交互向导。

播放工具参数是 `artist`、`song` 两个字符串，至少一个非空；未提供的字段用空字符串。当前宿主适配将它们传给：

```bash
python3 main.py --config /path/config.yaml --data-dir /path/data --runtime-dir /path/runtime \
  play --artist 周杰伦 --song 晴天
```

`check-play` 接收相同的歌手/歌名，校验配置和恢复状态，不取流或播放；模拟使用 `--simulate`。独立命令、参数和结果也可由其他宿主使用，不要求实现 Buddy API。

## 调度与结束

扬声器、动作、表情、灯光的资源声明和硬超时归 Buddy 侧适配管理。多个点歌任务必须串行；“播放完再关灯”由宿主安排依赖，不能把播放已提交当成完成。点歌自行调用原厂 `PlayMediaFile`，按自有 trace 等待真实 PLAYING/END 和联动清理。独立进程提交播放前先确认状态订阅匹配。

宿主在取消或强杀后使用同一运行目录调用 `status`，必要时调用 `recover`。只有 `resources_released: true` 才能确认本技能没有已知未结束操作；进程消失、恢复命令已发出、ERROR 反馈或退出码正常都不能替代它。已请求停止的标记跨进程保留，新恢复进程不重发未知结果的 Stop。联动恢复记录仍存在时，`recover` 保留记录并要求现场核对，不自动动作。

Q1 已部署的 `TtsStatusType` 使用精确名称 `TTS_STATUE_TYPE_END=3`、`TTS_STATUS_TYPE_STOP=4`；`ERROR=5`、`NOT_INQUE=7` 不确认资源释放。适配读取这些原厂符号，也兼容通用 `END/STOP/STOPPED`，不按任意名称后缀猜测终态；缺少符号时只保守接受已确认的 END=3。

## 续听与当前限制

- 音乐不会从原厂 IDLE 主动唤醒会话；续听依赖宿主在播放和清理完成后安排播报及收音。
- 不保证播放中再次唤醒会取消歌曲并启动新任务；跨轮“再来一首”的点歌上下文仍需宿主处理，缺失时询问。
- 不用直接 Pulse/TCP 或 `PlayAudioFile` 替代原厂媒体生命周期后宣称具有相同续听行为。
- 联动仅在保存配置开启或操作者明确要求本次开启时使用。T1 保持双足/四足形态，四足趴卧只在缓冲成功后准备起身；Q1 不进行形态切换，不从被动/躺姿自动起身。Q1 舞蹈恢复与打断兼容尚未完整实机验收。
- QQ `result=104003` 等错误只说明当前未取得可播放资源，不能据此直接判断 VIP、账号或版权原因；不绕过权限，不输出签名音源地址或登录态。

实际命令与恢复边界见 [README](../README.md)，实现见 [main.py](../main.py) 与 [src/robot/native_media.py](../src/robot/native_media.py)。
