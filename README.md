# 爱弥斯 QQ 机器人

基于 [AstrBot](https://github.com/AstrBotDevs/AstrBot) v4 + [NapCat](https://github.com/NapNeko/NapCatQQ)（OneBot v11）的 QQ 机器人。
功能全部以插件形式实现：人设、群聊插话、搬史、长期记忆、语音、表情、日记、Wiki 查询、点歌、掉线看门狗，
外加一个独立的 Web 设置面板。

> 仓库里**不含任何真实信息**：服务器地址、QQ 号、群号、令牌全部是占位符，运行时由环境变量或服务器本地配置提供。

---

## 功能

### 聊天与人格

| 插件 | 说明 |
|---|---|
| `astrbot_plugin_aemeath_persona` | 每轮注入角色人设（说话方式、口头禅、禁忌、被骂时的固定玩法） |
| `astrbot_plugin_aemeath_group_chat` | 群聊概率插话：概率、冷却、最短字数，支持热加载与 `/水群` 调整 |
| `astrbot_plugin_aemeath_memory` | 长期记忆：自动抽取人物/事件/梗，按清晰度阶梯衰减，对话时注入，支持历史回填 |
| `astrbot_plugin_aemeath_emoji` | 把回复里的 `[emoji]` / `[emoji:角色]` 换成表情图片 |
| `astrbot_plugin_aemeath_voice` | 语音合成（CosyVoice）：`/voice`、前缀触发、概率语音 |

### 群内容处理

| 插件 | 说明 |
|---|---|
| `astrbot_plugin_aemeath_chishi` | 搬史仙人：读取合并转发/卡片/截图，按人设评级评语，自动或手动搬到指定群 |
| `astrbot_plugin_aemeath_diary` | 每日群聊记录 + 日记（合并所有群写一篇，只私聊管理员） |
| `astrbot_plugin_aemeath_summary` | 群聊总结（默认每天定时） |
| `astrbot_plugin_aemeath_card` | 分享卡片解析 |
| `astrbot_plugin_aemeath_groupname` | 群名片/群名相关小工具 |

### 查询与娱乐

| 插件 | 说明 |
|---|---|
| `astrbot_plugin_aemeath_kurobbs` | 库街区 Wiki：突破材料、攻略、配队、全站搜索，并注册为 LLM 工具 |
| `astrbot_plugin_aemeath_wiki` | 鸣潮 Wiki（B 站数据源） |
| `astrbot_plugin_aemeath_music` | 点歌 |
| `astrbot_plugin_aemeath_poke` | 戳一戳 |
| `astrbot_plugin_aemeath_help` | `/帮助`：列出全部指令 |

### 运维

| 组件 | 说明 |
|---|---|
| `astrbot_plugin_aemeath_access` | 群白名单：`/allow` `/deny` `/status`；配置面板可直接编辑，改动即时生效 |
| `astrbot_plugin_aemeath_watchdog` | 掉线通知：主机侧看门狗写状态，插件私聊管理员 |
| `deploy/watchdog.py` | 主机侧看门狗（systemd timer）：检查 QQ 与容器，掉线先快捷登录，失败重启 NapCat |
| `dashboard/` | 独立设置面板（Python 标准库前后端）：插件配置、活跃度、长期记忆、状态与日志 |

---

## 架构

```
QQ ──► NapCat（Docker，OneBot v11） ──► AstrBot（Docker） ──► 插件
                                            │
                                            └──► LLM Provider（OpenAI 兼容）

宿主机
 ├─ deploy/watchdog.py     systemd timer，每 2 分钟
 ├─ dashboard/server.py    127.0.0.1:8610，token 鉴权
 └─ deploy/*.ps1           部署与 SSH 隧道脚本
```

---

## 目录结构

```
astrbot/
  plugins/                 # 自研插件（每个插件一个目录）
  tests/                   # 插件桩测试，不依赖网络
dashboard/                 # 设置面板（后端 + 单页前端 + 测试）
deploy/
  deploy.ps1               # 打包上传、重启、冒烟
  watchdog.py              # 掉线看门狗
  qqbot-watchdog.service   # systemd unit / timer
  qqbot-dashboard.service
  tunnel-*.ps1             # 本地 SSH 隧道
  打开面板.bat / 打开AstrBot.bat
docs/                      # 使用与排障文档
legacy/                    # 早期单文件机器人（仅存档）
assets/                    # 人设提示词等素材
```

---

## 快速开始

### 1. 服务器准备

- Linux（Debian/Ubuntu 均可），Docker + docker compose
- 2 核 2G 起步；容器内存限制见 `astrbot/docker-compose.yml`
- 装好 NapCat 容器并扫码登录 QQ

### 2. 环境变量

部署脚本与看门狗都从环境变量读取敏感信息：

| 变量 | 用途 |
|---|---|
| `QQBOT_SERVER` | 服务器地址（IP 或内网域名），部署/隧道脚本使用 |
| `WATCHDOG_UIN` | 机器人 QQ 号，看门狗快捷登录使用 |
| `NAPCAT_TOKEN` | NapCat WebUI token |
| `QQBOT_USER` | SSH 用户名，默认 `ecs-user` |

本地可以放 `deploy/local.bat`（已在 `.gitignore`）集中设置，双击的 `.bat` 会自动读取：

```bat
set QQBOT_SERVER=<你的服务器地址>
```

模板见 `deploy/local.bat.example`。

### 3. 部署

```powershell
$env:QQBOT_SERVER = "<你的服务器地址>"
.\deploy\deploy.ps1 -Server $env:QQBOT_SERVER
```

脚本会：预检 → 打包（排除缓存与语音素材）→ 上传 → 解包 → 重启容器 → 打印日志。

### 4. 设置面板

```powershell
.\deploy\打开面板.bat      # 建立 SSH 隧道并打开浏览器
```

面板只监听 `127.0.0.1:8610`，首次访问需要 token（服务器 `/opt/qqbot-dashboard/token`）。

---

## 常用指令

| 指令 | 说明 |
|---|---|
| `/帮助` | 全部指令 |
| `/summary`、`/日记`、`/写日记` | 总结与日记（管理员） |
| `/水群 on\|off\|prob 0.2` | 群聊插话开关与概率 |
| `/allow`、`/deny`、`/status` | 群白名单（管理员） |
| `/记忆`、`/记住`、`/忘记`、`/记忆回填` | 长期记忆 |
| `/材料`、`/攻略`、`/配队`、`/kuro` | 库街区 Wiki |
| `/voice` | 语音合成 |
| `/掉线状态` | 看门狗状态（管理员） |

完整列表见 [docs/指令说明.md](docs/指令说明.md)。

---

## 测试

```bash
python astrbot/tests/test_plugins.py    # 插件桩测试
python dashboard/tests/test_server.py   # 面板后端测试
```

两个套件都不访问网络，用假的 Context / Provider / 消息事件。

---

## 文档

- [指令说明](docs/指令说明.md)
- [吃史仙人说明](docs/吃史仙人说明.md)
- [记忆说明](docs/记忆说明.md)
- [设置面板说明](docs/设置面板说明.md)

---

## 隐私

- 仓库只包含占位符：`<SERVER_IP>`、`<ADMIN_QQ>`、`<BOT_QQ>`、`<NAPCAT_TOKEN>`、假群号 `90000000x`
- 真实配置只存在服务器（`/opt/astrbot/data/config/`）与本地 `deploy/local.bat`，两者都在 `.gitignore` 里
- 记忆插件入库前会脱敏手机号、身份证、卡号、token/密码

## 许可

MIT
