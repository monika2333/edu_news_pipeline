要实现程序自动将每日新闻摘要通过飞书发送给你，需通过**飞书开放平台的应用机器人能力**调用发送消息接口完成。以下是具体步骤及参考文档支撑：


## 步骤一：创建并配置飞书自建应用
需创建一个**自建应用**作为消息发送的载体，并开启机器人能力以支持消息收发。

1. **创建自建应用**：  
   登录飞书[开发者后台](https://open.feishu.cn/app)，创建一个**企业自建应用**（参考[应用类型简介](https://go.feishu.cn/s/63f3TEtQI02)）。
   
2. **开启机器人能力**：  
   在应用详情页的**应用能力 > 添加应用能力**页面，添加**机器人**能力（参考[机器人概述](https://go.feishu.cn/s/6aRonz2AA04)）。开启后需发布应用版本使配置生效（参考[发布应用](https://go.feishu.cn/s/63g3FAPNM01#baf09c7d)）。

3. **申请消息发送权限**：  
   在**开发配置 > 权限管理 > API 权限**页面，申请以下权限（参考[申请 API 权限](https://go.feishu.cn/s/5_sDu8Gjo01)）：  
   - `im:message:send_as_bot`（以应用身份发消息）  
   - 若需接收消息可补充`im:message.p2p_msg:readonly`（读取单聊消息），但本场景仅需发送，可忽略。


## 步骤二：获取应用访问凭证（tenant_access_token）
发送消息需使用应用身份的访问凭证`tenant_access_token`，用于接口鉴权。

1. **获取凭证**：  
   通过应用的`App ID`和`App Secret`（在**基础信息 > 凭证与基础信息**页面获取），调用[自建应用获取 tenant_access_token](https://go.feishu.cn/s/64m04iONs04)接口获取`tenant_access_token`。  
   - 示例请求（参考[获取访问凭证](https://go.feishu.cn/s/5_sDxlNO802)）：  
     ```http
     POST https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal/
     Content-Type: application/json
     {
       "app_id": "your_app_id",
       "app_secret": "your_app_secret"
     }
     ```
   - 响应将返回`tenant_access_token`，有效期2小时，需定期刷新（参考[access_token 生命周期管理](https://go.feishu.cn/s/63f3TEtQg02)）。


## 步骤三：获取接收者ID（你的飞书用户ID）
需获取你自己的飞书用户ID（`open_id`或`user_id`），作为消息的接收者。

1. **获取方式**：  
   - 若使用`open_id`：可通过[获取用户 Open ID](https://go.feishu.cn/s/6dXEOYS0I01)接口获取，或在飞书客户端的**个人信息 > 更多 > 开放平台ID**中查看。  
   - 若使用`user_id`：可通过[获取用户 User ID](https://go.feishu.cn/s/6dXEOYS0Y01)接口获取。


## 步骤四：编写代码调用发送消息接口
程序需读取`txt`文件内容，构造消息请求体，调用飞书[发送消息 API](https://go.feishu.cn/s/61BYfgpwE01)发送文本消息。

### 关键参数说明（参考[发送消息 content 结构](https://go.feishu.cn/s/60d1Y6W0s02)）：
- `receive_id_type`：设为`open_id`（与接收者ID类型一致）。  
- `receive_id`：你的飞书`open_id`。  
- `msg_type`：设为`text`（文本消息）。  
- `content`：文本内容，格式为`{"text": "新闻摘要内容"}`（需序列化为字符串）。

### 代码示例（Python）：
```python
import requests
import json

# 1. 配置参数
app_id = "your_app_id"
app_secret = "your_app_secret"
tenant_access_token = "your_tenant_access_token"  # 步骤二获取
receive_id = "your_open_id"  # 步骤三获取
txt_path = "path/to/your/news_summary.txt"  # 新闻摘要文件路径

# 2. 读取txt内容
with open(txt_path, "r", encoding="utf-8") as f:
    news_content = f.read().strip()

# 3. 构造发送消息请求
url = "https://open.feishu.cn/open-apis/im/v1/messages"
headers = {
    "Authorization": f"Bearer {tenant_access_token}",
    "Content-Type": "application/json; charset=utf-8"  # 避免中文乱码
}
params = {
    "receive_id_type": "open_id"
}
data = {
    "receive_id": receive_id,
    "msg_type": "text",
    "content": json.dumps({"text": news_content})  # 序列化文本内容
}

# 4. 发送请求
response = requests.post(url, headers=headers, params=params, json=data)
response_json = response.json()

# 5. 检查响应
if response_json.get("code") == 0:
    print("消息发送成功！")
else:
    print(f"消息发送失败：{response_json.get('msg')}")
```


## 步骤五：定时触发程序
需将程序设置为每日定时运行（例如使用`cron`（Linux/macOS）或**任务计划程序**（Windows）），确保每日生成新闻摘要后自动发送。


## 注意事项
1. **权限与可用范围**：确保你的用户ID在应用的[可用范围](https://go.feishu.cn/s/61J83Er3w0s)内，否则会报错`230013`（参考[机器人相关错误码](https://go.feishu.cn/s/61J83Er3w0s)）。  
2. **字符编码**：请求头需设置`Content-Type: application/json; charset=utf-8`，避免中文乱码（参考[机器人乱码问题解决](https://go.feishu.cn/s/5_sDu8GlM01)）。  
3. **接口频率限制**：发送消息接口的频率限制为[1000次/分钟、50次/秒](https://go.feishu.cn/s/5_sDxlNNQ02)，每日一次发送不会触发限流。


**参考资料支撑**：
- 发送消息API：[发送消息](https://go.feishu.cn/s/61BYfgpwE01)  
- 机器人能力：[机器人概述](https://go.feishu.cn/s/6aRonz2AA04)  
- 访问凭证获取：[获取访问凭证](https://go.feishu.cn/s/5_sDxlNO802)  
- 用户ID获取：[用户身份概述](https://go.feishu.cn/s/63f3TEtPU02)  
- 消息内容格式：[发送消息 content 结构](https://go.feishu.cn/s/60d1Y6W0s02)