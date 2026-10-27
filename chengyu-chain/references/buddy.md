# Buddy 的整局交互交接

使用包含本次 `interaction: exclusive` 实现的 PrimeBuddy 源码或后续正式版本。旧版通用脚本执行不提供这项保证，不能当作本应用的语音宿主。

宿主的 `cfg/skill-adapters.example.yaml` 映射 configure/context/status/recover/check-play，以及 `play --hosted`。宿主把游戏自己的 config/data/runtime 路径传给 CLI，整个游戏只占一个受监督的前台进程。

`play` 声明 microphone、speaker、robot.motion、robot.expression 资源，并且是独占交互动作，只允许单任务计划。Buddy 完成自己的启动应答后静音、清理旧事件再启动应用；游戏未返回时不识别游戏答案或分发其他技能。正常产品期限是应用的 120 秒无回应，宿主 86400 秒仅为失控进程硬上限。

单局输赢、询问下一局、原厂 IDLE 和唤醒都不解除应用独占。退出应用时必须确认前台进程和其子进程结束、独立 status 返回 `resources_released=true`；确认前保持隔离。确认后丢弃旧音频、建立新的 generation，单次询问“还想做什么？”后接收一条新的用户请求。不能把游戏中说过的点歌/灯控请求排队补执行。

`--hosted` 不设置或恢复原厂模式、不启停 Agent。Buddy 持有共享前端锁。独立模式通过同一控制器的共享锁排斥 Buddy，并由独立应用记录、恢复原模式。共享锁不跨控制器文件系统；操作者须在固定应用板上运行，不能两板同时启动独立前端管理。

安装向导可读取 Buddy 的已有模型作为默认值，确认后写入游戏自己的配置。运行时不读取 Buddy 模型配置、不导入其 Python 包。
