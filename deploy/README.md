# 部署到阿里云 ECS（旧版 bot.py，仅回滚用）

> ⚠️ **本文档描述的是旧版 systemd + `bot.py` 部署，已被 AstrBot 架构取代。**
> 当前线上运行的是 AstrBot + NapCat，见 [../astrbot/README.md](../astrbot/README.md)。
> 旧版代码在 [../legacy/](../legacy/)；`deploy.ps1` 会自动把 `legacy/` 内容同步到服务器 `/opt/qqbot/` 根目录，保持旧路径不变。
> 本文仅保留用于回滚与历史参考。

面向 Ubuntu 的服务器部署与运维手册。部署脚本对 Debian / CentOS 系也做了兼容，但本文以 Ubuntu 为准。

> 只想在本机跑起来？看根目录 [README](../README.md) 的「快速开始」。本文只讲服务器部署。

---

## 部署布局

| 项 | 路径 / 值 |
|---|---|
| 项目目录 | `/opt/qqbot`（属主 `qqbot`） |
| Python 环境 | `/opt/qqbot/.venv`（`aiohttp`、`pypinyin`） |
| 环境变量 | `/etc/qqbot/qqbot.env`（权限 640，含 API Key） |
| systemd 服务 | `qqbot.service` |
| NapCat 容器 | `napcat`，数据在 `/opt/napcat/data` |
| WebSocket | `127.0.0.1:3001`（仅回环） |
| WebUI | `127.0.0.1:6099`（仅回环，经 SSH 隧道访问） |
| SSH 用户 | 阿里云 Ubuntu 镜像默认 `ecs-user`，需免密 sudo |

## 架构

```
手机 QQ ──扫码──> NapCat 容器（OneBot v11）
                      │ ws://127.0.0.1:3001
                      ▼
               bot.py（systemd，用户 qqbot）
                 │                 │
        HTTPS ───┘                 └─── WSS
          ▼                                 ▼
 DeepSeek / OpenAI 兼容 API          千问 AI 平台
                                    （CosyVoice 语音合成）
```

`6099` / `3001` **只绑定 127.0.0.1**，公网无法直连；WebUI 通过 SSH 隧道访问。

## 实例要求

| 项 | 建议 |
|---|---|
| 配置 | 2 核 2G 起步（NapCat + QQ 常驻约 600–900MB） |
| 系统盘 | 40G 以上 |
| 镜像 | Ubuntu 22.04 / 24.04 / 26.04 |
| 带宽 | 1–3 Mbps 足够 |

> 内存小于 3GB 时 `install.sh` 会自动创建 2GB swap，防止 QQ + NapCat 被 OOM 杀掉。
>
> 如果实例是 Windows Server 镜像，必须先「更换操作系统」为 Ubuntu（会清空系统盘）。

---

## 部署前准备

### 1. 绑定 SSH 公钥

公钥在 `deploy/keys/aliyun_ed25519.pub.txt`：

```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHvGSeP3bQmSuqo48yzSagZ7juwK2xo/KcuMVcAtidDx qqbot-aliyun-deploy
```

绑定方式二选一：

- **实例已创建**：ECS 控制台 → 实例 → 更多 → 网络和安全组 → 绑定密钥对（切换密钥对通常需重启）
- **新建实例时**：登录凭证选「密钥对」，直接导入公钥

也可在实例内手动追加：

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
echo 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHvGSeP3bQmSuqo48yzSagZ7juwK2xo/KcuMVcAtidDx qqbot-aliyun-deploy' >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

> 私钥是 `deploy/keys/aliyun_ed25519`（无口令），已加入 `.gitignore`，不要提交或外发。

### 2. 安全组

ECS 控制台 → 安全组 → 入方向，**只保留 22**。

不要放行 `6099` / `3001`：WebUI 有登录态，暴露公网等于交出 QQ 账号控制权。

### 3. 准备密钥

| 用途 | 获取地址 | 写入位置 |
|---|---|---|
| AI 对话 | <https://platform.deepseek.com/> | `AI_API_KEY` |
| 语音合成（可选） | <https://platform.qianwenai.com> | `DASHSCOPE_API_KEY` |

---

## 一键部署

在 Windows 本机项目根目录执行：

```powershell
# 首次部署（上传 + 安装）
.\deploy\deploy.ps1 -Server <ECS公网IP>

# 指定用户或端口
.\deploy\deploy.ps1 -Server <ECS公网IP> -User root -Port 2222

# 只上传，不执行安装
.\deploy\deploy.ps1 -Server <ECS公网IP> -SkipInstall

# 上传安装并重启机器人
.\deploy\deploy.ps1 -Server <ECS公网IP> -Restart
```

`deploy.ps1` 会依次完成：

1. 测试 SSH 连通性；非 root 用户会额外验证免密 `sudo -n`
2. 打包项目（排除 `.venv`、`.git`、私钥、`声音素材/`、散落音频、缓存）
3. 上传到 `/tmp/qqbot-deploy.tar.gz`
4. 解压覆盖到 `/opt/qqbot`
5. 以 sudo 执行 `deploy/install.sh`

`install.sh` 负责：

- 识别发行版并安装 python3 / venv / curl / Docker
- 创建 `qqbot` 系统用户与虚拟环境，安装依赖（优先阿里云 PyPI 镜像）
- 小于 3GB 内存时创建 2GB swap
- 拉取 NapCat 镜像（Docker Hub 失败时自动回退国内镜像站）
- 启动或复用 `napcat` 容器（**已存在则只 start，绝不重建**，避免丢登录态）
- 安装并启用 `qqbot.service`

---

## 首次配置

### 1. 填写环境变量

```bash
ssh -i deploy/keys/aliyun_ed25519 ecs-user@<ECS公网IP>
sudo nano /etc/qqbot/qqbot.env
```

基础配置（模板见 `deploy/qqbot.env.example`）：

```ini
AI_API_KEY=sk-你的DeepSeek密钥
ADMIN_ID=你的QQ号
```

群聊行为（不写则用默认值）：

```ini
GROUP_CHAT=1                 # 0 = 只在被 @ 时说话
GROUP_CHAT_PROBABILITY=0.10  # 插话概率
GROUP_CHAT_COOLDOWN=120      # 同群最短间隔（秒）
GROUP_WHITELIST=             # 初始白名单，逗号分隔；留空 = 全部群
```

> 白名单支持**私聊动态管理**：`/allow <群号>` 加入、`/deny <群号>` 移出、`/allow list` 查看。
> 修改会写入项目目录下的 `whitelist.json`（部署不会覆盖该文件），重启后保持。

远程表情仓库（可选）：

```ini
WUWA_EMOJI_ENABLED=1
WUWA_CHARACTER=任意
WUWA_TOKEN=
```

语音合成（可选）：**部署不会覆盖该文件**，需要手动追加以下内容一次——

```ini
TTS_ENABLED=1
DASHSCOPE_API_KEY=sk-你的千问密钥
TTS_MODEL=cosyvoice-v3.5-flash
TTS_VOICE=cosyvoice-v3.5-flash-aemeath-59638a19c09243cabf1144ce1be23d28
VOICE_REPLY_PROBABILITY=0.25
VOICE_MAX_CHARS=120
VOICE_TRIGGER_PREFIXES=语音,说,念,读,朗读,用语音说,发语音,语音回复,读一下,念一下,念一遍,朗读一下,用声音
```

> 语音以 `base64://` 内嵌的 record 消息段发送，容器内 NapCat 无需读宿主机文件。
> 复刻音色存在平台账号侧，换机器只需带上 `DASHSCOPE_API_KEY` 与 `TTS_VOICE`。

改完重启生效：

```bash
sudo systemctl restart qqbot
```

### 2. 扫码登录 NapCat

本机另开一个窗口，保持不关：

```powershell
.\deploy\tunnel.ps1 -Server <ECS公网IP>
```

浏览器打开 <http://127.0.0.1:6099/webui>，输入 WebUI Token：

```bash
sudo grep WEBUI_TOKEN /opt/napcat/.env
```

用**手机 QQ** 扫码登录机器人账号。二维码约 2 分钟过期，过期后 WebUI 会自动刷新。

> 容器重建会丢失登录态并重新要求扫码，所以不要随意 `docker rm`。

### 3. 启动机器人

```bash
sudo systemctl enable --now qqbot
sudo journalctl -u qqbot -f
```

预期日志：

```
已连接
机器人登录号: <QQ号>
语音合成已启用，模型: cosyvoice-v3.5-flash, 音色: ...
QQ 收藏表情已禁用（使用远程表情库）
```

---

## 验证清单

```bash
# 服务
sudo systemctl status qqbot --no-pager

# 容器与端口（应只有 127.0.0.1）
sudo docker ps --filter name=napcat
sudo ss -lntp | grep -E '6099|3001'

# 登录状态
sudo /opt/qqbot/.venv/bin/python /opt/qqbot/deploy/probe_login.py

# 语音链路自检（读取 env 后真实合成一条）
sudo -n bash -c 'set -a; . /etc/qqbot/qqbot.env; set +a; /opt/qqbot/.venv/bin/python /opt/qqbot/deploy/server_tts_check.py'
```

到群里 @机器人有回复即成功；私聊发 `/voice 你好呀` 应收到语音。

---

## 日常运维

```bash
sudo systemctl restart qqbot     # 重启机器人
sudo systemctl stop qqbot        # 停止
sudo systemctl status qqbot      # 状态
sudo journalctl -u qqbot -n 100  # 最近日志
sudo journalctl -u qqbot -f      # 实时日志

sudo docker logs -f napcat       # NapCat 日志
sudo docker restart napcat       # 重启 NapCat（可能要求重新扫码）
```

> 缺少 `docker compose` 时部署会自动降级为 `docker run`，功能一致。

---

## 更新代码

```powershell
.\deploy\deploy.ps1 -Server <ECS公网IP> -Restart
```

- 会覆盖服务器上的 `config.py`（人设、触发器），如改过服务器版本请先同步回本地
- **不会覆盖** `/etc/qqbot/qqbot.env`
- 不会重建 NapCat 容器，登录态保留

---

## 语音功能运维

### 切换音色

改 `/etc/qqbot/qqbot.env` 的 `TTS_VOICE` 后重启服务。系统音色可直接用平台音色名（如 `longanyang`），复刻音色用创建时返回的 ID。

### 重新复刻音色

1. 本机准备干净样本（10 秒 ~ 5 分钟、无 BGM / 他人声音 / 唱歌）
2. 上传任意公网 URL
3. 执行：

```bash
python create_voice.py --audio-url "https://你的URL.wav" --voice-id aemeath-voice --text "试听文本"
```

4. 把返回的 `voice_id` 写进 `TTS_VOICE`

> MiniMax 复刻首次使用有 ¥9.9 解锁费，且该渠道实测不稳定；推荐走 CosyVoice（`voice-enrollment`，免费）。

### 调概率语音

```ini
VOICE_REPLY_PROBABILITY=0.25   # 0 = 关闭；建议 0.05 ~ 0.3
VOICE_MAX_CHARS=120            # 超长回退文字，控制费用
TTS_COOLDOWN=10                # 语音冷却（秒）
```

日志搜 `[概率语音]` 可确认是否命中；`[群xxx] 语音请求` 为显式触发。

---

## 故障排查

### @机器人没有反应

```bash
sudo journalctl -u qqbot -f          # 有没有「[群xxx] QQ号: 内容」
sudo docker logs --since 5m napcat | grep '接收 <-'
sudo /opt/qqbot/.venv/bin/python /opt/qqbot/deploy/probe_login.py
```

- 日志没有群消息 → NapCat 没收到：检查机器人是否在群里、是否被禁言
- 有触发日志但没回复 → 看「消息发不出去」
- 一直重连 3001 → NapCat 未登录或容器未运行

### 消息发不出去 / 表情发送失败

典型日志：

```
napcat: [error] 文件处理失败 识别URL失败, uri= /opt/qqbot/emojis/xxx.gif
```

NapCat 在容器内读不到宿主机路径，且**一个消息段失败会导致整条消息不发送**。本项目已改为 `base64://` 内嵌，正常不会再出现。若复现，确认服务器代码最新：

```bash
grep -n "base64://" /opt/qqbot/message_handler.py
```

### 语音发不出 / 合成失败

```bash
sudo journalctl -u qqbot -n 50 | grep -E "语音|CosyVoice|TTS"
```

| 现象 | 处理 |
|---|---|
| 启动日志没有「语音合成已启用」 | `TTS_ENABLED=1`、`DASHSCOPE_API_KEY` 未写入 env，或改完没重启 |
| `CosyVoice 任务失败` / `TTS 错误` | 检查服务器能否访问 `maas.qianwenaiapi.com`；密钥是否有效 |
| 私聊 `/voice` 报「未启用」 | 同上；用 `server_tts_check.py` 直接验证 |
| 概率语音从不出现 | `VOICE_REPLY_PROBABILITY` 被设为 0；或 10 秒冷却内连续聊天 |
| 机器人说话但没声音 | 音色 ID 失效（复刻音色被删/权限问题），换系统音色测试 |
| MiniMax 报 `product is not activated` | 该渠道需单独开通且不稳定，改 `TTS_MODEL=cosyvoice-v3.5-flash` |

### `docker pull` 卡住

大陆 ECS 访问 Docker Hub 常被阻断，`install.sh` 会自动尝试国内镜像站。全部失败时配置加速器：

```json
{ "registry-mirrors": ["https://<你的ID>.mirror.aliyuncs.com"] }
```

写入 `/etc/docker/daemon.json` 后：

```bash
sudo systemctl daemon-reload && sudo systemctl restart docker
```

然后重跑部署。

### 扫码报 `serverErrorCode: 168`

```
你的账号近期存在安全风险，部分功能使用受限
```

这是 QQ 侧风控，不是部署问题。处理：用最新版手机 QQ 登录该号并按提示恢复；**期间不要反复扫码**，否则会加重限制。机房 IP 是主要诱因，可考虑香港/新加坡地域或改用活跃老号。

### WebUI 打不开

隧道窗口是否还开着？本机 6099 是否被占用？可换端口：

```powershell
.\deploy\tunnel.ps1 -Server <ECS公网IP> -LocalPort 16099
```

### `venv 创建失败`

Debian/Ubuntu 缺 `python3-venv`，RHEL 系缺 `python3-libs`。手动补装后重跑。

### AI 报 401 / 429

- 401：Key 错误或额度不足，检查 `/etc/qqbot/qqbot.env`
- 429：限流，代码内置 30 秒退避

改完 `sudo systemctl restart qqbot`。

### 内存吃紧 / 被 OOM

```bash
free -h
sudo journalctl -k | grep -i oom
```

部署时已自动加 2GB swap；仍频繁 OOM 建议升到 4G。

---

## 安全须知

- 私钥 `deploy/keys/aliyun_ed25519` 不要提交、不要外发
- `/etc/qqbot/qqbot.env` 含 API Key，权限 640，勿打印到公开日志
- 安全组不要放行 `6099` / `3001`
- 建议给 SSH 加 fail2ban，并开启云安全中心基线检查
- API Key 若曾在聊天/截图里暴露，请到平台轮换后更新 env
- 机器人建议用小号，避免主号被风控波及
- 复刻音色仅限个人娱乐，不要公开分发或商用
