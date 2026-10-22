# 爱弥斯 QQ 机器人

基于 [NapCat](https://github.com/NapNeko/NapCatQQ)（OneBot v11）+ Python 的 QQ 机器人，人格是《鸣潮》的**爱弥斯（Aemeath）**。支持文字聊天、远程表情包、千问平台语音合成（含爱弥斯复刻音色）与每日群聊总结。

> 表情来自第三方仓库 [呜哇小站 · 表情包仓鼠库](https://emoji.wuwa.games)；语音使用千问 AI 平台（CosyVoice / MiniMax）。

---

## 功能特性

| 功能 | 说明 |
|---|---|
| 群聊对话 | @机器人，或以触发词（`bot` / `爱弥斯` / `小爱` / `aemeath`）开头 |
| 群聊插话 | 群友闲聊时按概率自然接话，AI 自行判断是否开口 |
| 私聊对话 | 仅管理员可用，上下文与群聊隔离 |
| 人设扮演 | 元气俏皮的电子幽灵少女；短句、去 AI 味、禁止客服腔 |
| 表情库 | 远程表情仓库，支持随机、点名角色、外号/谐音匹配 |
| 语音合成 | 爱弥斯复刻音色、显式触发、**25% 概率自动语音回复** |
| 每日总结 | 每天 21:00 为活跃群生成总结（纯文字，不含表情标记） |
| 群白名单 | 只在指定群参与，其他群完全静默；支持私聊 `/allow` 动态增删 |
| 管理指令 | `/status`、`/help`、`/clear`、`/summary`、`/emoji`、`/voice`、`/allow`、`/deny` |

---

## 架构

```
手机 QQ ──扫码──> NapCat（OneBot v11，Docker）
                      │ ws://127.0.0.1:3001
                      ▼
               bot.py（Python，systemd）
                 │            │
      HTTPS ─────┘            └───── WSS
         ▼                                ▼
 DeepSeek / OpenAI 兼容接口        千问 AI 平台
                                   （CosyVoice 语音合成）
```

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

仅依赖 `aiohttp`、`pypinyin`；语音走原生 WebSocket，不需要额外 SDK。

### 2. 准备 NapCat

1. 安装 [NapCatQQ](https://github.com/NapNeko/NapCatQQ)，或使用 `deploy/` 里的 Docker 方案
2. 登录 QQ 账号
3. 开启**正向 WebSocket 服务器**，端口 `3001`

### 3. 基础配置

密钥一律走环境变量，不要写进代码：

```bash
# Linux / macOS
export AI_API_KEY="sk-你的DeepSeek密钥"
export ADMIN_ID="你的QQ号"

# Windows PowerShell
$env:AI_API_KEY = "sk-你的DeepSeek密钥"
$env:ADMIN_ID = "你的QQ号"
```

### 4. 运行

```bash
python bot.py
```

日志出现 `已连接`、`机器人登录号: xxx` 即为成功；启用语音时会多一行 `语音合成已启用`。

---

## 配置

### AI 与运行环境

| 变量 | 默认 | 说明 |
|---|---|---|
| `AI_API_KEY` | 空 | **必填**，AI 接口密钥 |
| `AI_API_BASE` | `https://api.deepseek.com/v1` | OpenAI 兼容接口地址 |
| `AI_MODEL` | `deepseek-chat` | 模型名 |
| `ADMIN_ID` | `0` | 管理员 QQ，**务必改成自己的** |
| `ONEBOT_WS_URL` | `ws://127.0.0.1:3001` | NapCat WebSocket 地址 |
| `RECONNECT_INTERVAL` | `5` | 断线重连间隔（秒） |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` |
| `EMOJI_DIR` | `emojis` | 表情缓存根目录 |

### 群聊行为

| 变量 | 默认 | 说明 |
|---|---|---|
| `GROUP_CHAT` | `1` | 是否启用自然插话 |
| `GROUP_CHAT_PROBABILITY` | `0.10` | 每条群消息进入插话判断的概率 |
| `GROUP_CHAT_COOLDOWN` | `120` | 同群最短插话间隔（秒） |
| `GROUP_CHAT_CONTEXT` | `12` | 插话参考的最近消息条数 |
| `GROUP_WHITELIST` | 空 | 初始白名单（逗号分隔）；留空 = 全部群 |
| `WHITELIST_FILE` | `whitelist.json` | 运行时白名单持久化文件（`/allow`、`/deny` 修改后写入） |

### 表情

| 变量 | 默认 | 说明 |
|---|---|---|
| `WUWA_EMOJI_ENABLED` | `1` | 是否启用远程表情仓库 |
| `WUWA_CHARACTER` | `任意` | `任意` = 全站随机；也可固定角色名 |
| `WUWA_TOKEN` | 空 | 可选，站点 Token，可提高额度 |
| `EMOJI_QQ_FACES` | `0` | 兼容旧版 QQ 收藏表情，一般不用开 |

### 语音

| 变量 | 默认 | 说明 |
|---|---|---|
| `TTS_ENABLED` | `0` | `1` 开启语音功能 |
| `DASHSCOPE_API_KEY` | 空 | **语音必填**，[千问 AI 平台](https://platform.qianwenai.com) Key |
| `TTS_MODEL` | `cosyvoice-v3.5-flash` | `cosyvoice-*` / `qwen-audio-*` 走 WebSocket；`MiniMax/*` 走 HTTP |
| `TTS_VOICE` | 爱弥斯复刻音色 ID | 复刻音色或系统音色（如 `longanyang`） |
| `VOICE_REPLY_PROBABILITY` | `0.25` | 普通回复改用语音的概率，`0` = 关闭 |
| `VOICE_MAX_CHARS` | `120` | 概率语音最大字数，超长回退文字 |
| `TTS_COOLDOWN` | `10` | 语音冷却（秒），显式触发与概率语音共用 |
| `VOICE_TRIGGER_PREFIXES` | 见下 | 语音触发前缀，逗号分隔 |

默认触发前缀：

```
语音,说,念,读,朗读,用语音说,发语音,语音回复,读一下,念一下,念一遍,朗读一下,用声音
```

---

## 使用指南

### 群聊对话

| 方式 | 示例 |
|---|---|
| @机器人 | `@爱弥斯 你好` |
| 触发词 | `小爱 帮我看看` |
| 触发词 | `aemeath hi` |

触发词定义在 `config.py` 的 `TRIGGER_KEYWORDS`，被触发后 3 秒内不重复响应（`COOLDOWN`）。

### 私聊

仅对 `ADMIN_ID` 生效：普通内容走 AI 对话，`/` 开头为管理指令，其他人直接忽略。

### 群聊插话

不需要 @，按概率自然接话。三道闸门：概率进入判断 → 群内冷却 → AI 自判（不适合时回 `[skip]`）。

```ini
GROUP_CHAT_PROBABILITY=0.20   # 更活跃
GROUP_CHAT_COOLDOWN=60        # 更频繁
GROUP_CHAT=0                  # 关闭，只在被叫时说话
```

### 群白名单

**初始配置**（环境变量）：

```ini
GROUP_WHITELIST=753063398,123456789
```

**运行时动态管理**（管理员私聊）：

| 指令 | 作用 |
|---|---|
| `/allow <群号>` | 把群加入白名单，立即生效 |
| `/allow list` | 查看当前白名单 |
| `/deny <群号>` | 把群移出白名单 |

- 修改会原子写入 `whitelist.json`，重启机器人后仍然有效
- 首次启动时从 `GROUP_WHITELIST` 初始化，之后以文件为准
- **白名单为空 = 对所有群生效**；`/deny` 清空后会提示，需要限制请重新 `/allow`

非白名单群完全不参与：不回复、不插话、不发总结、不记录消息。私聊不受影响。

### 语音

**显式触发**（私聊、群里都可用）：

| 类型 | 示例 |
|---|---|
| 多字前缀，可直接接内容 | `语音 你好呀`、`语音你好呀`、`朗读一下 晚安` |
| 单字前缀，需分隔符（空格/冒号/逗号） | `说 你好`、`念：晚安`、`读 这首诗` |

防误触发规则：

- 单字前缀无分隔符不触发：`说真的`、`说明一下`、`读书时间到了` 都当作普通聊天
- **群里单字前缀必须 @机器人 或带触发词**，避免群友日常闲聊让机器人开口
- 多字前缀（语音/朗读/）群里无需 @

**概率语音**：普通 AI 回复按 `VOICE_REPLY_PROBABILITY`（当前 25%）改为语音，覆盖群 @ 回复、群插话、私聊对话三条路径；单条超过 `VOICE_MAX_CHARS` 或 10 秒内刚发过语音则自动回退文字。日志关键字 `[概率语音]`。

**管理指令**：`/voice <文字>` 立即合成一条语音（管理员私聊）。

### 管理指令

| 指令 | 功能 |
|---|---|
| `/status` | 运行状态与当前白名单 |
| `/help` | 指令与语音触发说明 |
| `/clear <群号>` | 清空指定群上下文 |
| `/summary` | 手动触发一次今日总结 |
| `/emoji` | 查看表情库状态 |
| `/voice <文字>` | 立即发送一条语音 |
| `/allow <群号>` | 把群加入白名单（`/allow list` 查看） |
| `/deny <群号>` | 把群移出白名单 |
| `/tag`、`/fetch_emoji` | 兼容旧版，默认无实际作用 |

### 每日总结

每天 21:00 自动执行：只总结白名单内的活跃群，消息少于 5 条跳过，最多参考最近 50 条。总结为**纯文字**——提示词禁止表情标记，发送前还会用正则剔除残留的 `[emoji]` / `[表情]` / `[图片]`。

---

## 语音系统

### 后端选择

| 模型 | 协议 | 说明 |
|---|---|---|
| `cosyvoice-*`（推荐） | WebSocket | 复刻音色免费创建，稳定、便宜 |
| `qwen-audio-*` | WebSocket | 千问语音模型 |
| `MiniMax/speech-*` | HTTP | 千问平台转发 MiniMax；实测偶发不稳定，不建议长期依赖 |

后端由 `TTS_MODEL` 前缀自动选择，无需改代码。

### 爱弥斯音色复刻流程

1. **准备素材**：10 秒 ~ 5 分钟的清晰说话音频（MP3/WAV/M4A），无 BGM、无他人声音、不要唱歌
2. **上传公网**：任意公网可访问 URL（阿里云 OSS 最方便）
3. **创建音色**：

```bash
python create_voice.py --audio-url "https://你的URL.wav" --voice-id aemeath-voice
```

也支持 CosyVoice 免费复刻（`voice-enrollment` 接口），返回 `cosyvoice-v3.5-flash-<prefix>-<id>` 形式的 voice_id。

4. **写回配置**：把返回的 voice_id 填进 `TTS_VOICE`
5. **验证**：

```bash
python create_voice.py --synth "你好呀" --voice-id <voice_id>
```

服务器上用 `deploy/server_tts_check.py`。

### 费用与限制

- 语音合成按字符计费（千问平台约 ¥2/万字符）；CosyVoice 复刻音色创建免费
- MiniMax 复刻音色首次使用有 ¥9.9 解锁费——这也是推荐 CosyVoice 的原因
- 复刻音色存放在平台账号侧，换机器只需带上 `DASHSCOPE_API_KEY` 与 `TTS_VOICE`

### 语音排查

| 现象 | 处理 |
|---|---|
| 发 `/voice` 没反应 | 确认 `TTS_ENABLED=1`、`DASHSCOPE_API_KEY` 已设置、启动日志有 `语音合成已启用` |
| 合成失败 | 看日志 `CosyVoice 任务失败` / `TTS 错误`；确认能访问 `maas.qianwenaiapi.com` |
| 概率语音从不出现 | 检查 `VOICE_REPLY_PROBABILITY` 是否被 env 覆盖成 0；日志搜 `[概率语音]` |
| 声音像但不对味 | 换更干净的参考音频重新复刻；避免唱歌片段 |
| MiniMax 报 `product is not activated` | 该渠道需单独开通且不稳定，改用 CosyVoice |

---

## 表情系统

### 标记写法

```
[emoji]          → 全站随机一张
[emoji:角色名]    → 指定角色，如 [emoji:菲比]、[emoji:今汐]
```

角色匹配顺序：别名（完全等于优先，其次包含）→ 角色名精确 → 角色名包含 → 拼音匹配 → 形近字兜底 → 回退全站随机。

### 内置别名

| 别名 | 角色 |
|---|---|
| 耙耙柑 / 西西 | 西格莉卡 |
| 水蜜桃 / 娅娅 / 达尼亚 / 达尼娅 / 达妮雅 | 达妮娅 |
| 糯糯 | 弗洛洛 |
| 嘻嘻 / 夕夕 / 汐汐 / xixi | 今汐 |
| 青霄 | 清宵 |
| 小爱 / 小爱同学 | 爱弥斯 |

别名表在 `wuwa_emoji.py` 的 `NAME_ALIASES`。图片来源为远程仓库（约 1.5 万张、69 个角色），按需缓存到 `emojis/wuwa_cache/`（上限 200 张），发送时 `base64://` 内嵌，避免 NapCat 容器读不到宿主机路径。

> 表情素材来自第三方站点，属于游戏素材/同人作品，仅建议自用，不要二次分发。

---

## 项目结构

```
QQBOT/
├── bot.py                 # 主程序：连接、事件分发、私聊/群聊、语音触发、定时总结
├── config.py              # 人设、触发器、运行参数、语音配置（不入库）
├── config.example.py      # 配置模板（复制为 config.py）
├── ai_client.py           # AI 客户端（OpenAI 兼容，含 429 退避）
├── message_handler.py     # 群聊处理、表情解析、语音触发与概率语音
├── minimax_voice.py       # 语音模块：CosyVoiceTTS（WebSocket）/ TTS（MiniMax HTTP）
├── create_voice.py        # 声音复刻工具（创建音色 / 合成试听）
├── prep_voice_sample.py   # 本地音频分析剪辑（选段、拼接样本，需 ffmpeg）
├── emoji_manager.py       # 表情管理（远程模式）
├── wuwa_emoji.py          # 呜哇小站 API 客户端：下载、缓存、角色匹配
├── whitelist.py           # 运行时群白名单：/allow、/deny 增删并持久化
├── conversation.py        # 上下文与每日消息记录
├── summary_scheduler.py   # 旧版调度器（逻辑已内联到 bot.py，未引用）
├── requirements.txt       # aiohttp、pypinyin
├── deploy/                # 部署脚本、运维文档与自检工具
│   ├── deploy.ps1         # 一键部署（Windows）
│   ├── install.sh         # 服务器端安装
│   ├── qqbot.service      # systemd 单元
│   ├── napcat/compose.yaml
│   ├── server_tts_check.py# 服务器语音链路自检
│   ├── probe_login.py     # 登录状态自检
│   └── verify_emojis.py   # 表情库自检
├── tools/                 # 文档提取辅助脚本
├── astrbot/               # AstrBot 重构实验：插件骨架与 compose（见 astrbot/README.md）
├── 人格提示词/            # 人设原始文档
├── 声音素材/              # 复刻素材（不入库、不部署）
└── README.md
```

---

## 部署

### 本地运行

```bash
pip install -r requirements.txt
python bot.py
```

前提：NapCat 已在本机运行并登录（正向 WebSocket 3001），环境变量已设置。

### 服务器部署

阿里云 ECS + Docker + systemd 的完整方案见 **[deploy/README.md](deploy/README.md)**，包含一键脚本、扫码登录、语音配置与故障排查。

### AstrBot 重构（实验）

基于 AstrBot 的插件化重构方案在 **[astrbot/README.md](astrbot/README.md)**：包含 Docker 编排、4 个插件骨架（白名单/语音/表情/总结）与验收清单。与现有部署互不影响，可并行验证。

### 交给 AI 部署

把下面这段发给能执行命令的 AI：

```text
请部署 QQ 机器人 https://github.com/Ritarosess/qqbot

1. 确认 Python 3.10+，克隆后 pip install -r requirements.txt
2. 安装并启动 NapCatQQ，登录 QQ，开启正向 WebSocket（端口 3001）
3. 设置环境变量 AI_API_KEY、ADMIN_ID；可选 GROUP_WHITELIST、GROUP_CHAT_*
4. （可选语音）设置 TTS_ENABLED=1、DASHSCOPE_API_KEY、TTS_MODEL、TTS_VOICE
5. 运行 python bot.py，出现「已连接」「机器人登录号」即成功
6. 群里 @机器人 验证；私聊 /voice 你好呀 验证语音

限制：密钥不要写进代码；NapCat 必须保持运行；语音依赖千问平台网络可达。
```

---

## 故障排查

| 现象 | 排查方向 |
|---|---|
| @机器人没反应 | `journalctl -u qqbot -f` 是否重连 → Docker 容器是否运行 → NapCat 是否登录 |
| 消息发送失败 | 一个消息段失败会导致整条消息丢弃；本项目已用 base64 内嵌，查 NapCat 日志 `文件处理失败` |
| AI 报 401 / 429 | 401 是密钥错误；429 是限流，内置 30 秒退避 |
| 角色表情错图 | 看日志 `按角色取表情`；可补充 `NAME_ALIASES` |
| 语音问题 | 见上文「语音排查」 |

---

## 安全提示

- **不要提交密钥**：`config.py`、`.env`、`deploy/keys/` 均已在 `.gitignore`
- **不要外发私钥**：`deploy/keys/aliyun_ed25519` 仅本机使用
- 服务器安全组不要放行 `6099`（WebUI）与 `3001`（OneBot）
- 复刻音色仅限个人娱乐，不要公开分发或商用

---

## 许可证

MIT License

## 致谢

- [NapCatQQ](https://github.com/NapNeko/NapCatQQ) — QQ 协议端
- [DeepSeek](https://platform.deepseek.com) — AI 大模型
- [千问 AI 平台](https://platform.qianwenai.com) — CosyVoice / MiniMax 语音合成
- [呜哇小站 · 表情包仓鼠库](https://emoji.wuwa.games) — 远程表情来源
