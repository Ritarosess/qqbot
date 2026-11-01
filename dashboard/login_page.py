# -*- coding: utf-8 -*-
"""设置面板的「扫码登录」页：手机不在电脑旁时也能扫。

- GET  /qr             页面（免 token，只读二维码/登录状态）
- GET  /api/qr         当前二维码 PNG
- GET  /api/login      登录状态 online / need_scan / waiting / unknown（含 qr_age）
- POST /api/qr/refresh 重启 NapCat 生成新二维码（约 30 秒）
"""
import subprocess
import threading
import time

QR_PATH = "/app/napcat/cache/qrcode.png"


def _ws_call(action, params=None, timeout=5):
    """最小 WebSocket 客户端：连 NapCat 3001 调 OneBot 接口"""
    import base64
    import json
    import os
    import socket
    import struct

    try:
        s = socket.create_connection(("127.0.0.1", 3001), timeout=timeout)
    except OSError:
        return None
    try:
        key = base64.b64encode(os.urandom(16)).decode()
        req = ("GET / HTTP/1.1\r\nHost: 127.0.0.1:3001\r\nUpgrade: websocket\r\n"
               "Connection: Upgrade\r\nSec-WebSocket-Key: " + key +
               "\r\nSec-WebSocket-Version: 13\r\n\r\n")
        s.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = s.recv(4096)
            if not chunk:
                return None
            resp += chunk
        if "101" not in resp.split(b"\r\n", 1)[0].decode(errors="ignore"):
            return None
        payload = json.dumps({"action": action, "params": params or {}, "echo": "p"}).encode()
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
            if data.get("echo") == "p":
                return data
    except OSError:
        return None
    finally:
        s.close()


def napcat_account():
    """返回 {uin, nickname} 或 None（没登录）"""
    data = _ws_call("get_login_info")
    if not isinstance(data, dict):
        return None
    info = data.get("data") or {}
    if info.get("user_id"):
        return {"uin": str(info["user_id"]), "nickname": str(info.get("nickname") or "")}
    return None


def astrbot_ws_port_alive():
    try:
        r = subprocess.run(
            ["docker", "exec", "astrbot", "python", "-c",
             "import socket;socket.create_connection(('127.0.0.1',6199),3).close();print('ok')"],
            capture_output=True, text=True, timeout=25,
        )
        return r.returncode == 0 and "ok" in (r.stdout or "")
    except Exception:  # noqa: BLE001
        return None


QR_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>爱弥斯 · 扫码登录</title>
<style>
  :root{color-scheme:dark}
  body{margin:0;background:#0a0b0d;color:#e8eaee;font-family:system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
       display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100dvh;gap:16px;padding:24px;text-align:center}
  h1{font-size:18px;font-weight:600;margin:0}
  .state{font-size:13px;padding:6px 12px;border-radius:999px;border:1px solid #2b313a;color:#8b93a1}
  .state.online{color:#5fd0a8;border-color:rgba(95,208,168,.45)}
  .state.scan{color:#d9a441;border-color:rgba(217,164,65,.45)}
  .card{background:#fff;padding:14px;border-radius:14px;box-shadow:0 20px 60px -20px rgba(0,0,0,.8);display:none}
  .card.show{display:block}
  img{display:block;width:min(78vw,340px);height:auto;image-rendering:pixelated}
  .hint{color:#8b93a1;font-size:13px;line-height:1.7;max-width:36ch;margin:0}
  button{background:#1b1f25;color:#e8eaee;border:1px solid #2b313a;border-radius:10px;padding:10px 16px;font-size:14px;cursor:pointer}
  button:active{transform:scale(.98)}
  .empty{color:#8b93a1;font-size:14px;line-height:1.8;margin:0}
</style></head>
<body>
  <h1>爱弥斯 · 扫码登录</h1>
  <div class="state" id="state">读取中…</div>
  <div class="card" id="card"><img id="qr" alt="登录二维码"></div>
  <p class="empty" id="empty" style="display:none"></p>
  <p class="hint">用<b>另一块屏幕</b>显示这张码，手机 QQ 打开「扫一扫」<b>用摄像头</b>对着扫 → 点「允许登录」。<br>QQ 已禁止相册/长按识别二维码，只能摄像头扫。</p>
  <button id="refresh" style="display:none">刷新二维码</button>
<script>
  const q = document.getElementById("qr"), card = document.getElementById("card");
  const st = document.getElementById("state"), empty = document.getElementById("empty");
  const btn = document.getElementById("refresh");
  let busy = false;

  async function tick(){
    if (busy) return;
    try{
      const r = await fetch("/api/login"); const d = await r.json();
      if (d.state === "online"){
        st.textContent = (d.nickname ? d.nickname + " 已登录" : "已登录") + "，无需扫码";
        st.className = "state online";
        card.classList.remove("show"); empty.style.display = "none"; btn.style.display = "none";
        return;
      }
      if (d.state === "adapter_down"){
        st.textContent = "已登录，但 AstrBot 没连上"; st.className = "state scan";
        card.classList.remove("show");
        empty.textContent = "打开设置面板点「重启 AstrBot」即可恢复"; empty.style.display = "block";
        btn.style.display = "none";
        return;
      }
      if (d.qr_age !== null && d.qr_age !== undefined && d.qr_age > 300){
        st.textContent = "二维码已过期（" + Math.round(d.qr_age/60) + " 分钟前生成）";
        st.className = "state scan";
        card.classList.remove("show");
        empty.textContent = "点下面的按钮重新生成二维码"; empty.style.display = "block";
        btn.style.display = "inline-block";
        return;
      }
      st.textContent = d.state === "waiting" ? "等待确认" : "等待扫码";
      st.className = "state scan";
      empty.style.display = "none"; btn.style.display = "none";
      card.classList.add("show");
      q.src = "/api/qr?t=" + Date.now();
    }catch(e){ st.textContent = "连接失败，检查 Tailscale"; }
  }

  btn.onclick = async () => {
    busy = true; btn.disabled = true; btn.textContent = "正在重启 NapCat…";
    try { await fetch("/api/qr/refresh", {method: "POST"}); } catch(e) {}
    setTimeout(() => { busy = false; btn.disabled = false; btn.textContent = "刷新二维码"; tick(); }, 32000);
  };

  tick(); setInterval(tick, 4000);
</script>
</body></html>"""


def qr_age():
    try:
        out = subprocess.run(
            ["docker", "exec", "napcat", "stat", "-c", "%Y", QR_PATH],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip().isdigit():
            return int(time.time()) - int(out.stdout.strip())
    except Exception:  # noqa: BLE001
        pass
    return None


def qr_bytes():
    try:
        out = subprocess.run(
            ["docker", "exec", "napcat", "cat", QR_PATH],
            capture_output=True, timeout=15,
        )
        if out.returncode == 0 and out.stdout[:4] == b"\x89PNG":
            return out.stdout
    except Exception:  # noqa: BLE001
        pass
    return None


def login_state():
    """返回 (状态, 账号信息)

    online      NapCat 已登录 且 AstrBot 的 OneBot 端口在监听（真能用）
    adapter_down 已登录但 AstrBot 端口不通（进程挂死，需要重启）
    need_scan  NapCat 没登录，要扫码
    """
    account = napcat_account()
    if not account:
        return "need_scan", None
    if astrbot_ws_port_alive() is False:
        return "adapter_down", account
    return "online", account


def refresh_qr():
    """重启 NapCat 生成新二维码（异步，页面等 30 秒）"""
    import threading

    def run():
        try:
            subprocess.run(["docker", "restart", "napcat"], timeout=150, check=False)
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=run, daemon=True).start()
    return True
