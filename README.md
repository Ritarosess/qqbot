# 爱弥斯 QQ 机器人

基于 [NapCat](https://github.com/NapNeko/NapCatQQ)（OneBot v11）+ Python 的 QQ 机器人，人格是《鸣潮》的**爱弥斯（Aemeath）**。

## 功能

| 功能 | 说明 |
|---|---|
| 群聊对话 | @机器人，或以触发词开头 |
| 群聊插话 | 群友闲聊时按概率自然插一句，不必 @；AI 可自行判断「现在不该说话」 |
| 私聊对话 | 管理员私聊即可聊天，上下文与群聊隔离 |
| 人设扮演 | 元气俏皮的电子幽灵少女，短句、去 AI 味、禁止客服腔 |
| 素材包表情 | 只从素材包取图，按标签匹配；找不到就静默不发，绝不输出 `[表情]` 之类的占位文本 |
| 每日总结 | 每天 21:00 为活跃群生成总结 |
| 管理指令 | `/status`、`/clear`、`/summary`、`/emoji`、`/tag` |

## 架构

```
QQ 账号 ──扫码──> NapCat（OneBot v11，Docker 容器）
                        │ ws://127.0.0.1:3001
                        ▼
                 bot.py（Python，systemd 托管）
                        │ HTTPS
                        ▼
                 DeepSeek / OpenAI 兼容接口
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 准备 NapCat

1. 安装 [NapCatQQ](https://github.com/NapNeko/NapCatQQ)（或使用 `deploy/` 里的 Docker 方案）
2. 登录 QQ 账号
3. 开启 **正向 WebSocket 服务器**，端口 `3001`

### 3. 配置环境变量

密钥通过环境变量注入，不要写进代码：

```bash
# Linux / macOS
export AI_API_KEY="sk-你的密钥"
export ADMIN_ID="你的QQ号"

# Windows PowerShell
$env:AI_API_KEY = "sk-你的密钥"
$env:ADMIN_ID = "你的QQ号"
```

### 4. 运行

```bash
python bot.py
```

日志出现 `已连接` 和 `机器人登录号: xxxxx` 即为成功。

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `AI_API_KEY` | 空 | **必填**，AI 接口密钥 |
| `AI_API_BASE` | `https://api.deepseek.com/v1` | 兼容 OpenAI 格式的接口地址 |
| `AI_MODEL` | `deepseek-chat` | 模型名 |
| `ADMIN_ID` | `0` | 管理员 QQ，**务必改成自己的**，否则私聊对话与管理指令不可用 |
| `ONEBOT_WS_URL` | `ws://127.0.0.1:3001` | NapCat 的 WebSocket 地址 |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` |
| `EMOJI_DIR` | `emojis` | 素材包目录 |
| `RECONNECT_INTERVAL` | `5` | 断线重连间隔（秒） |
| `GROUP_CHAT` | `1` | 是否启用群聊自然插话 |
| `GROUP_CHAT_PROBABILITY` | `0.10` | 每条群消息的插话概率 |
| `GROUP_CHAT_COOLDOWN` | `120` | 同一群最短插话间隔（秒） |
| `GROUP_CHAT_CONTEXT` | `12` | 插话时参考的最近消息条数 |
| `EMOJI_QQ_FACES` | `0` | 设为 `1` 才启用 QQ 收藏表情 |
| `EMOJI_AUTO_COLLECT` | `0` | 设为 `1` 才自动收藏群友发的图片 |

## 使用

### 群聊触发

| 方式 | 示例 |
|---|---|
| @机器人 | `@爱弥斯 你好` |
| 触发词 | `bot 今天天气怎么样` |
| 触发词 | `爱弥斯 在吗` |
| 触发词 | `小爱 帮我看看` |
| 触发词 | `aemeath hi` |

触发词定义在 `config.py` 的 `TRIGGER_KEYWORDS`。回应后 3 秒内不会重复响应（`COOLDOWN`）。

### 私聊

只有管理员（`ADMIN_ID`）私聊有效：

- 普通内容 → 走 AI 对话
- `/` 开头 → 走管理指令
- 其他人私聊 → 直接忽略（防止盗刷 API 额度）

### 群聊插话

不需要 @，机器人会偶尔自己接话。三层控制防止刷屏：

1. **概率**：每条消息只有 `GROUP_CHAT_PROBABILITY`（默认 10%）的机会进入判断
2. **冷却**：同一群两次插话至少间隔 `GROUP_CHAT_COOLDOWN`（默认 120 秒）
3. **AI 自判**：把最近 `GROUP_CHAT_CONTEXT` 条聊天记录交给模型，觉得不合适就回 `[skip]`，机器人保持安静

以下消息不会触发插话：机器人自己发的、`/` 开头的指令、纯卡片/CQ 消息、单字消息。

觉得太吵或太安静，调环境变量即可，不用改代码：

```bash
GROUP_CHAT_PROBABILITY=0.05   # 更安静
GROUP_CHAT_COOLDOWN=300       # 最少 5 分钟一次
GROUP_CHAT=0                  # 完全关闭，只在被叫时说话
```

### 管理指令

| 指令 | 功能 |
|---|---|
| `/status` | 查看运行状态 |
| `/help` | 指令列表 |
| `/clear <群号>` | 清空指定群的上下文 |
| `/summary` | 手动触发一次今日总结 |
| `/emoji` | 列出素材包表情 |
| `/tag <序号> <关键词>` | 给表情追加标签（仅内存生效） |
| `/fetch_emoji` | 纯素材包模式下提示已禁用 |

### 表情标签

`[emoji:标签]` 只支持以下标签（由素材包文件名生成）：

```
开心 喜欢 难过 生气 害羞 疑惑 惊讶 叹气 睡觉
卖萌 打钱 工作 偷看 自嘲 卡住 等待 早安 暧昧 笨蛋
```

同时支持英文分类名（`happy`、`sad`、`angry`…）。

## 表情包系统

项目采用**纯素材包模式**：只扫描 `emojis/` 下文件名含中文标签的图片，不使用 QQ 收藏表情，也不自动收藏群友图片。

### 命名规则

```
<中文情绪>_<英文分类>_<序号>.<扩展名>
例如：开心_happy_01.gif
```

`EmojiManager` 按 `_` 拆成关键词，所以 `[emoji:开心]` 和 `[emoji:happy]` 都能命中。

### 发送方式

图片以 **`base64://` 内嵌**发送，而不是传文件路径——NapCat 跑在 Docker 容器里，看不到宿主机的 `/opt/qqbot/emojis`，传路径会导致整条消息发送失败。

### 导入素材包

```bash
curl -fsSL -o /tmp/memes.tar.gz \
  https://codeload.github.com/shirachino/astrbot-Aemeath-meme-pack/tar.gz/refs/heads/main
mkdir -p /tmp/memes && tar -xzf /tmp/memes.tar.gz -C /tmp/memes --strip-components=1
bash deploy/import_memes.sh /tmp/memes/memes emojis
```

导入脚本会按分类重命名文件。旧版表情已归档到 `emojis/legacy/`，不会被扫描。

## 项目结构

```
QQBOT/
├── bot.py                # 主程序：连接、事件循环、私聊/群聊分发、定时总结
├── config.py             # 人设、触发器、模型参数（含环境变量读取）
├── config.example.py     # 配置模板（复制为 config.py）
├── ai_client.py          # AI 客户端（OpenAI 兼容，含 429 退避）
├── message_handler.py    # 群聊消息处理、表情消息段构造
├── emoji_manager.py      # 素材包扫描与关键词匹配（纯素材包模式）
├── conversation.py       # 上下文与每日消息记录
├── summary_scheduler.py  # 旧版调度器，逻辑已内联到 bot.py，当前未引用
├── requirements.txt      # 依赖（仅 aiohttp）
├── emojis/               # 素材包（legacy/ 为归档的旧图，不参与扫描）
├── deploy/               # 部署脚本与运维文档
├── tools/                # 文档提取等辅助脚本
├── 人格提示词/           # 原始人设文档与提取文本
└── README.md
```

## 部署

生产部署（阿里云 ECS + Docker + systemd）见 [deploy/README.md](deploy/README.md)。

## 常见问题

**@机器人没反应**
按顺序查：`sudo journalctl -u qqbot -f` 是否在重连、`sudo docker ps` 容器在不在、NapCat 是否已登录。详见部署文档的排查章节。

**表情发不出来 / 整条消息消失**
说明图片段发送失败。本项目已改用 base64 发送，若仍复现，检查 NapCat 日志里的 `文件处理失败`。

**AI 报 401 / 429**
401 是密钥错误；429 是限流，代码内置 30 秒退避。

**想恢复 QQ 收藏表情**
在环境变量里设 `EMOJI_QQ_FACES=1`，重启后生效。

## 许可证

MIT License

## 致谢

- [NapCatQQ](https://github.com/NapNeko/NapCatQQ) — QQ 协议端
- [DeepSeek](https://platform.deepseek.com) — AI 大模型
- [astrbot-Aemeath-meme-pack](https://github.com/shirachino/astrbot-Aemeath-meme-pack) — 表情素材（CC0）
