# AstrBot 重构（框架版）

用 [AstrBot](https://astrbot.app) 替换现有自研机器人。当前是**可运行的插件骨架**：协议层、会话层、调度层由 AstrBot 提供，业务逻辑拆成 4 个插件。

> 现有 `bot.py` 那套仍可独立运行，本目录是并行实验，两者不要同时挂同一个 QQ。

---

## 目录结构

```
astrbot/
├── docker-compose.yml              # AstrBot + NapCat
├── README.md
└── plugins/                        # 挂载到 /AstrBot/data/plugins
    ├── astrbot_plugin_aemeath_access/    # 群白名单：/allow /deny /status
    ├── astrbot_plugin_aemeath_voice/     # 语音：/voice、前缀触发、概率语音
    ├── astrbot_plugin_aemeath_emoji/     # 表情：[emoji] 标记 → 图片
    └── astrbot_plugin_aemeath_summary/   # 每日总结：记录 + 21:00 发送
```

每个插件都是 `main.py + metadata.yaml + _conf_schema.json`，配置项会出现在 AstrBot WebUI 里。

---

## 启动

```bash
cd astrbot
docker compose up -d
```

| 地址 | 用途 |
|---|---|
| http://127.0.0.1:6185 | AstrBot WebUI（配置 Provider / 人格 / 插件） |
| http://127.0.0.1:6099/webui | NapCat WebUI（扫码登录机器人 QQ） |

首次流程：

1. 打开 AstrBot WebUI → 配置 **Provider**（DeepSeek，OpenAI 兼容）
2. **人格**：把原 `config.py` 里的 `SYSTEM_PROMPT` 粘贴进去
3. **消息平台 → aiocqhttp**：连接地址填 `ws://napcat:3001`（容器内网）
4. NapCat WebUI 扫码登录，确认已开启**正向 WebSocket 服务（3001）**
5. 群聊 @ 机器人，先确认基础对话正常，再逐个启用插件

---

## 插件一览与迁移对应

| 插件 | 替代原项目 | 实现状态 |
|---|---|---|
| `aemeath_access` | `whitelist.py` + `/allow`、`/deny` | ✅ 可直接用（KV 持久化、群消息拦截） |
| `aemeath_voice` | `minimax_voice.py` + 语音触发/概率 | ✅ 可跑通；概率钩子依赖 AstrBot 版本 |
| `aemeath_emoji` | `wuwa_emoji.py` + `emoji_manager.py` | 🟡 链路已通，素材源是本地缓存骨架 |
| `aemeath_summary` | `bot.py` 的 `_daily_summary` | ✅ 记录/定时/手动触发齐全 |

### 语音插件注意

- **AstrBot 的 `Comp.Record` 目前只稳定支持 wav**，所以默认让 CosyVoice 输出 wav
- 概率语音走 `on_decorating_result` 钩子修改最终消息链；若开启**流式输出**会来不及替换，建议对该会话关闭流式
- 显式触发（语音/朗读/说…）会先 `stop_event()`，避免和默认 LLM 回复打架
- 音色 ID 默认沿用爱弥斯复刻音色，可在 WebUI 里改

### 表情插件注意

`core/emoji_source.py` 现在是本地缓存扫描骨架。接入远程表情仓库时，把原项目的 `wuwa_emoji.py` / `emoji_manager.py` 逻辑搬进该文件，保持 `pick(keyword) -> Path | None` 接口不变，主插件无需改动。

### 总结插件注意

- 消息按天写入 `data/plugin_data/astrbot_plugin_aemeath_summary/daily/YYYY-MM-DD/<群号>.jsonl`
- 依赖 `apscheduler`（AstrBot 自带）；通过 `on_astrbot_loaded` 启动调度
- 发送用 `context.send_message(unified_msg_origin, chain)` 主动推送，umo 在收到群消息时记录
- 总结强制纯文字，发送前剔除 `[emoji]` / `[表情]` / `[图片]`

---

## 已知坑与待办

| 项 | 说明 |
|---|---|
| 钩子版本差异 | `on_decorating_result`、`on_astrbot_loaded` 需 AstrBot ≥ 4.x 对应版本；升级后先回归 |
| 内置群白名单 | AstrBot 自带白名单配置；若启用，把 access 插件 `enforce` 关掉避免双拦截 |
| 群聊插话 | 原 `_maybe_join_group_chat` 未移植。先评估 AstrBot [主动型能力](https://docs.astrbot.app/use/proactive-agent.html)，不够再写插件 |
| 许可证 | AstrBot 是 AGPL-3.0；分发本目录插件时需保持 AGPL 兼容 |
| 数据迁移 | 旧 `whitelist.json` 群号可用 `/allow` 重新录入；历史聊天记录不迁移 |

---

## 验收清单

### 已在本机验证（桩环境 + 真实语音）

```bash
# 插件逻辑（不依赖 AstrBot / Docker）
python astrbot/tests/test_plugins.py      # 22/22 通过

# 真实 CosyVoice wav 合成（需 DASHSCOPE_API_KEY）
$env:DASHSCOPE_API_KEY="sk-xxx"
python astrbot/tests/test_tts_live.py     # PASS: RIFF/WAVE
```

| 清单项 | 状态 | 验证方式 |
|---|---|---|
| `/allow` 生效、非白名单群拦截 | ✅ | 桩测试 |
| 语音前缀触发 + 停止事件（不重复回复） | ✅ | 桩测试 |
| 显式触发发送 `Record` | ✅ | 桩测试 |
| 概率语音替换、超长回退文字 | ✅ | 桩测试 |
| `[emoji]` 替换为图片、`[表情]` 清理 | ✅ | 桩测试 |
| 群消息落盘、总结生成并发送、剔除标记 | ✅ | 桩测试 |
| CosyVoice 输出 wav（Record 前提） | ✅ | 真实 API |
| @机器人 回复 / 人格生效 | ⏳ 待 Docker 环境 |
| 每日 21:00 定时触发 | ⏳ 待 Docker 环境 |

### 待 Docker 环境补测

- [ ] @机器人 正常回复，人格生效
- [ ] NapCat 扫码 + aiocqhttp 连接 `ws://napcat:3001`
- [ ] 每日 21:00 定时任务实际触发（可临时改 hour/minute）
- [ ] 语音在真实 QQ 里可播放

---

## 下一步

1. 安装 Docker Desktop（本机目前未安装），`docker compose up -d` 补测剩下三项
2. 把表情素材源接入远程仓库
3. 评估群聊插话：内置能力 or 新插件
4. 测试稳定后，在服务器用本目录替换现有 systemd 部署（保留旧版回滚）
