# Q1 原厂预设舞蹈联动

点歌台直接调用原厂 ROS 2 接口，不依赖 Buddy 的动作或播放 API。型号来自规范身份信息；本页只描述 Q1，T1 保留原有双足/四足联动。

## 原厂能力

用户提供的 `primebot-q1-sdk` 定义 `Q_DANCE_A=3046`、`Q_DANCE_B=3047`、`Q_DANCE_C=3048`，属于 `SetMcPresetMotion`，与全身模仿动作 `SetMcMotion/DANCE_LOCKING` 不同。原厂手册要求预设请求之前先进入 `BIPED_WHOLE_BODY_CTRL(1005)` 并确认运行。SDK 的 `DEFAULT_POS=0` 表示恢复默认姿态。

`SetMcPresetMotion` 使用 `header/source/area/motion/interrupt/ani_path`，响应的 `response` 包含 `header.code`、`state.value/reason`、`task_id`。当前实现接受 code=0、reason=0 且 state 为 SUCCESS(1) 或 RUNNING(400)，随后通过已部署的 `GetMcPresetMotionState` 与新鲜 MC task ID 共同确认执行生命周期；RPC 被接受不表示实体动作已完成。该查询接口未附在这份 SDK 源码中，机上必须实际具备它，缺失时拒绝联动。

预设请求沿用官方示例的 area 默认值 0，使用内置动作、不传自定义轨迹。`area` 不会裁剪轨迹或缩小幅度；配置中的 `motion_area` 也不是 `McControlArea` 的同一枚举。A/B/C 是否符合演示所需幅度，须结合机上资源和现场关节运动确认。

## 播放和结束

1. 歌曲缓冲后检查 1002 静止、新鲜运控与灯光反馈、未跌倒、未提起或抱持。播放开始前不切换运控模式。
   同时从 `GetMcMotions` 核对 `0/3046/3047/3048` 四项实际注册为 ANIMATION(1)、自由打断(1)；只有 SDK 枚举存在不算资源可用。
2. 本次 `PlayMediaFile` 收到 PLAYING 后，保存恢复记录，切入 1005 并确认，再随机提交 A/B/C。每次提交后立即查询，绑定提交后新鲜 MC 中的自有 task ID，先观察 RUNNING(400) 再接受后续 SUCCESS(1)。加载前的 SUCCESS 只等待，最多 8 秒仍不能确认则失败。首段确认 RUNNING 后才请求表情，避免表情 RPC 挡住短动作的观测窗口。
3. 每次动作写入前检查本次媒体的最新状态。等待服务期间收到 END、STOP 或错误后，不再续发舞蹈；不通过固定时间猜测歌曲或动作完成。
4. 音乐结束或协作取消后，停止续发，核对任务和控制权。在本任务拥有的 1005 下提交 `DEFAULT_POS=0, interrupt=true`，为新 task 重新建立 RUNNING→SUCCESS 生命周期后再恢复 1002；默认姿态很短、漏掉 RUNNING 时，仅允许以独立查询 1002/status100、同一新 task 的提交后新鲜 MC 1002 静止反馈确认归位，不能凭全局 SUCCESS 强制切换。若原厂已经完成本段并自行返回 1002，则直接核实恢复状态。最后恢复自身灯光及表情、清除记录。

原厂运控动作状态 RUNNING=100 与通用任务 RUNNING=400 是不同枚举。写控制请求之间保留至少 120 ms 间隔，避免已观察到的短间隔拒绝。

出现请求结果未知、任务 ID 不匹配、任务中止、外部模式接管、反馈过期或归位未确认时，保留恢复记录，不重复强制控制、不声称资源释放。`recover` 仍不自动重放身体动作。进程遭强杀时无法保证运行清理。

## 验证边界

本地回归覆盖随机分段、等待原厂任务完成、音乐 END 的续发竞态、默认姿态和 1002 恢复、未知请求及外部接管保留记录。模拟不替代实体动作验收，原始安装配置仍默认关闭联动；下述通过实测的这台 Q1 在用户确认后开启，其他部署须自行确认现场条件与兼容性。

2026-10-20 首次正式路径实测未通过：已提交一段 C，并采到关节位置变化，但未完成随机轮换、随音乐结束归位的完整验收。随后只读检查对 `GetMcPresetMotionState` 分别传入本次 ID、0 和不存在的 ID，均得到 `response.task_id=0, code=0, state=SUCCESS(1), reason=0`。此前要求响应返回指定 ID 的版本因此拒绝续发并保留记录。

随后一次明确授权的 C 诊断观察到：运行期间查询返回 ID=0/state400，同时新鲜 MC task ID 为本次自有任务；结束后同 task 下查询返回 ID=0/state1，并独立采到 1002 静止反馈。因此本机查询按全局状态兼容，而不是假定请求 ID 被服务端使用。当前代码只在提交后新鲜 MC task 归属一致时采用全局状态，确认归属后若 task 变化立即停止续发。进入下一段之前的自有 SetMcAction(1005) 使用其单独响应 ID 跟踪模式交接，控制写入前仍检查归属。

`Q1_PRESET_STATUS` 仅在状态变化时记录请求/返回任务 ID、code/state/reason，以及新鲜 MC 反馈中的 task ID；不输出完整原厂响应或配置。

修复后第二次明确授权的正式 CLI 实测通过：同一媒体 trace PLAYING→END 为 60.122 秒；连续 13 段 C/A/B/C/A/B/C/A/C/A/B/C/A，无相邻重复；最后一段尚未播完时收到音乐 END，随后提交默认预设 0。各段与归位任务均采到自有 MC task 下的 RUNNING→SUCCESS，最终新鲜反馈为 1002/status100、静止、未跌倒、播放器空闲，CLI 返回 success/resources_released=true、无待恢复记录。用户确认全程持续轮换、歌停后恢复双脚稳定站立，三个动作幅度均适合演示。该轮没有启动 Buddy 或重测 only_voice、唤醒及免唤醒续听。

提前打断音乐仍有限制：本机 `StopTtsTraceId` 曾返回成功但媒体继续自然播完，`GetAudioStatus` 未提供匹配 trace 的持久终态。因此不能据这次自然结束验收宣称提前停止音乐已修复，也不以无任务字段的全局停止接口盲目替代。
