# AstrBot 架构（线上）

爱弥斯机器人当前跑在 **AstrBot + NapCat** 上：协议层、会话层、调度层由 AstrBot 提供，业务能力拆成插件。早期自研 `bot.py` 保留用于回滚（见 [../deploy/README.md](../deploy/README.md)）。

> 两者不能同时挂同一个 QQ。

---

## 目录

```
astrbot/
├── docker-compose.yml                  # 本地/服务器编排参考
├── README.md
├── plugins/                            # 挂载到 /AstrBot/data/plugins
│   ├── astrbot_plugin_aemeath_persona/ # 人设注入
│   ├── astrbot_plugin_aemeath_voice/   # 语音（CosyVoice）
│   ├── astrbot_plugin_aemeath_emoji/   # 表情
│   ├── astrbot_plugin_aemeath_access/  # 群白名单
│   ├── astrbot_plugin_aemeath_summary/ # 每日总结
│   └── astrbot_plugin_aemeath_card/    # 卡片消息解析
└── tests/
    ├── test_plugins.py                 # 桩测试（不依赖 AstrBot）
    └── test_tts_live.py                # 真实 CosyVoice 合成
```

每个插件 = `main.py` + `metadata.yaml` + `_conf_schema.json`，配置项会出现在 WebUI 插件页。

---

## 线上结构

| 组件 | 说明 |
|---|---|
| AstrBot | Docker 容器 `astrbot`，4.29.1，WebUI `127.0.0.1:6185` |
| NapCat | Docker 容器 `napcat`，OneBot v11，WebUI `127.0.0.1:6099` |
| 共享网络 | `botnet`，NapCat 通过反向 WS 连 `ws://astrbot:6199/ws` |
| 数据目录 | 宿主机 `/opt/astrbot/data`（配置、插件 KV、日志） |
| 插件目录 | 宿主机 `/opt/astrbot/plugins` |
| 时区 | 容器 `TZ=Asia/Shanghai`（生活状态/总结按北京时间） |

容器启动命令（服务器当前用法）：

```bash
docker network create botnet 2>/dev/null || true
docker network connect botnet napcat 2>/dev/null || true

docker run -d --name astrbot --restart unless-stopped \
  --network botnet -e TZ=Asia/Shanghai \
  -p 127.0.0.1:6185:6185 \
  -v /opt/astrbot/data:/AstrBot/data \
  -v /opt/astrbot/plugins:/AstrBot/data/plugins \
  --memory 768m \
  --log-opt max-size=10m --log-opt max-file=3 \
  soulter/astrbot:latest
```

> Docker Hub 被墙时用镜像：`docker pull docker.m.daocloud.io/soulter/astrbot:latest`，再 `docker tag` 成 `soulter/astrbot:latest`。

---

## 首次配置（WebUI）

1. **模型提供商**：DeepSeek，`https://api.deepseek.com/v1` + Key
2. **消息平台**：OneBot v11，反向 WebSocket 主机 `0.0.0.0`、端口 `6199`
3. **NapCat**：WebUI → 网络配置 → 新建 **WebSockets 客户端**，URL `ws://astrbot:6199/ws`
4. **管理员**：AstrBot `admins_id` 填 QQ 号（`/allow`、`/dls regen` 等需要）
5. **插件**：在插件页配置各自参数（见下）

---

## 插件清单

### 自研

| 插件 | 关键点 |
|---|---|
| `aemeath_persona` | `on_llm_request` 覆写 system prompt；人设文本内置 |
| `aemeath_voice` | CosyVoice WebSocket 合成 wav；`/voice`；前缀触发；概率语音跳过表情/进度提示 |
| `aemeath_emoji` | 把 `[emoji]` / `[emoji:角色]` 换成图片；缓存为空时静默清理标记 |
| `aemeath_access` | 白名单存插件 KV；非白名单群 `stop_event()` |
| `aemeath_summary` | 群消息落盘 JSONL；21:00 生成纯文字总结并主动发送 |
| `aemeath_card` | priority=100，解析 JSON/XML 卡片为文本，修复空消息不回复 |
| `aemeath_group_chat` | 群聊插话（水群）；判定与旧版一致：概率 0.2、冷却 60s、参考 15 条上下文、AI 自判 `[skip]`；冷群间隔 30 分钟自动清空上下文；`/水群 on\|off\|prob` |
| `aemeath_poke` | 戳一戳机器人回俏皮话；10s 冷却防刷屏 |
| `aemeath_music` | 「演奏/来一首/点一首 xxx」搜网易云分享歌曲链接；含 LLM 工具 `share_netease_music` |

### 第三方（已装）

| 插件 | 说明 |
|---|---|
| `dynamic_life_state` | 每天生成生活时间线并注入当前时段；模板已定制为爱弥斯设定 |
| `gamepush` | 鸣潮版本/预下载推送；文本模式，群 753063398 |
| `listen_music`（bili_player） | B 站搜索点播；不支持 b23.tv 短链 |
| `deepseek_search` | DeepSeek 官方搜索/抓取；已填 Key |
| `kuro_checkin` | 库街区签到（待配置账号） |
| `majsoul_stats` | 雀魂战绩 |

---

## 关键配置

| 配置 | 值 | 原因 |
|---|---|---|
| `agent_runner.config.misc.tool_call_timeout` | `600` | B 站下载 + ffmpeg 转码常超 120s |
| `aemeath_voice.probability` | `0.25` | 概率语音 |
| `aemeath_voice.format` | `wav` | AstrBot `Record` 仅稳定支持 wav |
| `dynamic_life_state.session_list_mode` | `none` | 全会话注入；白名单+空列表=完全不生效 |
| `dynamic_life_state.prompt_template` | 爱弥斯定制 | 默认模板的 `{persona}` 取不到我们的人格 |
| `gamepush.ww.push_change_type` | `2` | 文本推送，避免 Chromium 与内存压力 |
| `deepseek_search.api_key` | 已填 | 其“复用 AstrBot 密钥”在当前版本有 bug |

---

## 测试

```bash
python astrbot/tests/test_plugins.py      # 27/27，桩环境
$env:DASHSCOPE_API_KEY="sk-..."
python astrbot/tests/test_tts_live.py     # 真实 wav 合成
```

桩测试覆盖：白名单增删/拦截、语音前缀与概率、表情替换、总结生成、卡片解析。

---

## 运维

```bash
docker restart astrbot          # 只重启 AstrBot，不影响 NapCat 登录
docker logs -f astrbot
docker logs --tail 50 napcat
```

隧道（本机）：

```powershell
.\deploy\tunnel-astrbot.ps1 -Server <IP>                 # 6185
ssh -i deploy\keys\aliyun_ed25519 -N -L 6099:127.0.0.1:6099 ecs-user@<IP>   # 6099
```

**注意**：

- 重启/重建 `napcat` 容器会要求重新扫码，登录后别随意重启
- 实例内存 1.7G 偏紧，AstrBot 限 768m；升级到 4G 更稳
- 修改插件代码后：`docker cp` 或重传文件 + `docker restart astrbot`

---

## 已知问题

| 问题 | 状态 |
|---|---|
| gamepush 图片卡片不可用 | Chromium 未安装，插件自动回退文本（预期行为） |
| b23.tv 短链无法点播 | bili_player 插件限制，需展开 BV 号 |
| 卡片消息不回复 | 已由 `aemeath_card` 修复 |
| deepseek_search 复用密钥失效 | 已改为直接填 `api_key` |
| 概率语音误读状态提示/表情 | 已加过滤，跳过 `[emoji]` 与“正在生成”类文本 |
| NapCat 重启需扫码 | QQ 机制，无法绕过；保持容器稳定 |

---

## 下一步

1. 库街区签到插件配置账号
2. 表情素材源接入（当前缓存为空，`[emoji]` 只会被清理）
3. 实例升配到 4G 后，可考虑开启 gamepush 图片卡片（安装 Chromium）
