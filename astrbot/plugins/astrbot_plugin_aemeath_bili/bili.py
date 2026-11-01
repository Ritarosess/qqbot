# -*- coding: utf-8 -*-
"""B站视频抓取：元信息、DASH 流下载、抽帧拼图（不依赖 yt-dlp）。

要点：
- api.bilibili.com 对机房 IP 会返回 -412，带上任意 buvid3/b_nut cookie 即可放行
- 取流需要 WBI 签名（nav 里的 img/sub key → 混入 44 个下标 → md5）
- 只取画面轨（不要音轨），抽帧够用且省流量
"""
import hashlib
import os
import random
import re
import string
import subprocess
import time
import urllib.parse

try:
    import httpx
except ImportError:  # 测试环境没装 httpx，纯函数仍可用
    httpx = None

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Referer": "https://www.bilibili.com/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}
UA_MOBILE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")

MIXIN = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]

BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
AV_RE = re.compile(r"(?:^|[/=])(?:av|AV)(\d{5,12})")
B23_RE = re.compile(r"https?://b23\.tv/[0-9A-Za-z]+")
URL_RE = re.compile(r"https?://(?:[a-z0-9-]+\.)?bilibili\.com/[^\s]+")
SHORT_RE = re.compile(r"https?://(?:b23\.tv|bili2233\.cn)/[0-9A-Za-z]+")


def extract_video_id(text: str) -> str | None:
    """从文本里抠出 bvid / av 号；b23 短链不在这里解析"""
    m = BV_RE.search(text or "")
    if m:
        return m.group(1)
    m = AV_RE.search(text or "")
    if m:
        return f"av{m.group(1)}"
    return None


def find_short_link(text: str) -> str | None:
    m = SHORT_RE.search(text or "")
    return m.group(0) if m else None


def has_bili_link(text: str) -> bool:
    """有 B站链接（bilibili/b23）才算；裸 BV 号也算；别的网站里的 BV 字符串不算"""
    text = text or ""
    if SHORT_RE.search(text) or URL_RE.search(text):
        return True
    # 没有任何 http 链接时才接受裸 BV/av
    if re.search(r"https?://", text):
        return False
    return bool(extract_video_id(text))


def pick_stream(streams: list[dict], max_height: int = 480) -> dict | None:
    """挑高度不超过 max_height 里最小的那路（省流量），没有就退到最矮的"""
    if not streams:
        return None

    def height(s):
        try:
            return int(s.get("height") or 0)
        except (TypeError, ValueError):
            return 0

    fit = [s for s in streams if 0 < height(s) <= max_height]
    if fit:
        return min(fit, key=height)
    return min(streams, key=lambda s: height(s) or 99999)


def grid_layout(count: int, cols: int = 4) -> tuple[int, int]:
    cols = max(1, cols)
    rows = max(1, (count + cols - 1) // cols)
    return cols, rows


def frame_times(duration: int, count: int) -> list[float]:
    """均匀取 count 个时间点，避开片头片尾"""
    if duration <= 0 or count <= 0:
        return []
    start = min(3.0, duration * 0.05)
    end = max(start + 1, duration * 0.95)
    span = end - start
    return [round(start + span * i / max(1, count - 1), 2) for i in range(count)]


class BiliError(RuntimeError):
    pass


class Bili:
    def __init__(self, timeout: float = 20.0):
        if httpx is None:
            raise BiliError("缺少 httpx 依赖")
        self._client = httpx.Client(
            headers=HEADERS,
            cookies={"buvid3": self._fake_buvid(), "b_nut": str(int(time.time()))},
            timeout=timeout,
            follow_redirects=True,
        )
        self._mixin = ""
        self._keys_at = 0.0

    @staticmethod
    def _fake_buvid() -> str:
        return "".join(random.choice(string.hexdigits[:16] + "ABCDEF") for _ in range(36)) + "infoc"

    # ---------- WBI ----------

    def _ensure_keys(self) -> str:
        if self._mixin and time.time() - self._keys_at < 1800:
            return self._mixin
        r = self._client.get("https://api.bilibili.com/x/web-interface/nav")
        data = r.json()
        wbi = ((data.get("data") or {}).get("wbi_img") or {})
        img = (wbi.get("img_url") or "").rsplit("/", 1)[-1].split(".")[0]
        sub = (wbi.get("sub_url") or "").rsplit("/", 1)[-1].split(".")[0]
        if not img or not sub:
            raise BiliError("拿不到 WBI key")
        raw = img + sub
        self._mixin = "".join(raw[i] for i in MIXIN if i < len(raw))[:32]
        self._keys_at = time.time()
        return self._mixin

    def _sign(self, params: dict) -> dict:
        mixin = self._ensure_keys()
        params = dict(params)
        params["wts"] = int(time.time())
        query = urllib.parse.urlencode(
            [(k, str(v)) for k, v in sorted(params.items())],
            quote_via=urllib.parse.quote,
        )
        params["w_rid"] = hashlib.md5((query + mixin).encode()).hexdigest()
        return params

    # ---------- 接口 ----------

    def resolve_short(self, url: str) -> str:
        try:
            r = self._client.get(url)
            return str(r.url)
        except httpx.HTTPError as exc:
            raise BiliError(f"短链解析失败: {exc}") from exc

    def resolve_input(self, text: str) -> str:
        """把用户输入（链接/BV/短链）转成 bvid 或 avxxx"""
        vid = extract_video_id(text)
        if vid:
            return vid
        short = find_short_link(text)
        if short:
            final = self.resolve_short(short)
            vid = extract_video_id(final)
            if vid:
                return vid
        url = URL_RE.search(text or "")
        if url:
            vid = extract_video_id(url.group(0))
            if vid:
                return vid
        raise BiliError("没找到 B站视频号")

    def view(self, vid: str) -> dict:
        params = {"bvid": vid} if vid.startswith("BV") else {"aid": vid[2:]}
        r = self._client.get("https://api.bilibili.com/x/web-interface/view", params=params)
        data = r.json()
        if data.get("code") != 0:
            raise BiliError(f"取视频信息失败: {data.get('code')} {data.get('message')}")
        d = data.get("data") or {}
        pages = d.get("pages") or []
        return {
            "bvid": d.get("bvid"),
            "aid": d.get("aid"),
            "title": d.get("title") or "",
            "desc": d.get("desc") or "",
            "owner": (d.get("owner") or {}).get("name") or "",
            "duration": int(d.get("duration") or 0),
            "pic": d.get("pic") or "",
            "cid": (pages[0] or {}).get("cid") if pages else None,
            "stat": d.get("stat") or {},
            "pubdate": d.get("pubdate") or 0,
        }

    def playurl(self, bvid: str, cid: int, max_height: int = 480) -> dict:
        params = self._sign({
            "bvid": bvid, "cid": cid, "qn": 64, "fnval": 16, "fnver": 0, "fourk": 0,
        })
        r = self._client.get("https://api.bilibili.com/x/player/wbi/playurl", params=params)
        data = r.json()
        if data.get("code") != 0:
            raise BiliError(f"取流失败: {data.get('code')} {data.get('message')}")
        dash = ((data.get("data") or {}).get("dash") or {})
        streams = dash.get("video") or []
        picked = pick_stream(streams, max_height)
        if not picked:
            raise BiliError("没有可用画面流")
        return picked

    def comments(self, aid: int, n: int = 5) -> list[str]:
        try:
            r = self._client.get("https://api.bilibili.com/x/v2/reply/main",
                                 params={"type": 1, "oid": aid, "mode": 3, "ps": max(5, n)})
            data = r.json()
            items = ((data.get("data") or {}).get("replies") or [])
            out = []
            for it in items[:n]:
                content = ((it.get("content") or {}).get("message") or "").strip()
                if content:
                    out.append(content[:80])
            return out
        except Exception:  # noqa: BLE001
            return []

    def download(self, url: str, path: str, max_bytes: int, timeout: float = 180.0) -> int:
        total = 0
        with self._client.stream("GET", url, timeout=timeout) as resp:
            if resp.status_code not in (200, 206):
                raise BiliError(f"下载失败: HTTP {resp.status_code}")
            with open(path, "wb") as f:
                for chunk in resp.iter_bytes(65536):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        f.write(chunk)
                        raise BiliError(f"视频太大（超过 {max_bytes // 1024 // 1024}MB）")
                    f.write(chunk)
        return total


def extract_frames(video: str, times: list[float], out_dir: str, width: int = 480) -> list[str]:
    os.makedirs(out_dir, exist_ok=True)
    files = []
    for idx, ts in enumerate(times):
        out = os.path.join(out_dir, f"f{idx:02d}.jpg")
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error", "-ss", f"{ts:.2f}", "-i", video,
            "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", out,
        ]
        subprocess.run(cmd, capture_output=True, timeout=60, check=False)
        if os.path.exists(out) and os.path.getsize(out) > 1000:
            files.append(out)
    return files


def make_storyboard(frames: list[str], out_path: str, cols: int = 4, labels: list[str] | None = None) -> str:
    """把抽帧拼成一张图，左上角烧上时间标签"""
    from PIL import Image, ImageDraw

    if not frames:
        raise BiliError("没有抽到任何画面")
    imgs = [Image.open(p).convert("RGB") for p in frames]
    w = min(im.width for im in imgs)
    imgs = [im.resize((w, int(im.height * w / im.width))) for im in imgs]
    h = max(im.height for im in imgs)
    cols, rows = grid_layout(len(imgs), cols)
    pad = 4
    canvas = Image.new("RGB", (cols * w + pad * (cols + 1), rows * h + pad * (rows + 1)), (14, 15, 17))
    draw = ImageDraw.Draw(canvas)
    for i, im in enumerate(imgs):
        cx = pad + (i % cols) * (w + pad)
        cy = pad + (i // cols) * (h + pad)
        canvas.paste(im, (cx, cy))
        if labels and i < len(labels):
            text = labels[i]
            draw.rectangle([cx + 2, cy + 2, cx + 8 + 7 * len(text), cy + 16], fill=(0, 0, 0))
            draw.text((cx + 5, cy + 4), text, fill=(255, 255, 255))
    canvas.save(out_path, "JPEG", quality=86)
    return out_path


def fmt_duration(seconds: int) -> str:
    seconds = int(seconds or 0)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_num(n) -> str:
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return "0"
    if n >= 100000000:
        return f"{n / 100000000:.1f}亿"
    if n >= 10000:
        return f"{n / 10000:.1f}万"
    return str(n)
