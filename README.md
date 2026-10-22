# 爱弥斯 QQ 机器人

基于 **AstrBot + NapCat** 的 QQ 机器人，人格是《鸣潮》的**爱弥斯（Aemeath）**。线上部署在阿里云 ECS，支持对话、语音、表情、群白名单、每日总结、动态生活状态、游戏版本推送、B 站点播与联网搜索。

> 本仓库同时保留早期自研 `bot.py` 版本（systemd 部署），仅用于回滚。日常开发与运维请看 [astrbot/README.md](astrbot/README.md)。

---

## 两条线

| 线 | 状态 | 入口 |
|---|---|---|
| **AstrBot 架构**（AstrBot + 插件） | ✅ 线上运行 | [astrbot/README.md](astrbot/README.md) |
| 早期自研 `bot.py` | 🗄️ 回滚备用 | [deploy/README.md](deploy/README.md) |

---

## 架构

```
手机 QQ ──扫码──> NapCat（OneBot v11，Docker）
                      │ 反向 WS：ws://astrbot:6199/ws
                      ▼
               AstrBot（Docker，:6185 WebUI）
                      │
      ┌───────────────┼────────────────┐
      ▼               ▼                ▼
 DeepSeek API    千问 CosyVoice    业务插件
 （对话/搜索）    （爱弥斯复刻音色）  （表情/白名单/总结/卡片…）
```

---

## 功能

| 功能 | 说明 |
|---|---|
| 群聊/私聊对话 | @机器人或触发词唤醒，DeepSeek 驱动，爱弥斯人设 |
| 群聊插话 | 群友闲聊时按概率（默认 15%）自然接话，带冷却与 AI 自判 |
| 语音合成 | CosyVoice + 爱弥斯复刻音色，`/voice`、前缀触发、25% 概率语音 |
| 表情包 | `[emoji]` / `[emoji:角色]` 标记替换为图片 |
| 群白名单 | `/allow`、`/deny` 动态管理，非白名单群静默 |
| 每日总结 | 每天 21:00 生成纯文字群聊总结 |
| 动态生活状态 | 每天生成爱弥斯的生活时间线，对话时注入当前时段 |
| 游戏版本推送 | 鸣潮版本/预下载变更推送到群（文本模式） |
| B 站点播 | 搜索并发送视频/音频，支持语音与文件 |
| 联网搜索 | DeepSeek 官方 Search/Fetch，模型可主动检索 |
| 卡片解析 | B 站分享卡片转文本，避免空消息不回复 |

---

## 快速开始（AstrBot）

服务器目录：

```
/opt/astrbot/
├── data/          # AstrBot 数据（配置、插件 KV、日志）
├── plugins/       # 挂载到容器 /AstrBot/data/plugins
└── docker-compose.yml（可选，当前用 docker run）
```

启动容器：

```bash
docker network create botnet 2>/dev/null || true
docker network connect botnet napcat 2>/dev/null || true

docker run -d --name astrbot --restart unless-stopped \
  --network botnet \
  -e TZ=Asia/Shanghai \
  -p 127.0.0.1:6185:6185 \
  -v /opt/astrbot/data:/AstrBot/data \
  -v /opt/astrbot/plugins:/AstrBot/data/plugins \
  --memory 768m \
  --log-opt max-size=10m --log-opt max-file=3 \
  soulter/astrbot:latest
```

NapCat 侧：WebUI 新建 **WebSockets 客户端**，URL 填 `ws://astrbot:6199/ws`，保存后 AstrBot 日志出现 `aiocqhttp 适配器已连接` 即成功。

访问 WebUI：

```powershell
ssh -i deploy\keys\aliyun_ed25519 -N -L 6185:127.0.0.1:6185 ecs-user@<ECS公网IP>
# 浏览器 http://127.0.0.1:6185
```

---

## 插件

### 自研插件

| 插件 | 作用 |
|---|---|
| `astrbot_plugin_aemeath_persona` | 每轮注入爱弥斯人设 |
| `astrbot_plugin_aemeath_voice` | CosyVoice 语音、前缀触发、概率语音 |
| `astrbot_plugin_aemeath_emoji` | `[emoji]` 标记转图片 |
| `astrbot_plugin_aemeath_access` | 群白名单 `/allow` `/deny` `/status` |
| `astrbot_plugin_aemeath_group_chat` | 群聊概率插话（水群），`/水群 on|off|prob` |
| `astrbot_plugin_aemeath_summary` | 每日总结 |
| `astrbot_plugin_aemeath_card` | QQ 分享卡片解析（JSON/XML） |

### 已安装的第三方插件

| 插件 | 作用 | 当前配置 |
|---|---|---|
| `astrbot_plugin_dynamic_life_state` | 动态生活状态 | 全会话启用，自定义爱弥斯模板 |
| `astrbot_plugin_gamepush` | 游戏版本推送 | 鸣潮启用，文本模式，群 753063398 |
| `astrbot_plugin_listen_music` | B 站点播（已改名 bili_player） | 默认配置 |
| `astrbot_plugin_deepseek_search` | DeepSeek 官方搜索 | 已填 API Key |
| `astrbot_plugin_kuro_checkin` | 库街区签到 | 待配置账号 |
| `astrbot_plugin_majsoul_stats` | 雀魂战绩 | 默认配置 |

---

## 配置要点

| 项 | 位置 | 说明 |
|---|---|---|
| 工具超时 | `agent_runner.config.misc.tool_call_timeout` | 已从 120 调到 **600**，B 站下载/转码不会再超时 |
| 语音密钥 | 插件 `aemeath_voice` 的 `api_key` | 千问平台 Key |
| 语音音色 | `aemeath_voice.voice` | 爱弥斯复刻音色 ID |
| 概率语音 | `aemeath_voice.probability` | 默认 0.25 |
| 生活状态模板 | 插件 `dynamic_life_state.prompt_template` | 已写入爱弥斯设定（学院/歌手/拉海洛） |
| 版本推送 | `gamepush.ww.push_change_type` | `2` = 文本（无 Chromium，图片卡片不可用） |
| 搜索密钥 | 插件 `deepseek_search.api_key` | 已直接填写；其"复用 AstrBot 密钥"功能在当前版本有 bug |

---

## 管理指令

| 指令 | 作用 |
|---|---|
| `/allow <群号>`、`/deny <群号>`、`/allow list` | 群白名单 |
| `/status` | 运行状态与白名单 |
| `/voice <文字>` | 立即发送一条语音 |
| `/dls show`、`/dls full`、`/dls regen` | 查看/重生成生活状态（regen 管理员） |
| `/dsearch <关键词>`、`/dfetch <URL>` | 联网搜索 / 抓取网页 |
| `/summary` | 手动触发每日总结 |
| `/水群 on|off`、`/水群 prob 0.15` | 开关/调整群聊自动插话（管理员） |

---

## 运维

```bash
# 重启 AstrBot（不影响 NapCat 登录）
docker restart astrbot

# 看日志
docker logs -f astrbot

# NapCat 状态
docker logs --tail 50 napcat
```

- **隧道**：`deploy/tunnel-astrbot.ps1 -Server <IP>`（6185）；NapCat WebUI 用 `6099`
- **⚠️ NapCat 重启/重建会要求重新扫码**，登录后不要随意重启 napcat 容器
- **内存**：实例 1.7G 偏紧（QQ + NapCat + AstrBot），建议升到 4G；AstrBot 限制 768m
- **回滚**：`docker stop astrbot && systemctl enable --now qqbot`

---

## 开发与测试

```bash
# 插件逻辑（桩环境，不依赖 AstrBot/Docker）
python astrbot/tests/test_plugins.py        # 27/27

# 真实 CosyVoice 合成
$env:DASHSCOPE_API_KEY="sk-..."
python astrbot/tests/test_tts_live.py
```

插件结构：`main.py` + `metadata.yaml` + `_conf_schema.json`，放进 `astrbot/plugins/` 后重载即可。

---

## 故障排查

| 现象 | 排查 |
|---|---|
| 群里 @ 无回复 | NapCat 是否登录；`docker logs astrbot` 是否收到事件；群白名单是否拦截 |
| 分享卡片无回复 | 卡片是 JSON/XML 段，需 `aemeath_card` 插件解析（已装） |
| 语音/点歌超时 | 工具超时是否 600；看日志 `execution timeout` |
| 游戏推送不推 | 版本无变化；`push_groups` 是否填群；文本模式 |
| 服务器 SSH 卡死 | 内存耗尽；控制台重启实例，之后 NapCat 需重新扫码 |

---

## 安全提示

- 密钥只放服务器 `/etc/qqbot/qqbot.env` 或 AstrBot 配置，不进 Git
- 私钥 `deploy/keys/aliyun_ed25519` 不要外发
- 6185 / 6099 / 3001 只绑 127.0.0.1，不要放行公网
- 第三方插件能读取配置与密钥，只装可信来源

---

## 许可证

MIT License（自研插件与代码）；第三方插件遵循各自许可证（AstrBot 为 AGPL-3.0）。

## 致谢

- [AstrBot](https://github.com/AstrBotDevs/AstrBot) — 机器人框架
- [NapCatQQ](https://github.com/NapNeko/NapCatQQ) — QQ 协议端
- [DeepSeek](https://platform.deepseek.com) — 对话与搜索
- [千问 AI 平台](https://platform.qianwenai.com) — CosyVoice 语音
- [呜哇小站](https://emoji.wuwa.games) — 表情来源
