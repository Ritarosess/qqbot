# -*- coding: utf-8 -*-
"""设置面板加「扫码登录」页：手机不在电脑旁时，用手机浏览器打开就能看到实时二维码。

- GET /qr            实时二维码页面（每 3 秒自动换图，带登录状态）
- GET /api/qr        当前二维码 PNG
- GET /api/login     当前登录状态（online / need_scan / unknown）
"""
import json
import os
import subprocess
import time

QR_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>扫码登录</title>
<style>
  body{margin:0;background:#0a0b0d;color:#e8eaee;font-family:system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
       display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100dvh;gap:18px;padding:24px}
  h1{font-size:18px;font-weight:600;margin:0}
  .card{background:#fff;padding:14px;border-radius:14px;box-shadow:0 20px 60px -20px rgba(0,0,0,.8)}
  img{display:block;width:min(78vw,360px);height:auto;image-rendering:pixelated}
  .hint{color:#8b93a1;font-size:13px;text-align:center;line-height:1.7;max-width:34ch}
  .state{font-size:13px;padding:6px 12px;border-radius:999px;border:1px solid #2b313a;color:#8b93a1}
  .state.online{color:#5fd0a8;border-color:rgba(95,208,168,.45)}
  .state.scan{color:#d9a441;border-color:rgba(217,164,65,.45)}
</style></head>
<body>
  <h1>爱弥斯 · 扫码登录</h1>
  <div class="state" id="state">读取中…</div>
  <div class="card"><img id="qr" alt="登录二维码"></div>
  <p class="hint">用手机 QQ 扫这张图 → 点「允许登录」。<br>不方便扫屏幕时：截图后打开 QQ → 扫一扫 → 从相册选择。</p>
<script>
  const token = new URLSearchParams(location.search).get("token") || "";
  const q = document.getElementById("qr"), s = document.getElementById("state");
  async function tick(){
    try {
      const r = await fetch("/api/login", {headers: {"X-Token": token}});
      const d = await r.json();
      if (d.state === "online") { s.textContent = "已登录，无需扫码"; s.className = "state online"; }
      else if (d.state === "need_scan") { s.textContent = "等待扫码"; s.className = "state scan"; }
      else { s.textContent = "等待确认"; s.className = "state"; }
      if (d.state !== "online") { q.src = "/api/qr?token=" + encodeURIComponent(token) + "&t=" + Date.now(); }
    } catch (e) { s.textContent = "连接失败"; }
  }
  tick(); setInterval(tick, 3000);
</script>
</body></html>"""


def qr_bytes():
    """从 napcat 容器里取当前二维码 PNG"""
    try:
        out = subprocess.run(
            ["docker", "exec", "napcat", "cat", "/app/napcat/cache/qrcode.png"],
            capture_output=True, timeout=15,
        )
        if out.returncode == 0 and out.stdout[:4] == b"\x89PNG":
            return out.stdout
    except Exception:  # noqa: BLE001
        pass
    return None


def login_state():
    """从最新一行往回找：先遇到收发就是在线，先遇到二维码就是需要扫码"""
    try:
        out = subprocess.run(
            ["docker", "logs", "--since", "15m", "napcat"],
            capture_output=True, text=True, timeout=20,
        )
        text = (out.stdout or "") + (out.stderr or "")
    except Exception:  # noqa: BLE001
        return "unknown"
    lines = [ln for ln in text.splitlines() if ln.strip()][-800:]
    for line in reversed(lines):
        if "接收 <-" in line or "发送 ->" in line or "登录成功" in line:
            return "online"
        if "请扫描下面的二维码" in line or "Login Error" in line or "KickedOffLine" in line:
            return "need_scan"
        if "二维码已被扫描" in line:
            return "waiting"
    return "unknown"
