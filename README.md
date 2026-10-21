# 爱弥斯 QQ 机器人

基于 [NapCat](https://github.com/NapNeko/NapCatQQ)（OneBot v11）+ Python 的 QQ 机器人，人格是《鸣潮》的**爱弥斯（Aemeath）**。

表情来自第三方仓库 [呜哇小站 · 表情包仓鼠库](https://emoji.wuwa.games)，支持全站随机和按角色点名。

## 功能

| 功能 | 说明 |
|---|---|
| 群聊对话 | @机器人，或以触发词开头 |
| 群聊插话 | 群友闲聊时按概率自然接话，不必 @，AI 可自行判断是否开口 |
| 私聊对话 | 仅管理员可用，上下文与群聊隔离 |
| 人设扮演 | 元气俏皮的电子幽灵少女；短句、去 AI 味、禁止客服腔 |
| 表情库 | 远程表情仓库，支持随机、指定角色、外号与错别字匹配 |
| 群白名单 | 只在指定群参与，其他群完全静默 |
| 每日总结 | 每天 21:00 为活跃群生成总结 |
| 管理指令 | `/status`、`/help`、`/clear`、`/summary`、`/emoji` |

## 架构

```
QQ 账号 ──扫码──> NapCat（OneBot v11，Docker 容器）
                        │ ws://127.0.0.1:3001
                        ▼
                 bot.py（Python，systemd 托管）
                        │ HTTPS
                        ▼
              DeepSeek / OpenAI 兼容接口
                        │ HTTPS
                        ▼
              呜哇小站表情仓库 API
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
| `ADMIN_ID` | `0` | 管理员 QQ，**务必改成自己的**，否则私聊与管理指令不可用 |
| `ONEBOT_WS_URL` | `ws://127.0.0.1:3001` | NapCat 的 WebSocket 地址 |
| `RECONNECT_INTERVAL` | `5` | 断线重连间隔（秒） |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` |
| `EMOJI_DIR` | `emojis` | 表情缓存根目录（`wuwa_cache` 存放位置） |
| `GROUP_CHAT` | `1` | 是否启用群聊自然插话 |
| `GROUP_CHAT_PROBABILITY` | `0.10` | 每条群消息进入判断的概率 |
| `GROUP_CHAT_COOLDOWN` | `120` | 同一群最短插话间隔（秒） |
| `GROUP_CHAT_CONTEXT` | `12` | 插话时参考的最近消息条数 |
| `GROUP_WHITELIST` | 空 | 只在这些群发言（逗号分隔）；留空 = 全部群 |
| `WUWA_EMOJI_ENABLED` | `1` | 是否启用远程表情仓库 |
| `WUWA_CHARACTER` | `任意` | `任意` = 全站随机；也可固定角色名 |
| `WUWA_TOKEN` | 空 | 可选，站点 Token，可提高调用额度（匿名也能用） |

## 使用

### 群聊触发

| 方式 | 示例 |
|---|---|
| @机器人 | `@爱弥斯 你好` |
| 触发词 | `bot 今天天气怎么样` |
| 触发词 | `爱弥斯 在吗` |
| 触发词 | `小爱 帮我看看` |
| 触发词 | `aemeath hi` |

触发词定义在 `config.py` 的 `TRIGGER_KEYWORDS`。被触发后 3 秒内不会重复响应（`COOLDOWN`）。

### 私聊

只有管理员（`ADMIN_ID`）私聊有效：

- 普通内容 → AI 对话
- `/` 开头 → 管理指令
- 其他人私聊 → 直接忽略（防止盗刷 API 额度）

### 群聊插话

不需要 @，机器人会按概率自己接话。三层控制防止刷屏：

1. **概率**：每条消息只有 `GROUP_CHAT_PROBABILITY` 的机会进入判断
2. **冷却**：同群两次插话至少间隔 `GROUP_CHAT_COOLDOWN`
3. **AI 自判**：参考最近 `GROUP_CHAT_CONTEXT` 条消息，确实不适合才回 `[skip]`

以下消息不会触发：机器人自己发的、`/` 开头的、纯卡片/CQ 消息、单字消息。

调参只需要改环境变量：

```ini
GROUP_CHAT_PROBABILITY=0.20   # 更活跃（当前部署值）
GROUP_CHAT_COOLDOWN=60        # 更频繁
GROUP_CHAT_PROBABILITY=0.05   # 更安静
GROUP_CHAT=0                  # 关闭，只在被叫时说话
```

### 群白名单

```ini
GROUP_WHITELIST=753063398,123456789
```

名单生效后，非白名单群**完全不参与**：@ 不回复、不插话、不发总结，连消息都不记录。留空则所有群参与。

私聊不受影响，仍只对 `ADMIN_ID` 生效。

### 管理指令

| 指令 | 功能 |
|---|---|
| `/status` | 运行状态与当前白名单 |
| `/help` | 指令列表 |
| `/clear <群号>` | 清空指定群的上下文 |
| `/summary` | 手动触发一次今日总结 |
| `/emoji` | 查看表情库状态 |

> `/tag`、`/fetch_emoji` 已停用（本地素材包和 QQ 收藏表情都已移除）。

## 表情系统

### 标记写法

```
[emoji]          → 全站随机一张
[emoji:角色名]    → 指定角色，如 [emoji:菲比]、[emoji:今汐]
```

角色匹配按以下顺序进行，不需要配置：

1. **别名**（外号 / 昵称 / 错别字），完全相等优先，其次包含匹配
2. 角色名精确匹配
3. 角色名包含匹配
4. **拼音匹配**（消除谐音、错别字，如「达尼亚」→「达妮娅」）
5. 形近字相似度兜底
6. 都匹配不到 → 回退全站随机

### 内置别名

| 别名 | 角色 |
|---|---|
| 耙耙柑 / 西西 | 西格莉卡 |
| 水蜜桃 / 娅娅 / 达尼亚 / 达尼娅 / 达妮雅 | 达妮娅 |
| 糯糯 | 弗洛洛 |
| 嘻嘻 / 夕夕 / 汐汐 / xixi | 今汐 |
| 青霄 | 清宵 |
| 小爱 / 小爱同学 | 爱弥斯 |

外号表在 `wuwa_emoji.py` 的 `NAME_ALIASES`，新增一行即可。

### 图片来源与缓存

- 只使用远程仓库，全站约 1.5 万张、69 个角色
- 指定角色时只从**单角色合集**取图，避免多人合集里出现别的角色
- 按需下载到 `emojis/wuwa_cache/`，最多 200 张，单张超过 3MB 自动跳过并重试
- 发送时以 `base64://` 内嵌，避免 NapCat 容器读不到宿主机路径
- 缓存目录已在 `.gitignore` 中，不会提交

> 远程表情来自第三方站点，属于游戏素材/同人作品，仅建议自用，不要二次分发。

## 项目结构

```
QQBOT/
├── bot.py                # 主程序：连接、事件分发、私聊/群聊、定时总结
├── config.py             # 人设、触发器、运行参数（含环境变量读取）
├── config.example.py     # 配置模板（复制为 config.py）
├── ai_client.py          # AI 客户端（OpenAI 兼容，含 429 退避）
├── message_handler.py    # 群聊处理、表情解析与消息段构造
├── emoji_manager.py      # 表情管理（远程模式）
├── wuwa_emoji.py         # 呜哇小站 API 客户端：下载、缓存、角色匹配
├── conversation.py       # 上下文与每日消息记录
├── summary_scheduler.py  # 旧版调度器（逻辑已内联到 bot.py，未引用）
├── requirements.txt      # 依赖：aiohttp、pypinyin
├── emojis/               # 运行期生成，仅存放 wuwa_cache
├── deploy/               # 部署脚本与运维文档
├── tools/                # 文档提取等辅助脚本
├── 人格提示词/           # 人设原始文档与提取文本
└── README.md
```

## 部署

### 本地运行

```bash
pip install -r requirements.txt
python bot.py
```

前提：NapCat 已在本机运行并登录（正向 WebSocket 3001），环境变量已设置。

### 交给 AI 本地部署

不想自己操作的话，把这段发给能执行命令的 AI：

```text
请在本机部署 QQ 机器人：https://github.com/Ritarosess/qqbot

1. 确认 Python 3.10+，克隆仓库后执行 pip install -r requirements.txt
2. 安装并启动 NapCatQQ，登录 QQ，开启正向 WebSocket（端口 3001）
3. 设置环境变量 AI_API_KEY、ADMIN_ID；可选 GROUP_WHITELIST、GROUP_CHAT_PROBABILITY、GROUP_CHAT_COOLDOWN
4. 运行 python bot.py，日志出现「已连接」「机器人登录号」即成功
5. 在群里 @机器人 验证

限制：密钥不要写进代码；NapCat 必须保持运行；表情来自远程接口，缓存到 emojis/wuwa_cache。
```

### 服务器部署

阿里云 ECS + Docker + systemd 的完整方案见 [deploy/README.md](deploy/README.md)。

## 常见问题

**@机器人没反应**

按链路排查：`sudo journalctl -u qqbot -f` 是否在重连 → `sudo docker ps` 容器是否运行 → NapCat 是否已登录。详见部署文档。

**表情发不出来 / 整条消息消失**

图片段发送失败会导致整条消息被丢弃。本项目已用 base64 发送；若仍复现，检查 NapCat 日志中的 `文件处理失败`。

**AI 报 401 / 429**

401 是密钥错误；429 是限流，代码内置 30 秒退避。

**角色表情不对**

先看日志里的 `按角色取表情` 是否命中正确角色；若是多角色合集导致的错图，已通过单角色过滤规避。仍不对时可把角色名加入 `NAME_ALIASES` 或反馈具体案例。

## 许可证

MIT License

## 致谢

- [NapCatQQ](https://github.com/NapNeko/NapCatQQ) — QQ 协议端
- [DeepSeek](https://platform.deepseek.com) — AI 大模型
- [呜哇小站 · 表情包仓鼠库](https://emoji.wuwa.games) — 远程表情来源
