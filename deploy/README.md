# 部署到阿里云 ECS

面向 Ubuntu 的完整部署与运维手册。部署脚本对 Debian/CentOS 系也做了兼容，但本文以 Ubuntu 为准。

## 部署布局

| 项 | 路径 / 值 |
|---|---|
| 项目目录 | `/opt/qqbot`（属主 `qqbot`） |
| Python 环境 | `/opt/qqbot/.venv`（仅依赖 aiohttp） |
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
                      │ HTTPS
                      ▼
               DeepSeek / OpenAI 兼容 API
```

6099 / 3001 **只绑定 127.0.0.1**，公网无法直连。WebUI 通过 SSH 隧道访问。

## 实例要求

| 项 | 建议 |
|---|---|
| 配置 | 2 核 2G 起步（NapCat + QQ 常驻约 600–900MB） |
| 系统盘 | 40G 以上（表情缓存约 200MB） |
| 镜像 | Ubuntu 22.04 / 24.04 / 26.04 |
| 带宽 | 1–3 Mbps 足够 |

> 内存小于 3GB 时，`install.sh` 会自动创建 2GB swap，防止 QQ + NapCat 被 OOM 杀掉。
>
> 如果实例当前是 Windows Server 镜像，必须先「更换操作系统」为 Ubuntu（系统盘会清空）：ECS 控制台 → 实例 → 更多 → 云盘和镜像 → 更换操作系统。Windows 上跑不了 Linux 版 NapCat。

## 部署前准备

### 1. 绑定 SSH 公钥

公钥在 `deploy/keys/aliyun_ed25519.pub.txt`：

```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHvGSeP3bQmSuqo48yzSagZ7juwK2xo/KcuMVcAtidDx qqbot-aliyun-deploy
```

绑定方式二选一：

- **实例已创建**：ECS 控制台 → 实例 → 更多 → 网络和安全组 → 绑定密钥对（切换密钥对通常需重启）
- **新建实例时**：登录凭证选「密钥对」，直接导入公钥

也可以在实例内手动追加：

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
echo 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHvGSeP3bQmSuqo48yzSagZ7juwK2xo/KcuMVcAtidDx qqbot-aliyun-deploy' >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

> 私钥是 `deploy/keys/aliyun_ed25519`（无口令），已加入 `.gitignore`，不要提交或外发。

### 2. 安全组

ECS 控制台 → 安全组 → 入方向，**只保留 22**。

不要放行 6099 / 3001：WebUI 有登录态，暴露公网等于交出 QQ 账号控制权。

### 3. 准备 API Key

到 https://platform.deepseek.com/ 创建 Key，部署后写入 `/etc/qqbot/qqbot.env`。

## 一键部署

在 Windows 本机项目根目录执行：

```powershell
# 首次部署（上传 + 安装）
.\deploy\deploy.ps1 -Server <ECS公网IP>

# 指定用户或端口
.\deploy\deploy.ps1 -Server <ECS公网IP> -User root -Port 2222

# 只上传，不执行安装
.\deploy\deploy.ps1 -Server <ECS公网IP> -SkipInstall
```

`deploy.ps1` 会依次完成：

1. 测试 SSH 连通性，非 root 用户会额外验证免密 `sudo -n`
2. 打包项目（排除 `.venv`、`.git`、私钥、缓存）
3. 上传到 `/tmp/qqbot-deploy.tar.gz`
4. 解压覆盖到 `/opt/qqbot`
5. 以 sudo 执行 `deploy/install.sh`

`install.sh` 负责：

- 识别发行版并安装 python3 / venv / curl / Docker
- 创建 `qqbot` 系统用户与虚拟环境，安装依赖（优先阿里云 PyPI 镜像）
- 小于 3GB 内存时创建 2GB swap
- 拉取 NapCat 镜像（Docker Hub 失败时自动回退 `docker.m.daocloud.io` → `docker.1ms.run` → `dockerproxy.net`）
- 启动或复用 `napcat` 容器（**已存在则只 start，绝不重建**，避免丢登录态）
- 安装并启用 `qqbot.service`

## 首次配置

### 1. 填写 API Key

```bash
ssh -i deploy/keys/aliyun_ed25519 ecs-user@<ECS公网IP>
sudo nano /etc/qqbot/qqbot.env
```

至少修改：

```ini
AI_API_KEY=sk-你的密钥
ADMIN_ID=你的QQ号
```

群聊插话的调参项（不写则用默认值）：

```ini
GROUP_CHAT=1                 # 0 = 只在被 @ 时说话
GROUP_CHAT_PROBABILITY=0.10  # 插话概率
GROUP_CHAT_COOLDOWN=120      # 同群最短间隔（秒）
GROUP_WHITELIST=             # 只在这些群发言，逗号分隔；留空 = 全部群
```

远程表情仓库（可选）：

```ini
WUWA_EMOJI_ENABLED=1         # 0 = 关闭表情功能
WUWA_CHARACTER=任意           # 任意 = 全站随机；也可填角色名
WUWA_TOKEN=                  # 站点 Token，可提高额度
```

### 2. 扫码登录 NapCat

本机另开一个窗口，保持不关：

```powershell
.\deploy\tunnel.ps1 -Server <ECS公网IP>
```

浏览器打开 http://127.0.0.1:6099/webui ，输入 WebUI Token：

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
QQ 收藏表情已禁用（使用远程表情库）
```

## 验证清单

```bash
# 服务
sudo systemctl status qqbot --no-pager

# 容器与端口（应只有 127.0.0.1）
sudo docker ps --filter name=napcat
sudo ss -lntp | grep -E '6099|3001'

# 登录状态
sudo /opt/qqbot/.venv/bin/python /opt/qqbot/deploy/probe_login.py

# 表情库自检（远程取图 + 角色解析）
sudo /opt/qqbot/.venv/bin/python /opt/qqbot/deploy/verify_emojis.py
```

到群里 @机器人，有回复即成功。

## 日常运维

```bash
sudo systemctl restart qqbot        # 重启机器人
sudo systemctl stop qqbot           # 停止
sudo systemctl status qqbot         # 状态
sudo journalctl -u qqbot -n 100     # 最近日志
sudo journalctl -u qqbot -f         # 实时日志

sudo docker logs -f napcat          # NapCat 日志
sudo docker restart napcat          # 重启 NapCat（可能要求重新扫码）
```

> 缺少 `docker compose` 时部署会自动降级为 `docker run`，功能一致。

## 更新代码

```powershell
.\deploy\deploy.ps1 -Server <ECS公网IP> -Restart
```

- 会覆盖服务器上的 `config.py`（人设、触发器），如改过服务器版本请先同步回本地
- 不会覆盖 `/etc/qqbot/qqbot.env`
- 不会重建 NapCat 容器，登录态保留

## 表情包

只使用远程表情库（[呜哇小站](https://emoji.wuwa.games)），本地素材包已移除。

- `[emoji]` → 全站随机
- `[emoji:角色名]` → 指定角色（如 菲比、今汐）

图片按需下载到 `/opt/qqbot/emojis/wuwa_cache/`（上限 200 张，单张超过 3MB 自动跳过），发送时以 base64 内嵌。

```ini
WUWA_EMOJI_ENABLED=1
WUWA_CHARACTER=任意
WUWA_TOKEN=...
```

> 第三方站点素材，仅建议自用；缓存目录不要提交到 Git。

自检：

```bash
sudo bash -c 'set -a; . /etc/qqbot/qqbot.env; set +a; EMOJI_DIR=/opt/qqbot/emojis /opt/qqbot/.venv/bin/python /opt/qqbot/deploy/verify_emojis.py'
```

## 故障排查

### @机器人没有反应

按链路排查：

```bash
sudo journalctl -u qqbot -f          # 有没有「[群xxx] QQ号: 内容」
sudo docker logs --since 5m napcat | grep '接收 <-'
sudo /opt/qqbot/.venv/bin/python /opt/qqbot/deploy/probe_login.py
```

- 日志里没有群消息 → NapCat 没收到，检查机器人是否在群里、是否被禁言
- 有触发日志但没有回复 → 看下一节（发送失败）
- 一直重连 3001 → NapCat 未登录或容器未运行

### 消息发不出去 / 表情发送失败

典型日志：

```
napcat: [error] 文件处理失败 识别URL失败, uri= /opt/qqbot/emojis/xxx.gif
```

NapCat 在容器内，读不到宿主机路径，且**一个消息段失败会导致整条消息不发送**。本项目已改为 `base64://` 内嵌发送，正常情况下不会再出现。

若仍复现，确认服务器上的 `message_handler.py` 是最新版本：

```bash
grep -n "base64://" /opt/qqbot/message_handler.py
```

### `docker pull` 卡住

大陆 ECS 访问 Docker Hub 常被阻断，`install.sh` 会自动尝试国内镜像站。全部失败时配置加速器：

```json
{ "registry-mirrors": ["https://<你的ID>.mirror.aliyuncs.com"] }
```

写入 `/etc/docker/daemon.json` 后 `sudo systemctl daemon-reload && sudo systemctl restart docker`，重跑部署。

### 扫码报 `serverErrorCode: 168`

```
你的账号近期存在安全风险，部分功能使用受限
```

这是 QQ 侧风控，不是部署问题。处理：用最新版手机 QQ 登录该号，按提示恢复；**期间不要反复扫码**，否则会加重限制。机房 IP 是主要诱因，可考虑香港/新加坡地域或改用活跃老号。

### WebUI 打不开

隧道窗口是否还开着？本机 6099 是否被占用？可换端口：

```powershell
.\deploy\tunnel.ps1 -Server <ECS公网IP> -LocalPort 16099
```

### `venv 创建失败`

Debian/Ubuntu 缺 `python3-venv`，RHEL 系缺 `python3-libs`。手动补装后重跑。

### AI 报 401 / 429

- 401：Key 错误或额度不足，检查 `/etc/qqbot/qqbot.env`
- 429：限流，代码已内置 30 秒退避

改完 `sudo systemctl restart qqbot`。

### 内存吃紧 / 被 OOM

```bash
free -h
sudo journalctl -k | grep -i oom
```

部署时已自动加 2GB swap；仍频繁 OOM 建议升到 4G。

## 安全须知

- 私钥 `deploy/keys/aliyun_ed25519` 不要提交、不要外发
- `/etc/qqbot/qqbot.env` 含 API Key，权限 640，勿打印到公开日志
- 安全组不要放行 6099 / 3001
- 建议给 SSH 加 fail2ban，并开启云安全中心基线检查
- 机器人建议用小号，避免主号被风控波及
