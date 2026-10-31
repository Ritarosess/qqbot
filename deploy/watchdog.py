#!/usr/bin/env python3
"""NapCat 掉线看门狗（主机侧）

每 2 分钟检查 NapCat 的 QQ 进程是否还在：
- 掉线 → 先调 NapCat WebUI 接口尝试快捷登录（会话还在时能自动恢复）
- 快捷登录失败 → 每 20 分钟最多重启一次 NapCat（生成新二维码）
- 掉线/恢复都写进 state.json，AstrBot 的 aemeath_watchdog 插件据此给管理员发通知
"""
import json
import os
import subprocess
import time
import urllib.request
from datetime import datetime

NAPCAT_TOKEN = os.environ.get("NAPCAT_TOKEN", "")
API = os.environ.get("NAPCAT_API", "http://127.0.0.1:6099")
UIN = os.environ.get("WATCHDOG_UIN", "")
STATE_DIR = "/opt/astrbot/data/plugin_data/astrbot_plugin_aemeath_watchdog"
STATE = os.path.join(STATE_DIR, "state.json")
LOG = "/var/log/qqbot-watchdog.log"
WEBHOOK = os.environ.get("WATCHDOG_WEBHOOK", "")
RESTART_GAP = 60 * 60  # 重新登录要扫码，重启只会刷新二维码，别刷太勤


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(d):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, STATE)


ONLINE_MARKS = ("接收 <-", "发送 ->", "登录成功", "账号已在线")
OFFLINE_MARKS = (
    "KickedOffLine",
    "Login Error",
    "账号当前登录已失效",
    "请扫描下面的二维码",
)


def qq_alive():
    """判断 NapCat 是否在线：**看最后一条关键日志**，而不是「最近 15 分钟里出现过」。

    返回 "online" / "offline" / "unknown"（unknown 时不做任何动作，避免误重启）
    """
    try:
        r = subprocess.run(
            ["docker", "logs", "--since", "30m", "napcat"],
            capture_output=True, text=True, timeout=40,
        )
        text = (r.stdout or "") + (r.stderr or "")
    except Exception as exc:  # noqa: BLE001
        log(f"读取日志失败: {exc}")
        return "unknown"
    lines = [ln for ln in text.splitlines() if ln.strip()][-800:]
    state = "unknown"
    for line in lines:
        if any(mark in line for mark in OFFLINE_MARKS):
            state = "offline"
        elif any(mark in line for mark in ONLINE_MARKS):
            state = "online"
    return state


def http_json(url, payload=None, headers=None):
    data = None
    hdrs = {"Content-Type": "application/json"}
    hdrs.update(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers=hdrs)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def quick_login():
    import hashlib
    try:
        h = hashlib.sha256((NAPCAT_TOKEN + ".napcat").encode()).hexdigest()
        r = http_json(f"{API}/api/auth/login", {"hash": h, "totpCode": ""})
        cred = (r.get("data") or {}).get("Credential")
        if not cred:
            return False, "auth failed"
        r2 = http_json(
            f"{API}/api/QQLogin/SetQuickLogin",
            {"uin": int(UIN)},
            {"Authorization": f"Bearer {cred}"},
        )
        msg = str(r2.get("message") or "")
        if r2.get("code") == 0:
            return True, "ok"
        # 已经在线时接口会返回 -1 + "QQ Is Logined"，这不算失败
        if "logined" in msg.lower() or "已登录" in msg:
            return True, "already"
        return False, msg
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def restart_napcat():
    try:
        subprocess.run(["docker", "restart", "napcat"], timeout=90, check=False)
        return True
    except Exception as exc:  # noqa: BLE001
        log(f"重启 NapCat 失败: {exc}")
        return False


def container_running(name: str) -> bool | None:
    """容器是否在运行；查不到返回 None"""
    try:
        r = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", name],
            capture_output=True, text=True, timeout=20, check=False,
        )
        out = (r.stdout or "").strip().lower()
        if out in ("true", "false"):
            return out == "true"
        return None
    except Exception:  # noqa: BLE001
        return None


def ensure_astrbot() -> None:
    """AstrBot 进程偶尔会因为模型接口连续失败而整个退出，这里兜底拉起"""
    running = container_running("astrbot")
    if running is False:
        log("检测到 AstrBot 未运行，正在拉起")
        try:
            subprocess.run(["docker", "start", "astrbot"], timeout=120, check=False)
            log("AstrBot 已执行 start")
        except Exception as exc:  # noqa: BLE001
            log(f"拉起 AstrBot 失败: {exc}")


def notify_webhook(text):
    if not WEBHOOK:
        return
    try:
        http_json(WEBHOOK, {"text": text})
    except Exception as exc:  # noqa: BLE001
        log(f"webhook 失败: {exc}")


def main():
    ensure_astrbot()
    now = time.time()
    st = load_state()
    state = qq_alive()

    if state == "online":
        off_since = st.get("offline_since")
        if off_since:
            secs = int(now - float(off_since))
            st["pending"] = {
                "since": off_since,
                "until": now,
                "seconds": secs,
                "reason": st.get("reason") or "",
            }
            st["offline_since"] = None
            st["reason"] = ""
            save_state(st)
            log(f"已恢复，掉线 {secs // 60} 分钟")
            notify_webhook(f"QQ 机器人已恢复，掉线 {secs // 60} 分钟")
        return

    if state == "unknown":
        return

    # 掉线
    if not st.get("offline_since"):
        st["offline_since"] = now
        st["reason"] = ""
        save_state(st)
        log("检测到掉线")
        notify_webhook("QQ 机器人掉线，正在尝试自动恢复")

    ok, why = quick_login()
    if ok:
        log("快捷登录成功，等待进程恢复")
        st["reason"] = "快捷登录恢复"
        save_state(st)
        return

    st["reason"] = f"需要扫码（{why[:40]}）"
    last = float(st.get("last_restart") or 0)
    if now - last > RESTART_GAP:
        log("快捷登录失败，重启 NapCat 生成新二维码")
        if restart_napcat():
            st["last_restart"] = now
            notify_webhook("QQ 机器人需要扫码登录，NapCat 已重启并生成新二维码")
    save_state(st)


if __name__ == "__main__":
    main()
