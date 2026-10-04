# QQ 机器人 - 樱井莉奈

LV动漫社的社娘机器人，基于 NapCat + Python 开发。

## 功能特性

- **AI 对话**：@机器人 或发送触发词即可与 AI 对话
- **人设扮演**：扮演 LV动漫社社娘「樱井莉奈」，粉发绿瞳双马尾，元气偶像气质
- **表情包**：自动收藏群友/私聊发送的 QQ 收藏表情，AI 回复时随机使用
- **每日总结**：每天晚上 21:00 自动发送群聊总结
- **管理员指令**：私聊机器人可使用管理命令

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 NapCat

1. 下载并安装 [NapCatQQ](https://github.com/NapNeko/NapCatQQ)
2. 登录 QQ 账号
3. 开启 **WebSocket 服务器**，端口设为 `3001`

### 3. 配置环境变量

```bash
# Windows PowerShell
$env:AI_API_KEY = "你的AI密钥"
$env:AI_API_BASE = "https://api.deepseek.com/v1"
$env:AI_MODEL = "deepseek-chat"
$env:ADMIN_ID = "你的QQ号"

# Linux/Mac
export AI_API_KEY="你的AI密钥"
export AI_API_BASE="https://api.deepseek.com/v1"
export AI_MODEL="deepseek-chat"
export ADMIN_ID="你的QQ号"
```

### 4. 运行

```bash
python bot.py
```

## 使用方法

### 群聊触发

| 触发方式 | 示例 |
|---------|------|
| @机器人 | `@樱井莉奈 你好` |
| 触发词 | `bot 今天天气怎么样` |
| 触发词 | `樱井莉奈 在吗` |

### 表情包功能

1. **导入收藏表情**：私聊机器人发送你的 QQ 收藏表情，会自动保存
2. **查看表情列表**：私聊发送 `/emoji`
3. **给表情打标签**：私聊发送 `/tag 序号 关键词`
4. **机器人使用**：AI 回复时会随机使用收藏的表情

### 管理员指令（私聊）

| 指令 | 功能 |
|-----|------|
| `/status` | 查看机器人状态 |
| `/emoji` | 查看已保存的表情包列表 |
| `/tag 序号 关键词` | 给表情包添加标签 |
| `/clear 群号` | 清空指定群的聊天上下文 |
| `/summary` | 手动触发今日总结 |
| `/fetch_emoji` | 重新获取 QQ 收藏表情 |
| `/reload_emoji` | 重新扫描本地表情包文件夹 |

## 项目结构

```
QQBOT/
├── bot.py              # 主程序：WebSocket 连接、事件循环
├── config.py           # 配置文件：API 密钥、人设词、触发词
├── message_handler.py  # 消息处理：触发判断、AI 调用、回复发送
├── emoji_manager.py    # 表情包管理：收藏、查找、下载
├── conversation.py     # 会话管理：上下文记录、每日消息记录
├── ai_client.py        # AI 客户端：DeepSeek/智谱等兼容接口
├── summary_scheduler.py # 定时任务：每日总结调度器
├── requirements.txt    # Python 依赖
├── emojis/             # 本地表情包文件夹（自动创建）
└── README.md           # 本文件
```

## 技术栈

- **NapCat**：QQ 协议端，提供 OneBot v11 接口
- **Python 3.10+**：后端逻辑
- **aiohttp**：异步 HTTP/WebSocket 客户端
- **DeepSeek API**：AI 大模型（可替换为其他兼容 OpenAI 格式的接口）

## 服务器部署

### 打包上传

```bash
# 本地打包
cd c:\Users\你的用户名\Desktop
Compress-Archive -Path QQBOT -DestinationPath QQBOT.zip

# 上传到服务器
scp QQBOT.zip root@服务器IP:/root/
```

### 服务器运行

```bash
# 解压
unzip QQBOT.zip -d QQBOT
cd QQBOT

# 安装依赖
pip3 install -r requirements.txt

# 后台运行
nohup python3 bot.py > bot.log 2>&1 &

# 查看日志
tail -f bot.log
```

### systemd 服务（开机自启）

创建 `/etc/systemd/system/qqbot.service`：

```ini
[Unit]
Description=QQ Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/QQBOT
Environment=AI_API_KEY=你的密钥
Environment=ADMIN_ID=你的QQ号
ExecStart=/usr/bin/python3 /root/QQBOT/bot.py
Restart=always

[Install]
WantedBy=multi-user.target
```

启用：
```bash
systemctl daemon-reload
systemctl enable qqbot
systemctl start qqbot
```

## 注意事项

1. **NapCat 必须运行**：机器人依赖 NapCat 提供 QQ 连接
2. **API 密钥安全**：不要将 `AI_API_KEY` 提交到 Git
3. **表情包版权**：QQ 收藏表情仅供个人使用，注意版权
4. **频率限制**：AI 接口有调用频率限制，已内置 429 退避机制

## 许可证

MIT License

## 致谢

- [NapCatQQ](https://github.com/NapNeko/NapCatQQ) - QQ 协议端
- [DeepSeek](https://platform.deepseek.com) - AI 大模型
