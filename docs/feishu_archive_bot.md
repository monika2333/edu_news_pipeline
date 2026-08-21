# 飞书报送存档机器人

飞书报送存档机器人通过长连接接收受信任用户的私聊文本。用户无需添加命令前缀；消息首个非空行必须是当前支持的报送稿标题，系统才会继续解析和入库。普通聊天、群聊、非文本消息和非白名单用户消息均静默忽略。

当前支持的首行标题：

- `首都教育每日舆情综报`
- `首都教育舆情`

识别成功后，机器人直接创建存档并启动既有自动回链 worker。解析警告不阻止保存，但会在飞书回复中列出。同一报别和日期已有存档时不会自动覆盖；飞书事件重推也会由消息 ID 幂等去重。

## 飞书开放平台配置

在当前 `FEISHU_APP_ID` 对应的企业自建应用中：

1. 开启机器人能力。
2. 为机器人申请发送消息，以及获取用户发给机器人的单聊消息权限。
3. 配置下文环境变量，并先以前台方式启动一次机器人；飞书保存长连接订阅方式时需要检测到在线连接。
4. 在「事件与回调」中选择长连接订阅方式。
5. 添加「接收消息 v2.0」事件（`im.message.receive_v1`）。
6. 发布新版本，并确保应用可用范围包含允许提交存档的用户。

长连接只要求运行机器能够访问飞书公网，不需要为控制台新增公网回调地址。

## 环境变量

```env
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=replace-with-feishu-app-secret
FEISHU_ARCHIVE_ALLOWED_OPEN_IDS=ou_xxx
```

多个允许用户用英文逗号分隔。未设置 `FEISHU_ARCHIVE_ALLOWED_OPEN_IDS` 时，如果 `FEISHU_RECEIVE_ID_TYPE=open_id`，会把现有 `FEISHU_RECEIVE_ID` 作为唯一白名单用户。

## 启动与常驻运行

前台验证：

```powershell
.\venv\Scripts\python.exe -m src.cli.main feishu-archive-bot
```

验证正常后，可用管理员 PowerShell 注册开机任务：

```powershell
.\scripts\register_feishu_archive_bot_task.ps1
```

默认日志写入 `logs/feishu_archive_bot.log`。修改飞书权限或事件订阅后，需要在飞书开放平台重新发布应用版本。
