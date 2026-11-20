# 短信与检测配置

在 Jetson 的技能目录执行 `nano cfg/config.yaml`。发布包带空白模板。安装会把该文件的权限收紧为仅当前账号可读写；请在新目录解包升级，保留原有设置。

## 选择一种发送方式

两种方式都要填写 `contact.phone`。当前版本接收一位家属的 11 位中国内地手机号，用引号包住。只校验所选发送方式的配置。

| 使用阿里云短信时填写 | 填什么 |
| --- | --- |
| `sms.provider` | 保持 `'aliyun'` |
| `sms.aliyun.sign_name` | 已审核通过的短信签名 |
| `sms.aliyun.template_code` | 已审核通过的通知模板编号，以 `SMS_` 开头 |
| `sms.aliyun.time_variable` | 模板中接收检测时间的变量名，默认为 `'time'` |
| `sms.aliyun.access_key_id`、`access_key_secret` | 具备发短信权限的 RAM 用户凭证 |

已有自建短信服务的用户可把 `sms.provider` 改为 `'https_gateway'`，在 `sms.https_gateway.url` 填写 HTTPS 地址。网关需要 Bearer 认证时填写 `sms.https_gateway.bearer_token`。如打算只用此方式，可在首次 `install` 前先编辑配置，并执行 `chmod 600 cfg/config.yaml` 限制读取权限。

Jetson 会向网关 POST UTF-8 JSON，字段为 `event_id`、`phone`、`detected_at`（包含时区）和 `message`。网关应按事件编号去重。受理时返回 HTTP 2xx 和 `{"accepted":true,"id":"网关流水号"}`，明确拒绝时返回 HTTP 2xx 和 `{"accepted":false}`。修改发送方式后，重新运行 `python3 main.py install` 准备对应依赖。

## 调整等待时间

| 字段 | 默认值 | 作用 |
| --- | --- | --- |
| `detection.hold_seconds` | 2 秒 | 同一个人的横卧轮廓持续多久才算一次倒地 |
| `voice.response_seconds` | 30 秒 | 每次提问播完、开始收音后等待回答的时间 |

只需填写正数。两次都未收到答复时播报并发送。听到明确的“不需要”时，机器人播报不发送通知，结束后恢复原厂问答。本轮出现无法判定的答复或语音故障时，可从 Jetson 日志查看实际决定。

## 核对发送结果

在 Jetson 执行 `journalctl -u t1-fall-skill-watch.service -o cat`。`SMS_PROVIDER ... accepted` 表示短信服务已受理，家属手机是否收到以实际收件为准。同一事件在提交前会写入机上 SQLite 记录，结果不明时保留现场记录供核查。每轮的文件名对应日志里的 `CASE_LEDGER`，位于 `/robot/persist/t1-fall-alert/private/cases/`。
