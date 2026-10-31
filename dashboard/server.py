#!/usr/bin/env python3
"""爱弥斯 · 设置面板（后端，仅标准库）

- 读取 /opt/astrbot/plugins/*/_conf_schema.json + /opt/astrbot/data/config/*_config.json
- 前端表单直接改配置（写回 JSON，重启 AstrBot 生效）
- 附带「重启 AstrBot」「查看状态」「查看日志」接口
- 只监听 127.0.0.1，通过 SSH 隧道访问；带 token 鉴权
"""
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.environ.get("DASH_CONFIG_DIR", "/opt/astrbot/data/config")
PLUGIN_DIR = os.environ.get("DASH_PLUGIN_DIR", "/opt/astrbot/plugins")
TOKEN_FILE = os.environ.get("DASH_TOKEN_FILE", os.path.join(BASE_DIR, "token"))
INDEX = os.path.join(BASE_DIR, "index.html")
PORT = int(os.environ.get("DASH_PORT", "8610"))
# 默认只听本机；要直接公网访问时设 DASH_HOST=0.0.0.0（务必配合安全组限制来源 IP）
HOST = os.environ.get("DASH_HOST", "127.0.0.1")
MEMORY_DIR = os.environ.get(
    "DASH_MEMORY_DIR", "/opt/astrbot/plugins/astrbot_plugin_aemeath_memory/data")
MEMORY_FILE = os.path.join(MEMORY_DIR, "memory.json")
MEMORY_REQ = os.path.join(MEMORY_DIR, "backfill_request.json")
MEMORY_STATUS = os.path.join(MEMORY_DIR, "backfill_status.json")


# ---------- 基础工具 ----------

def read_json(path):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    os.replace(tmp, path)


def get_token():
    token = os.environ.get("DASH_TOKEN", "").strip()
    if token:
        return token
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            token = f.read().strip()
    except OSError:
        token = ""
    if not token:
        token = secrets.token_urlsafe(18)
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(token)
        os.chmod(TOKEN_FILE, 0o600)
    return token


def meta_name(plugin_dir, name):
    """从 metadata.yaml 里取 display_name / desc（没有就退回插件目录名）"""
    path = os.path.join(plugin_dir, "metadata.yaml")
    info = {"display_name": name, "desc": ""}
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return info
    m = re.search(r"^display_name:\s*(.+)$", text, re.M)
    if m:
        info["display_name"] = m.group(1).strip()
    m = re.search(r"^desc:\s*(.+)$", text, re.M)
    if m:
        info["desc"] = m.group(1).strip()
    return info


def config_path(name):
    return os.path.join(CONFIG_DIR, f"{name}_config.json")


# ---------- 插件配置 ----------

def list_plugins():
    items = []
    if not os.path.isdir(PLUGIN_DIR):
        return items
    # 常用插件排在最前（群白名单最常改）
    preferred = ["astrbot_plugin_aemeath_access"]
    names = sorted(os.listdir(PLUGIN_DIR))
    names.sort(key=lambda n: (preferred.index(n) if n in preferred else len(preferred), n))
    for name in names:
        pdir = os.path.join(PLUGIN_DIR, name)
        if not os.path.isdir(pdir) or name.startswith("__"):
            continue
        schema = read_json(os.path.join(pdir, "_conf_schema.json")) or {}
        meta = meta_name(pdir, name)
        cfg = read_json(config_path(name)) or {}
        items.append({
            "name": name,
            "display_name": meta["display_name"],
            "desc": meta["desc"],
            "schema": schema,
            "config": cfg,
            "has_config": bool(cfg),
        })
    return items


def known_groups():
    """收集项目里出现过的群号：插件配置里的逗号分隔数字 + 日记/总结的 umo 映射"""
    ids = set()
    if os.path.isdir(CONFIG_DIR):
        for fn in os.listdir(CONFIG_DIR):
            if not fn.endswith("_config.json"):
                continue
            cfg = read_json(os.path.join(CONFIG_DIR, fn)) or {}
            _walk_ids(cfg, ids)
    data_dir = os.environ.get("DASH_DATA_DIR", "/opt/astrbot/data")
    for sub in ("astrbot_plugin_aemeath_diary", "astrbot_plugin_aemeath_summary"):
        umo = read_json(os.path.join(data_dir, "plugin_data", sub, "umo.json")) or {}
        if isinstance(umo, dict):
            ids.update(k for k in umo.keys() if str(k).isdigit())
    return sorted(ids, key=lambda x: (len(x), x))


def _walk_ids(node, ids):
    if isinstance(node, dict):
        for v in node.values():
            _walk_ids(v, ids)
    elif isinstance(node, list):
        for v in node:
            _walk_ids(v, ids)
    elif isinstance(node, str):
        for token in re.split(r"[,，\s]+", node):
            token = token.strip()
            if token.isdigit() and 5 <= len(token) <= 12:
                ids.add(token)


def coerce(value, spec):
    """按 schema 类型转换前端提交的值；失败抛 ValueError"""
    typ = (spec or {}).get("type", "string")
    if typ == "bool":
        if isinstance(value, bool):
            return value
        return str(value).lower() in ("true", "1", "on", "yes")
    if typ == "int":
        return int(str(value).strip() or 0)
    if typ == "float":
        return float(str(value).strip() or 0)
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value if value is not None else "")


def save_plugin(name, payload):
    plugins = {p["name"]: p for p in list_plugins()}
    if name not in plugins:
        raise KeyError(name)
    schema = plugins[name]["schema"] or {}
    cfg = read_json(config_path(name)) or {}
    changed = {}
    for key, value in (payload or {}).items():
        if key not in schema and key not in cfg:
            continue  # 不认识又不存在的键直接忽略
        changed[key] = coerce(value, schema.get(key))
    cfg.update(changed)
    os.makedirs(CONFIG_DIR, exist_ok=True)
    backup = config_path(name) + ".bak"
    if os.path.exists(config_path(name)):
        shutil.copyfile(config_path(name), backup)
    write_json(config_path(name), cfg)
    return cfg


# ---------- 活跃度（群聊插话频率） ----------

ACTIVITY_PLUGIN = "astrbot_plugin_aemeath_group_chat"
ACTIVITY_LEVELS = [
    ("quiet", "安静", {"probability": 0.01, "cooldown": 900, "min_len": 6}),
    ("low", "低调", {"probability": 0.03, "cooldown": 600, "min_len": 5}),
    ("normal", "正常", {"probability": 0.05, "cooldown": 300, "min_len": 4}),
    ("high", "活跃", {"probability": 0.15, "cooldown": 120, "min_len": 3}),
    ("max", "话痨", {"probability": 0.25, "cooldown": 60, "min_len": 2}),
]
ACTIVITY_KEYS = ("probability", "cooldown", "min_len")


def activity_state():
    cfg = read_json(config_path(ACTIVITY_PLUGIN)) or {}
    values = {k: cfg.get(k) for k in ACTIVITY_KEYS}
    level = None
    best = None
    if isinstance(values.get("probability"), (int, float)):
        for key, _label, preset in ACTIVITY_LEVELS:
            dist = abs(float(values["probability"]) - float(preset["probability"]))
            if best is None or dist < best:
                best, level = dist, key
    return {
        "plugin": ACTIVITY_PLUGIN,
        "level": level or "normal",
        "values": values,
        "levels": [{"key": k, "label": label, "values": v} for k, label, v in ACTIVITY_LEVELS],
        "enabled": bool(cfg.get("enabled", True)),
    }


def set_activity(level):
    preset = next((v for k, _l, v in ACTIVITY_LEVELS if k == level), None)
    if preset is None:
        raise ValueError(f"未知活跃度: {level}")
    plugins = {p["name"]: p for p in list_plugins()}
    if ACTIVITY_PLUGIN not in plugins:
        raise ValueError("群聊插话插件未安装")
    path = config_path(ACTIVITY_PLUGIN)
    cfg = read_json(path) or {}
    cfg.update(preset)
    os.makedirs(CONFIG_DIR, exist_ok=True)
    if os.path.exists(path):
        shutil.copyfile(path, path + ".bak")
    write_json(path, cfg)
    return activity_state()



# ---------- 记忆（长期记忆插件的数据） ----------

def _memory_records():
    data = read_json(MEMORY_FILE) or {}
    records = data.get("records") if isinstance(data, dict) else data
    return [r for r in (records or []) if isinstance(r, dict) and r.get("text")]


def _save_memory(records):
    os.makedirs(MEMORY_DIR, exist_ok=True)
    if os.path.exists(MEMORY_FILE):
        shutil.copyfile(MEMORY_FILE, MEMORY_FILE + ".bak")
    write_json(MEMORY_FILE, {"version": 1, "updated": int(time.time()), "records": records})


def memory_state(query=""):
    records = _memory_records()
    by_clarity = {}
    by_scope = {}
    for rec in records:
        key = str(rec.get("clarity") or "clear")
        by_clarity[key] = by_clarity.get(key, 0) + 1
        scope = str(rec.get("scope") or "")
        group = "全局" if scope == "global" else ("群聊" if scope.startswith("group:") else scope)
        by_scope[group] = by_scope.get(group, 0) + 1
    items = records
    q = (query or "").strip().lower()
    if q:
        items = [r for r in records if q in str(r.get("text", "")).lower()]
    order = {"vivid": 0, "clear": 1, "fuzzy": 2, "trace": 3, "forgotten": 4}
    items = sorted(items, key=lambda r: (order.get(str(r.get("clarity")), 5),
                                         -int(r.get("importance") or 0),
                                         -float(r.get("last_seen") or 0)))
    return {
        "total": len(records),
        "by_clarity": by_clarity,
        "by_scope": by_scope,
        "records": items[:400],
        "matched": len(items),
        "backfill": read_json(MEMORY_STATUS) or {},
    }


def memory_action(payload):
    action = str(payload.get("action") or "")
    records = _memory_records()
    if action == "delete":
        target = str(payload.get("id") or "")
        kept = [r for r in records if str(r.get("id")) != target]
        if len(kept) == len(records):
            raise ValueError("没有这条记忆")
        _save_memory(kept)
    elif action == "add":
        text = str(payload.get("text") or "").strip()
        if len(text) < 2:
            raise ValueError("内容太短")
        scope = str(payload.get("scope") or "global")
        records.append({
            "id": os.urandom(4).hex(),
            "text": text[:300],
            "scope": scope,
            "importance": int(payload.get("importance") or 80),
            "pinned": True,
            "who": "",
            "created": time.time(),
            "last_seen": time.time(),
            "recalls": 0,
            "clarity": "vivid",
            "source": "panel",
        })
        _save_memory(records)
    elif action == "clear":
        _save_memory([])
    elif action == "backfill":
        days = max(1, min(14, int(payload.get("days") or 7)))
        os.makedirs(MEMORY_DIR, exist_ok=True)
        write_json(MEMORY_REQ, {"days": days, "at": time.time()})
        write_json(MEMORY_STATUS, {"state": "requested", "days": days, "done": 0,
                                   "total": 0, "added": 0, "started": time.time()})
    else:
        raise ValueError(f"未知操作: {action}")
    return memory_state()


# ---------- 运行状态 ----------

def docker(*args, timeout=60):
    try:
        out = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
        return out.returncode, (out.stdout or "") + (out.stderr or "")
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)


def status():
    code, out = docker("ps", "--format", "{{.Names}}|{{.Status}}|{{.Image}}")
    containers = []
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) >= 2:
            containers.append({"name": parts[0], "status": parts[1]})
    mem = ""
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            info = {}
            for line in f:
                k, v = line.split(":", 1)
                info[k] = int(v.split()[0])
            mem = f"{info['MemTotal'] // 1024}MB 总 / {info['MemAvailable'] // 1024}MB 可用 / " \
                  f"swap {info.get('SwapTotal', 0) // 1024}MB"
    except (OSError, ValueError, KeyError):
        pass
    return {"containers": containers, "memory": mem}


def restart_bot():
    code, out = docker("restart", "astrbot", timeout=180)
    return code == 0, out.strip()[-800:]


def bot_logs(lines=200):
    code, out = docker("logs", "--tail", str(max(20, min(2000, int(lines)))), "astrbot")
    text = re.sub(r"\x1b\[[0-9;]*m", "", out)
    return text[-60000:]


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "AemeathDash/1.0"

    def log_message(self, fmt, *args):  # 安静点
        pass

    def _send(self, code, body=b"", ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode())

    def _authed(self):
        token = get_token()
        if self.headers.get("X-Token") == token:
            return True
        qs = parse_qs(urlparse(self.path).query)
        return qs.get("token", [""])[0] == token

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            try:
                with open(INDEX, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError:
                self._send(404, b"index.html missing", "text/plain; charset=utf-8")
            return
        if not self._authed():
            self._json(401, {"error": "unauthorized"})
            return
        if path == "/api/plugins":
            self._json(200, {"plugins": list_plugins()})
            return
        if path == "/api/groups":
            self._json(200, {"groups": known_groups()})
            return
        if path == "/api/status":
            self._json(200, status())
            return
        if path == "/api/logs":
            qs = parse_qs(parsed.query)
            lines = int(qs.get("lines", ["200"])[0] or 200)
            self._json(200, {"logs": bot_logs(lines)})
            return
        if path == "/api/activity":
            self._json(200, activity_state())
            return
        if path == "/api/memory":
            qs = parse_qs(parsed.query)
            self._json(200, memory_state(qs.get("q", [""])[0]))
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if not self._authed():
            self._json(401, {"error": "unauthorized"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            self._json(400, {"error": "bad json"})
            return
        parsed = urlparse(self.path)
        if parsed.path == "/api/save":
            name = str(payload.get("plugin") or "")
            try:
                cfg = save_plugin(name, payload.get("config") or {})
            except KeyError:
                self._json(404, {"error": "unknown plugin"})
                return
            except (ValueError, OSError) as exc:
                self._json(400, {"error": f"保存失败: {exc}"})
                return
            self._json(200, {"ok": True, "config": cfg})
            return
        if parsed.path == "/api/restart":
            ok, out = restart_bot()
            self._json(200 if ok else 500, {"ok": ok, "output": out})
            return
        if parsed.path == "/api/memory":
            try:
                self._json(200, memory_action(payload))
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
            return
        if parsed.path == "/api/activity":
            try:
                state = set_activity(str(payload.get("level") or ""))
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
                return
            self._json(200, state)
            return
        self._json(404, {"error": "not found"})


def main():
    get_token()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"settings dashboard on http://{HOST}:{PORT}/ (config: {CONFIG_DIR})", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
