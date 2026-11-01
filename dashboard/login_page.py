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
  <p class="hint">手机 QQ 扫码 → 点「允许登录」。<br>不方便扫屏幕：截图后 QQ 扫一扫 → 从相册选择。</p>
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
        st.textContent = "已登录，无需扫码"; st.className = "state online";
        card.classList.remove("show"); empty.style.display = "none"; btn.style.display = "none";
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
    """看最近 6 小时日志里最后一条关键行：收发=在线，二维码=要扫码"""
    try:
        out = subprocess.run(
            ["docker", "logs", "--since", "6h", "napcat"],
            capture_output=True, text=True, timeout=20,
        )
        text = (out.stdout or "") + (out.stderr or "")
    except Exception:  # noqa: BLE001
        return "unknown"
    lines = [ln for ln in text.splitlines() if ln.strip()][-2000:]
    for line in reversed(lines):
        if "接收 <-" in line or "发送 ->" in line or "登录成功" in line:
            return "online"
        if "请扫描下面的二维码" in line or "Login Error" in line or "KickedOffLine" in line:
            return "need_scan"
        if "二维码已被扫描" in line:
            return "waiting"
    return "unknown"


def refresh_qr():
    """重启 NapCat 生成新二维码（异步）"""
    def run():
        try:
            subprocess.run(["docker", "restart", "napcat"], timeout=150, check=False)
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=run, daemon=True).start()
    return True
