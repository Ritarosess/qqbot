#!/usr/bin/env bash
# ============================================================
# QQBOT 一键部署脚本
# 适用：阿里云 ECS — Ubuntu / Debian / Alibaba Cloud Linux 3 / CentOS 系
# 用法：以 root 执行  bash /opt/qqbot/deploy/install.sh
# ============================================================
set -Eeuo pipefail

APP_DIR="${QQBOT_DIR:-/opt/qqbot}"
ENV_DIR="/etc/qqbot"
ENV_FILE="${ENV_DIR}/qqbot.env"
SERVICE_NAME="qqbot"
RUN_USER="qqbot"
NAPCAT_DIR="/opt/napcat"
DEPLOY_DIR="${APP_DIR}/deploy"
IMAGE="mlikiowa/napcat-docker:latest"

log()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[警告]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[错误]\033[0m %s\n' "$*" >&2; exit 1; }

[[ ${EUID} -eq 0 ]] || die "请用 root 运行：sudo bash $0"
[[ -f "${APP_DIR}/bot.py" ]] || die "未找到 ${APP_DIR}/bot.py，请先用 deploy.ps1 上传项目"
[[ -f "${DEPLOY_DIR}/qqbot.service" ]] || die "缺少 ${DEPLOY_DIR}/qqbot.service，部署包不完整"

# ---------- 1. 识别发行版 ----------
# shellcheck disable=SC1091
. /etc/os-release
DISTRO="${ID:-unknown}"
PRETTY="${PRETTY_NAME:-$DISTRO}"
case "$DISTRO" in
  ubuntu|debian|linuxmint|pop) PKG="apt" ;;
  alinux|centos|rhel|rocky|almalinux|fedora|anolis|opencloudos) PKG="dnf" ;;
  *)
    if   command -v apt-get >/dev/null 2>&1; then PKG="apt"
    elif command -v dnf     >/dev/null 2>&1; then PKG="dnf"
    elif command -v yum     >/dev/null 2>&1; then PKG="yum"
    else die "无法识别包管理器（${DISTRO}）"
    fi ;;
esac
log "系统：${PRETTY} / 包管理器：${PKG} / 架构：$(uname -m)"

# ---------- 2. 基础依赖 ----------
log "安装基础依赖（python3 / venv / curl）..."
if [[ "$PKG" == "apt" ]]; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y || die "apt-get update 失败，请检查网络或换用阿里云内网源"
  apt-get install -y python3 python3-venv python3-pip curl ca-certificates tar || die "依赖安装失败"
else
  $PKG install -y python3 python3-pip curl ca-certificates tar || die "依赖安装失败"
fi
command -v python3 >/dev/null 2>&1 || die "python3 未安装成功"

# ---------- 3. Docker ----------
if ! command -v docker >/dev/null 2>&1; then
  log "安装 Docker..."
  if [[ "$PKG" == "apt" ]]; then
    apt-get install -y docker.io || die "Docker 安装失败，请手动安装后重试"
  else
    ok=0
    for pkg in docker docker-ce; do
      if $PKG install -y "$pkg"; then ok=1; break; fi
    done
    [[ $ok -eq 1 ]] || die "Docker 安装失败（已尝试 docker / docker-ce），请手动安装后重试"
  fi
fi
systemctl enable --now docker >/dev/null 2>&1 || warn "docker 服务未能启动，请检查 systemctl status docker"
command -v docker >/dev/null 2>&1 || die "docker 命令不可用"
docker info >/dev/null 2>&1 || die "Docker 守护进程未运行：请执行 systemctl start docker 后重试"
log "Docker 就绪：$(docker --version)"

# ---------- 4. 运行用户与目录 ----------
if ! id -u "$RUN_USER" >/dev/null 2>&1; then
  log "创建系统用户 ${RUN_USER}"
  useradd --system --user-group --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$RUN_USER" 2>/dev/null \
    || warn "useradd 失败，稍后将直接使用 root 属主"
fi
mkdir -p "${APP_DIR}/emojis" "$ENV_DIR"

# ---------- 4.5 小内存实例加 swap ----------
MEM_MB="$(awk '/MemTotal/{printf "%d", $2/1024}' /proc/meminfo)"
if [[ "${MEM_MB:-0}" -lt 3000 ]] && [[ "$(awk 'NR>1' /proc/swaps | wc -l)" -eq 0 ]]; then
  log "检测到内存 ${MEM_MB}MB，创建 2GB swap（防止 QQ+NapCat 被 OOM 杀掉）..."
  if fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none; then
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
    swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    log "swap 已启用"
  else
    warn "swap 创建失败（可忽略，但 2G 内存跑 QQ 可能偏紧）"
  fi
fi

# ---------- 5. Python 虚拟环境 ----------
log "创建虚拟环境并安装依赖..."
if [[ ! -x "${APP_DIR}/.venv/bin/python" ]]; then
  python3 -m venv "${APP_DIR}/.venv" || die "venv 创建失败：Debian/Ubuntu 请装 python3-venv，RHEL 系请装 python3-libs"
fi
PIP_MIRROR="https://mirrors.aliyun.com/pypi/simple/"
"${APP_DIR}/.venv/bin/pip" install --upgrade pip -q -i "$PIP_MIRROR" || warn "pip 升级失败，继续"
if ! "${APP_DIR}/.venv/bin/pip" install -i "$PIP_MIRROR" -r "${APP_DIR}/requirements.txt"; then
  warn "阿里云 PyPI 镜像不可用，回退默认源重试"
  "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt" || die "Python 依赖安装失败"
fi

# ---------- 6. 权限 ----------
if id -u "$RUN_USER" >/dev/null 2>&1; then
  chown -R "${RUN_USER}:${RUN_USER}" "$APP_DIR"
fi
chmod 750 "$APP_DIR"
chmod -R u+rwX,go+rX "${APP_DIR}/emojis" 2>/dev/null || true

# ---------- 7. 环境变量文件 ----------
if [[ ! -f "$ENV_FILE" ]]; then
  if getent group "$RUN_USER" >/dev/null 2>&1; then ENV_GROUP="$RUN_USER"; else ENV_GROUP="root"; fi
  install -m 640 -o root -g "$ENV_GROUP" "${DEPLOY_DIR}/qqbot.env.example" "$ENV_FILE"
  sed -i "s#^EMOJI_DIR=.*#EMOJI_DIR=${APP_DIR}/emojis#" "$ENV_FILE"
  warn "已生成 ${ENV_FILE}，其中 AI_API_KEY 为占位值，需要你填写"
fi

# ---------- 8. NapCat 容器 ----------
log "准备 NapCat（QQ 协议端）..."
mkdir -p "${NAPCAT_DIR}/data/qq" "${NAPCAT_DIR}/data/config" "${NAPCAT_DIR}/data/plugins"
if [[ ! -f "${NAPCAT_DIR}/compose.yaml" ]]; then
  install -m 644 "${DEPLOY_DIR}/napcat/compose.yaml" "${NAPCAT_DIR}/compose.yaml"
fi
if [[ ! -f "${NAPCAT_DIR}/.env" ]]; then
  if command -v openssl >/dev/null 2>&1; then
    TOKEN="$(openssl rand -hex 16)"
  else
    TOKEN="$(od -An -tx1 -N16 /dev/urandom | tr -d ' \n')"
  fi
  printf 'WEBUI_TOKEN=%s\n' "$TOKEN" > "${NAPCAT_DIR}/.env"
  chmod 600 "${NAPCAT_DIR}/.env"
fi
WEBUI_TOKEN="$(grep -E '^WEBUI_TOKEN=' "${NAPCAT_DIR}/.env" | cut -d= -f2-)"

log "准备 NapCat 镜像：${IMAGE}"
MIRRORS="${DOCKER_MIRRORS:-docker.m.daocloud.io docker.1ms.run dockerproxy.net}"
PULLED=0
if docker image inspect "$IMAGE" >/dev/null 2>&1; then
  log "本地已存在该镜像，跳过拉取"
  PULLED=1
elif docker pull "$IMAGE"; then
  PULLED=1
fi
if [[ $PULLED -eq 0 ]]; then
  warn "直连 Docker Hub 失败（大陆 ECS 常态），尝试国内镜像站..."
  for m in $MIRRORS; do
    log "尝试镜像源：${m}"
    if docker pull "${m}/mlikiowa/napcat-docker:latest"; then
      docker tag "${m}/mlikiowa/napcat-docker:latest" "$IMAGE"
      PULLED=1
      log "已从 ${m} 拉取并重命名为 ${IMAGE}"
      break
    fi
  done
fi
if [[ $PULLED -eq 0 ]]; then
  warn "所有镜像源均失败。可到阿里云「容器镜像服务 ACR → 镜像工具 → 镜像加速器」获取专属地址后重试："
  printf '%s\n' \
    "   sudo mkdir -p /etc/docker" \
    "   sudo tee /etc/docker/daemon.json <<'JSON'" \
    "   { \"registry-mirrors\": [\"https://<你的ID>.mirror.aliyuncs.com\"] }" \
    "   JSON" \
    "   sudo systemctl daemon-reload && sudo systemctl restart docker"
  die "NapCat 镜像不可用，部署中止"
fi

cd "$NAPCAT_DIR"
if docker inspect napcat >/dev/null 2>&1; then
  # 关键：容器已存在时绝不能重建，否则会丢失 QQ 登录态、需要重新扫码
  log "NapCat 容器已存在，仅确保其运行（不重建）"
  docker update --restart unless-stopped napcat >/dev/null 2>&1 || true
  docker start napcat >/dev/null 2>&1 || true
elif docker compose version >/dev/null 2>&1; then
  docker compose up -d
elif command -v docker-compose >/dev/null 2>&1; then
  docker-compose up -d
else
  warn "未找到 docker compose，改用 docker run 直接启动"
  docker rm -f napcat >/dev/null 2>&1 || true
  docker run -d --name napcat --restart unless-stopped \
    -e MODE=ws -e WEBUI_TOKEN="$WEBUI_TOKEN" \
    -p 127.0.0.1:6099:6099 -p 127.0.0.1:3001:3001 \
    -v "${NAPCAT_DIR}/data/qq:/app/.config/QQ" \
    -v "${NAPCAT_DIR}/data/config:/app/napcat/config" \
    -v "${NAPCAT_DIR}/data/plugins:/app/napcat/plugins" \
    "$IMAGE"
fi
sleep 2
docker ps --filter name=napcat --format '容器：{{.Names}}  状态：{{.Status}}' || true

# ---------- 9. systemd ----------
log "安装 systemd 服务..."
sed "s#/opt/qqbot#${APP_DIR}#g" "${DEPLOY_DIR}/qqbot.service" > "/etc/systemd/system/${SERVICE_NAME}.service"
chmod 644 "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null 2>&1 || warn "设置开机自启失败"

API_KEY="$(grep -E '^AI_API_KEY=' "$ENV_FILE" | cut -d= -f2- || true)"
if [[ -n "$API_KEY" && "$API_KEY" != *"替换"* && "$API_KEY" != "sk-" ]]; then
  systemctl restart "$SERVICE_NAME"
  log "已启动 ${SERVICE_NAME}"
else
  warn "AI_API_KEY 尚未填写，暂不启动机器人"
fi

# ---------- 10. 后续步骤 ----------
cat <<EOF

================= 部署完成 =================
项目目录    : ${APP_DIR}
配置（密钥）: ${ENV_FILE}
NapCat 数据 : ${NAPCAT_DIR}/data
WebUI Token : ${WEBUI_TOKEN}

接下来（按顺序）：
 1. 填写密钥：  sudo nano ${ENV_FILE}
 2. 在【你自己的电脑】上开 SSH 隧道（保持窗口不关）：
      ssh -i <私钥路径> -N -L 6099:127.0.0.1:6099 <用户名>@<ECS公网IP>
 3. 浏览器打开 http://127.0.0.1:6099/webui ，输入上面的 WebUI Token
 4. 在 WebUI 里用手机 QQ 扫码登录机器人账号
 5. 回服务器启动： sudo systemctl start ${SERVICE_NAME}
 6. 看日志：       sudo journalctl -u ${SERVICE_NAME} -f

安全提醒：6099 与 3001 只监听 127.0.0.1，切勿在阿里云安全组放行这两个端口。
============================================
EOF
