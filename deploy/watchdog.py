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
# ntfy 手机推送：填主题地址，例如 https://ntfy.sh/你的随机主题
NTFY_URL = os.environ.get("NTFY_URL", "")
# 假在线探测：用 send_like 戳一下这个 QQ（不产生聊天消息），留空则只做被动检查
PROBE_QQ = os.environ.get("WATCHDOG_PROBE_QQ", "")
RESTART_GAP = 6 * 60 * 60  # 扫码登录期间重启只会把二维码刷掉，6 小时一次就够


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


def astrbot_ws_alive():
    """AstrBot 是否还活着：6199 端口能连 + 6185 的 HTTP 能在 6 秒内回

    只看端口会误判：事件循环卡死时 socket 仍然 accept（内核 backlog），
    必须让它真的返回一个 HTTP 响应才算活着。
    """
    probe = (
        "import socket, urllib.request\n"
        "socket.create_connection(('127.0.0.1', 6199), 3).close()\n"
        "urllib.request.urlopen('http://127.0.0.1:6185/', timeout=6).read(64)\n"
        "print('ok')\n"
    )
    try:
        r = subprocess.run(
            ["docker", "exec", "astrbot", "python", "-c", probe],
            capture_output=True, text=True, timeout=30,
        )
        return r.returncode == 0 and "ok" in (r.stdout or "")
    except Exception:  # noqa: BLE001
        return None


STRIKE_FILE = "/var/lib/qqbot-watchdog/astrbot_strikes.json"


def _load_strikes() -> int:
    try:
        with open(STRIKE_FILE, encoding="utf-8") as f:
            return int(json.load(f).get("strikes") or 0)
    except Exception:  # noqa: BLE001
        return 0


def _save_strikes(n: int) -> None:
    try:
        os.makedirs(os.path.dirname(STRIKE_FILE), exist_ok=True)
        with open(STRIKE_FILE, "w", encoding="utf-8") as f:
            json.dump({"strikes": n}, f)
    except Exception:  # noqa: BLE001
        pass


def docker_restart(name: str) -> None:
    subprocess.run(["docker", "restart", name], timeout=180, check=False)


def ensure_astrbot() -> None:
    """AstrBot 兜底：容器没跑就 start；容器在跑但健康检查连续失败就 restart"""
    running = container_running("astrbot")
    if running is False:
        log("检测到 AstrBot 未运行，正在拉起")
        try:
            subprocess.run(["docker", "start", "astrbot"], timeout=120, check=False)
            log("AstrBot 已执行 start")
        except Exception as exc:  # noqa: BLE001
            log(f"拉起 AstrBot 失败: {exc}")
        return
    alive = astrbot_ws_alive() if running else None
    if running and alive is False:
        strikes = _load_strikes() + 1
        _save_strikes(strikes)
        if strikes < 2:
            log(f"AstrBot 健康检查失败（第 {strikes} 次），下次再失败就重启")
            return
        _save_strikes(0)
        log("AstrBot 在跑但健康检查连续失败，判定挂死，重启")
        try:
            subprocess.run(["docker", "restart", "astrbot"], timeout=180, check=False)
            log("AstrBot 已重启")
            _save_strikes(0)
            notify_ntfy("AstrBot 已自动重启", "检测到 AstrBot 进程挂死（OneBot 端口不通），已重启恢复",
                        "high", "wrench")
        except Exception as exc:  # noqa: BLE001
            log(f"重启 AstrBot 失败: {exc}")



def _napcat_tcp_count() -> int:
    """看容器里有没有到外网的 ESTABLISHED 连接；0 基本等于 QQ 层死了"""
    try:
        out = subprocess.run(
            ["docker", "exec", "napcat", "cat", "/proc/net/tcp"],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:  # noqa: BLE001
        return -1
    count = 0
    for line in (out.stdout or "").splitlines()[1:]:
        parts = line.split()
        if len(parts) < 4 or parts[3] != "01":  # 01 = ESTABLISHED
            continue
        try:
            port = int(parts[2].split(":")[1], 16)
        except (IndexError, ValueError):
            continue
        if port in (80, 443, 8080, 8000, 14000):
            count += 1
    return count


def _onebot_call(action: str, params: dict, timeout: float = 8.0):
    """最小 WebSocket 客户端：连 NapCat 3001 调一个 OneBot 接口"""
    import base64
    import socket
    import struct

    try:
        s = socket.create_connection(("127.0.0.1", 3001), timeout=timeout)
    except OSError:
        return None
    try:
        key = base64.b64encode(os.urandom(16)).decode()
        s.sendall(("GET / HTTP/1.1\r\nHost: 127.0.0.1:3001\r\nUpgrade: websocket\r\n"
                   "Connection: Upgrade\r\nSec-WebSocket-Key: " + key +
                   "\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = s.recv(4096)
            if not chunk:
                return None
            resp += chunk
        if "101" not in resp.split(b"\r\n", 1)[0].decode(errors="ignore"):
            return None
        payload = json.dumps({"action": action, "params": params, "echo": "wd"}).encode()
        mask = os.urandom(4)
        header = bytearray([0x81])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack("!H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", n)
        header += mask
        s.sendall(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))
        buf = resp.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in resp else b""
        while True:
            while len(buf) < 2:
                more = s.recv(4096)
                if not more:
                    return None
                buf += more
            length = buf[1] & 0x7F
            offset = 2
            if length == 126:
                while len(buf) < 4:
                    buf += s.recv(4096)
                length = struct.unpack("!H", buf[2:4])[0]
                offset = 4
            elif length == 127:
                while len(buf) < 10:
                    buf += s.recv(4096)
                length = struct.unpack("!Q", buf[2:10])[0]
                offset = 10
            while len(buf) < offset + length:
                buf += s.recv(4096)
            body = buf[offset:offset + length]
            buf = buf[offset + length:]
            try:
                data = json.loads(body.decode())
            except ValueError:
                continue
            if data.get("echo") == "wd":
                return data
    except OSError:
        return None
    finally:
        s.close()


def qq_link_alive(st: dict) -> bool | None:
    """QQ 链路是否真活着：TCP 至少有一条外网连接 + 每小时用点赞戳一次

    只看 get_status 会被骗：QQ 底层断了它照样返回 online。
    """
    tcp = _napcat_tcp_count()
    if tcp == 0:
        return False
    if not PROBE_QQ:
        return True if tcp > 0 else None
    now = time.time()
    last = float(st.get("last_like_probe") or 0)
    if now - last < 3600:
        return True
    res = _onebot_call("send_like", {"user_id": PROBE_QQ, "times": 1})
    st["last_like_probe"] = now
    save_state(st)
    if not res:
        return False
    if res.get("status") != "ok":
        return False
    return True


def notify_ntfy(title: str, text: str, priority: str = "default", tags: str = "") -> None:
    """手机推送：ntfy 主题地址由 NTFY_URL 提供；不填就跳过"""
    if not NTFY_URL:
        return
    headers = {
        "Title": title.encode("utf-8"),
        "Priority": priority,
    }
    if tags:
        headers["Tags"] = tags
    try:
        req = urllib.request.Request(
            NTFY_URL,
            data=text.encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception as exc:  # noqa: BLE001
        log(f"ntfy 推送失败: {exc}")


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
        # 假在线检查：日志看着在线，但 QQ 链路可能已经死了（get_status 会骗人）
        link = qq_link_alive(st)
        if link is False:
            strikes = int(st.get("fake_online_strikes") or 0) + 1
            st["fake_online_strikes"] = strikes
            save_state(st)
            log(f"疑似假在线（第 {strikes} 次）：QQ 链路探测失败")
            if strikes >= 2:
                st["fake_online_strikes"] = 0
                save_state(st)
                log("连续两次判定假在线，重启 NapCat")
                try:
                    docker_restart("napcat")
                except Exception as exc:  # noqa: BLE001
                    log(f"重启 NapCat 失败: {exc}")
                notify_ntfy("QQ 链路假在线", "NapCat 显示在线但实际发不出消息，已重启 NapCat（可能需要扫码）",
                            "urgent", "rotating_light")
                return
        elif link is True:
            if st.get("fake_online_strikes"):
                st["fake_online_strikes"] = 0
                save_state(st)

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
            notify_ntfy("QQ 机器人已恢复", f"掉线 {secs // 60} 分钟，现在已经在线", "default", "white_check_mark")
        return

    if state == "unknown":
        return

    # 掉线
    if not st.get("offline_since"):
        st["offline_since"] = now
        st["reason"] = ""
        st["last_push"] = now
        save_state(st)
        log("检测到掉线")
        notify_webhook("QQ 机器人掉线，正在尝试自动恢复")
        notify_ntfy("QQ 机器人掉线", "检测到掉线，正在尝试快捷登录恢复", "high", "warning")
    else:
        last_push = float(st.get("last_push") or 0)
        if now - last_push > 30 * 60:
            mins = int((now - float(st["offline_since"])) // 60)
            st["last_push"] = now
            save_state(st)
            notify_ntfy("仍然掉线", f"已经掉线 {mins} 分钟了，还是没恢复。打开设置面板的 /qr 页面扫码",
                        "urgent", "rotating_light")

    ok, why = quick_login()
    if ok:
        log("快捷登录成功，等待进程恢复")
        st["reason"] = "快捷登录恢复"
        save_state(st)
        return

    st["reason"] = f"需要扫码（{why[:40]}）"
    last_push = float(st.get("last_push") or 0)
    if now - last_push > 30 * 60:
        st["last_push"] = now
        save_state(st)
        notify_ntfy("需要扫码登录",
                    "登录态失效，快捷登录救不回来。手机打开设置面板的 /qr 页面扫码（需挂 Tailscale）",
                    "urgent", "rotating_light")
    last = float(st.get("last_restart") or 0)
    if now - last > RESTART_GAP:
        log("快捷登录失败，重启 NapCat 生成新二维码")
        if restart_napcat():
            st["last_restart"] = now
            notify_webhook("QQ 机器人需要扫码登录，NapCat 已重启并生成新二维码")
            notify_ntfy("需要扫码登录",
                        "登录态失效，快捷登录救不回来。手机打开设置面板的 /qr 页面扫码（需挂 Tailscale）",
                        "urgent", "rotating_light")
    save_state(st)


if __name__ == "__main__":
    main()
