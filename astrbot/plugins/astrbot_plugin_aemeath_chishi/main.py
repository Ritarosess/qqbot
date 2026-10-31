"""
爱弥斯 · 吃史仙人（AstrBot 移植版，源自 https://github.com/Alphamancer/chishi-xianren 的 Electron 版）
原插件是 QQ Agent（Electron）的 JS 插件，本文件是按 AstrBot 能力重写的 Python 版。

能力：
- 群里有人发「合并转发 / 分享卡片 / 聊天截图/图片」，她读全内容，按人设定级（Lv1~5）后甩一句反应
- 够离谱（Lv>=feedMinLevel）且概率命中时，悄悄搬到 feedGroups
- 手动搬运：搬过去 / 发我 / 发给QQ / 私聊QQ / 搬到群号 / 群号（群里仅 ownerQQ 可叫；私聊不限）
- 观察白名单 watchGroups / 评论白名单 commentGroups / 搬史白名单 feedGroups
- 同一份内容 5 分钟内不重复搬（跨目标独立）
- 图片走视觉：直接把图片 URL 传给模型（deepseek-flash 支持看图），无需 OCR

与原版的差异：
- AstrBot 原生丢弃合并转发消息段，这里直接解析原始 OneBot 事件（含 get_forward_msg 拉取）
- 转发不再重建成合并转发节点，改为「文字 + 图片」发送（保真度略降，但稳定）
- 未实现：梗百科/萌娘百科查梗（后续可加）、套娃转发的嵌套保留
"""
import asyncio
import hashlib
import json
import random
import re
import time
import uuid
from collections import deque

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star
import astrbot.api.message_components as Comp

LEVELS = (
    "分级标准（你自己按人设标准打）：\n"
    "1 = 白开水，日常闲聊没信息量\n"
    "2 = 有点意思，小梗小笑话\n"
    "3 = 逆天，离谱操作、翻车现场\n"
    "4 = 典中典，经典梗、老活新整\n"
    "5 = 破防，真恶心、真下头\n"
)

TASK = (
    "\n\n【任务】你正在群里，看到了别人转发/分享的聊天记录、卡片或截图。\n"
    "读完后挑一个具体的点甩一句态度，不要复述、不要总结、不要写小作文。\n"
    f"{LEVELS}"
    "另外单独打主题标签，和上面的分级无关（只决定搬运方式，不影响你说话）：\n"
    "内容主要是美少女/二次元美图/动漫少女（文字或图片都算）→ 打「美少女」\n"
    "内容很抽象（抽象话、离谱整活、烂梗、逆天截图）→ 打「抽象」\n"
    "内容有擦边（轻度性感、暗示、露而不露）→ 打「擦边」；擦边和 R18 互斥，只能选一个\n"
    "内容含 R18 要素（色情、露骨性暗示）→ **必须**打「R18」，这一条优先\n"
    "多个标签用逗号分隔；都不符合 → 打「其他」\n"
    "输出格式（严格遵守三行）：\n"
    "TAG: 美少女,抽象,擦边,R18|其他\n"
    "LV: <1-5 的数字>\n"
    "<一句反应，不超过 {chars} 字，不要 [emoji] 等标记>\n"
)

MOVE_PAT = re.compile(
    r"(?:搬|转|发|送)(?:过去|出去|到|去|给)|(?:搬|转|喂)(?:一下|给我|过来|走|给|出)"
    r"|发我|私聊|私发|私我|小窗|发过来|拿过来"
)
GROUP_MOVE_PAT = re.compile(r"(?:搬到|转到|发到|送到|搬去|转去|搬给|转给|喂给|群)\s*(\d{5,12})")
PRIVATE_MOVE_PAT = re.compile(r"(?:发给|私聊|私发|私我|小窗)\s*(\d{5,12})")
FEED_PHRASES = {"搬过去", "转过去", "发过去", "送过去", "搬一下", "喂一下", "搬给我", "喂给我"}
# 口语触发：只认「搬」字（「这条也搬」「这个也搬」「再搬一次」「这图搬走」），
# 不用 转/喂，避免「我要把这个喂给我家鱼」这类正常聊天被误判
NATURAL_MOVE_PAT = re.compile(
    r"(?:这条|这个|那条|那个|这份|那份|这图|那张|再|也)\s*(?:也|再)?\s*搬")
NEGATIVE_PAT = re.compile(r"(?:别|不要|不用|先不|不)\s*(?:搬|转|喂)")

DEFAULT_PERSONA = (
    "你是爱弥斯（Aemeath），《鸣潮》里来自拉海洛的少女，星炬学院学生、校园歌手「飞行雪绒」。"
    "性格元气俏皮、好奇心旺盛。在群里看到离谱的事会直接吐槽，但说话短、不啰嗦。"
)


def in_list(raw: str, *ids: str) -> bool:
    """判断 ids 里是否有任一命中逗号/空格分隔的名单"""
    items = {x for x in re.split(r"[,，\s]+", raw or "") if x}
    return any(i and i in items for i in ids)


LEVEL_RE = re.compile(r"(?:LV|等级|评分|评级)\s*[:：]\s*([1-5])", re.IGNORECASE)
TAG_RE = re.compile(r"(?:TAG|标签)\s*[:：]\s*([^\n]+)", re.IGNORECASE)
# 模型有时不按英文格式输出（写成「标签：抽象」「等级：3」），这些行一律不展示
META_LINE_RE = re.compile(r"^[ \t]*(?:TAG|标签|LV|等级|评分|评级)\s*[:：].*$", re.IGNORECASE | re.MULTILINE)


def level_comment(text: str) -> tuple[int, str]:
    """从模型输出解析 (等级, 评语)；兼容 `LV:` 与「等级：」两种写法，并剔除元信息行"""
    text = (text or "").strip()
    m = LEVEL_RE.search(text)
    level = int(m.group(1)) if m else 0
    body = META_LINE_RE.sub("", text)
    body = re.sub(r"\n{3,}", "\n\n", body).strip("*`> \n")
    return level, body


def parse_tag(text: str) -> list[str]:
    """解析主题标签（TAG: 美少女,抽象 / 标签：抽象）——只用于自动搬运过滤，与分级无关"""
    m = TAG_RE.search(text or "")
    if not m:
        return []
    return [t for t in re.split(r"[,，/、\s]+", m.group(1)) if t]


def hit_keywords(raw: str, text: str) -> str:
    """文本里命中任一关键词则返回该词，否则空串"""
    for word in re.split(r"[,，\s]+", raw or ""):
        if word and word in (text or ""):
            return word
    return ""


def match_move(text: str):
    """解析搬运指令 → ("feed"|"private"|"group"|"self", target) 或 None

    只认短消息（≤24 字），避免普通聊天里带"发给/转到"被误判。
    """
    t = (text or "").strip()
    if not t or len(t) > 24:
        return None
    m = GROUP_MOVE_PAT.search(t)
    if m:
        return "group", m.group(1)
    m = PRIVATE_MOVE_PAT.search(t)
    if m:
        return "private", m.group(1)
    if re.fullmatch(r"\d{5,12}", t):
        gid = t
        return ("group" if len(gid) <= 10 else "private"), gid
    if t in FEED_PHRASES:
        return "feed", ""
    if t == "发我" or (t.endswith("发我") and len(t) <= 8):
        return "self", ""
    # 口语：「这条也搬」「这个也搬」「再搬一次」——没指定去处，搬最近一份
    if NEGATIVE_PAT.search(t):
        return None
    if len(t) <= 14 and NATURAL_MOVE_PAT.search(t):
        return "feed", ""
    return None


def _segments_of(event) -> list:
    raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
    segs = raw.get("message") if isinstance(raw, dict) else getattr(raw, "message", None)
    return [s for s in segs or [] if isinstance(s, dict)]


def reply_id_of(event) -> str:
    """当前消息引用了哪条消息（reply 段 id）"""
    for seg in _segments_of(event):
        if seg.get("type") == "reply":
            rid = str((seg.get("data") or {}).get("id") or "")
            if rid:
                return rid
    return ""


def parse_seg_list(segs: list) -> dict:
    out = {"texts": [], "image_urls": [], "forward_ids": [], "card_texts": [], "nodes": [], "urls": []}
    for seg in segs or []:
        if not isinstance(seg, dict):
            continue
        st, data = seg.get("type"), seg.get("data") or {}
        if st == "text":
            txt = str(data.get("text") or "").strip()
            if txt:
                out["texts"].append(txt)
        elif st == "image":
            url = str(data.get("url") or "")
            if url.startswith("http"):
                out["image_urls"].append(url)
        elif st == "forward":
            fid = str(data.get("id") or "")
            if fid:
                out["forward_ids"].append(fid)
            # 有的实现直接内联 content（node 数组）
            for node in data.get("content") or []:
                if isinstance(node, dict):
                    nd = node.get("data") or {}
                    name = str(nd.get("nickname") or nd.get("name") or "群友")
                    uin = str(nd.get("user_id") or nd.get("uin") or "0")
                    content = nd.get("content")
                    parts = []
                    if isinstance(content, list):
                        for s in content:
                            if not isinstance(s, dict):
                                continue
                            if s.get("type") == "text":
                                t = str((s.get("data") or {}).get("text") or "").strip()
                                if t:
                                    parts.append(("text", t))
                            elif s.get("type") == "image":
                                url = str((s.get("data") or {}).get("url") or "")
                                parts.append(("image", url) if url.startswith("http") else ("text", "[图片]"))
                            elif s.get("type") == "video":
                                url = str((s.get("data") or {}).get("url") or (s.get("data") or {}).get("file") or "")
                                parts.append(("video", url) if url else ("text", "[视频]"))
                                out["texts"].append("[视频]")
                    elif str(content or "").strip():
                        parts.append(("text", str(content).strip()))
                    if parts:
                        line = "\n".join(p[1] for p in parts if p[0] == "text").strip()
                        out["nodes"].append({"name": name, "uin": uin, "parts": parts, "text": line})
                for t in node_texts(node):
                    out["texts"].append(t)
        elif st == "json":
            card = parse_card_json(data)
            if card:
                out["card_texts"].append(card)
            url = card_url_json(data)
            if url:
                out["urls"].append(url)
        elif st == "xml":
            card = parse_card_xml(data)
            if card:
                out["card_texts"].append(card)
            url = card_url_xml(data)
            if url:
                out["urls"].append(url)
    out["kind"] = ("forward" if out["forward_ids"] or out["nodes"] else
                   "card" if out["card_texts"] else
                   "image" if out["image_urls"] else
                   "text" if out["texts"] else "none")
    return out


def parse_segments(event) -> dict:
    """从原始 OneBot 事件里抽取 文本 / 图片 / 转发 / 卡片

    AstrBot 的 aiocqhttp 适配器会丢弃 forward 段，这里直接读 raw_message。
    返回 {kind, texts, image_urls, forward_ids, card_texts}
    """
    return parse_seg_list(_segments_of(event))


def node_texts(node) -> list[str]:
    """单个转发节点 → 文本列表"""
    if not isinstance(node, dict):
        return []
    data = node.get("data") or {}
    texts = []
    if isinstance(data.get("content"), list):
        for seg in data["content"]:
            if isinstance(seg, dict):
                if seg.get("type") == "text":
                    t = str((seg.get("data") or {}).get("text") or "").strip()
                    if t:
                        texts.append(t)
                elif seg.get("type") == "image":
                    texts.append("[图片]")
    elif str(data.get("content") or "").strip():
        texts.append(str(data["content"]).strip())
    sender = str(data.get("nickname") or data.get("name") or "").strip()
    if sender and texts:
        return [f"{sender}: {t}" for t in texts]
    return texts


CARD_URL_KEYS = ("jumpUrl", "jump_url", "qqdocurl", "url", "link", "srcUrl", "originUrl")


def card_url_json(data) -> str:
    """分享卡片（json 段）→ 原始链接（B站等）"""
    raw = data.get("data") or ""
    try:
        d = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return ""
    meta = d.get("meta") or {}
    if not isinstance(meta, dict):
        return ""
    for v in meta.values():
        if not isinstance(v, dict):
            continue
        for k in CARD_URL_KEYS:
            u = v.get(k)
            if isinstance(u, str) and u.startswith("http"):
                return u
        for sub in v.values():          # 有的卡片把链接塞在子对象里
            if isinstance(sub, dict):
                for k in CARD_URL_KEYS:
                    u = sub.get(k)
                    if isinstance(u, str) and u.startswith("http"):
                        return u
    return ""


def card_url_xml(data) -> str:
    raw = str(data.get("data") or "")
    if not raw:
        return ""
    m = re.search(r"""(?:url|brief|jumpUrl)\s*=\s*["'](https?://[^"']+)["']""", raw)
    if m:
        return m.group(1)
    m = re.search(r"(https?://[^\s<>\"']+)", raw)
    return m.group(1) if m else ""


def card_url(data) -> str:
    return card_url_json(data) or card_url_xml(data)


def parse_card_json(data) -> str:
    """分享卡片（json 段）→ 标题/摘要文本"""
    raw = data.get("data") or ""
    try:
        d = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return ""
    meta = d.get("meta") or {}
    for v in meta.values() if isinstance(meta, dict) else []:
        if not isinstance(v, dict):
            continue
        title = str(v.get("title") or "")
        desc = str(v.get("desc") or v.get("description") or "")
        if title or desc:
            return "\n".join(x for x in (title, desc) if x)
    return ""


def parse_card_xml(data) -> str:
    raw = str(data.get("data") or "")
    if not raw:
        return ""
    title = re.search(r"<title[^>]*>(.*?)</title>", raw, re.S)
    desc = re.search(r"<summary[^>]*>(.*?)</summary>", raw, re.S) or re.search(r"<desc[^>]*>(.*?)</desc>", raw, re.S)
    parts = [x.group(1).strip() for x in (title, desc) if x]
    return "\n".join(parts) if parts else ""


class AemeathChishiPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._last: dict[str, dict] = {}      # 会话 → 最近一份可搬内容
        self._last_ts: dict[str, float] = {}  # 会话 → 上面那份内容的时间戳
        self._seen: dict[str, float] = {}     # 搬运去重（同一消息+目标）
        self._seen_content: dict[str, float] = {}  # 内容指纹去重（跨群）
        self._feed_hour: dict[str, deque] = {}  # 群 → 最近搬运时间戳
        logger.info("[aemeath_chishi] 吃史仙人插件已加载")

    # ---------- 配置 ----------

    def _groups(self, key: str) -> set[str]:
        raw = str(self.config.get(key) or "")
        return {x for x in re.split(r"[,，\s]+", raw) if x}

    def _persona(self) -> str:
        custom = (self.config.get("persona") or "").strip()
        if custom:
            return custom
        try:
            p = __import__("pathlib").Path(__file__).resolve().parent.parent / \
                "astrbot_plugin_aemeath_persona" / "main.py"
            src = p.read_text(encoding="utf-8")
            m = re.search(r"^SYSTEM_PROMPT = (.+)$", src, re.M)
            if m:
                import ast as _ast
                return _ast.literal_eval(m.group(1))
        except Exception:  # noqa: BLE001
            pass
        return DEFAULT_PERSONA

    # ---------- 读取 ----------

    async def _parts_of_segs(self, event, segs, depth: int) -> tuple[list[str], list[str], list]:
        """把一条消息的段列表解析成 (文本行, 图片, parts)

        parts: [("text", 文本) / ("image", url) / ("nodes", 子节点)]
        """
        texts, images, parts = [], [], []
        for seg in segs or []:
            if not isinstance(seg, dict):
                continue
            t, d = seg.get("type"), seg.get("data") or {}
            if t == "text":
                txt = str(d.get("text") or "").strip()
                if txt:
                    texts.append(txt)
                    parts.append(("text", txt))
            elif t == "image":
                url = str(d.get("url") or "")
                if url.startswith("http"):
                    images.append(url)
                    parts.append(("image", url))
                else:
                    texts.append("[图片]")
                    parts.append(("text", "[图片]"))
            elif t == "video":
                url = str(d.get("url") or d.get("file") or "")
                if url:
                    parts.append(("video", url))
                else:
                    parts.append(("text", "[视频]"))
                texts.append("[视频]")
            elif t in ("forward", "node"):
                sub_lines, sub_images, sub_nodes = await self._nested_of(event, d, depth + 1)
                if sub_nodes:
                    texts.extend(sub_lines)
                    images.extend(sub_images)
                    if not any(p[0] != "text" for p in parts):
                        parts.append(("nodes", sub_nodes))
                    else:
                        parts.append(("text", "[内层转发]\n" + "\n".join(sub_lines) + "\n[内层转发完]"))
                else:
                    parts.append(("text", "[内层转发未展开]"))
        return texts, images, parts

    async def _nested_of(self, event, d: dict, depth: int) -> tuple[list[str], list[str], list[dict]]:
        """内层转发：优先内联 content；没有内联时按 id 拉（NapCat 对二级 id 会报 1200）

        content 实测有三种形态，逐项识别：
        - {"type":"node","data":{nickname,user_id,content:[消息段]}}
        - {"sender":{...},"message":[消息段]}   ← get_forward_msg 那种消息对象
        - 裸消息段 {"type":"text"/"image",...}
        """
        content = d.get("content")
        if isinstance(content, list) and content:
            lines, images, nodes = [], [], []
            loose: list = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "node":
                    nd = item.get("data") or {}
                    name = str(nd.get("nickname") or nd.get("name") or "群友")
                    uin = str(nd.get("user_id") or nd.get("uin") or "0")
                    segs = nd.get("content") or []
                elif "message" in item or "sender" in item:
                    sender = item.get("sender") or {}
                    name = str(sender.get("nickname") or sender.get("card") or "群友")
                    uin = str(sender.get("user_id") or "0")
                    segs = item.get("message") or item.get("content") or []
                else:
                    loose.append(item)
                    continue
                st, si, sp = await self._parts_of_segs(event, segs, depth)
                line = "\n".join(p[1] for p in sp if p[0] == "text").strip()
                if sp:
                    nodes.append({"name": name, "uin": uin, "parts": sp, "text": line})
                lines.extend(f"{name}: {x}" for x in st)
                images.extend(si)
            if loose:
                st, si, sp = await self._parts_of_segs(event, loose, depth)
                if sp:
                    line = "\n".join(p[1] for p in sp if p[0] == "text").strip()
                    nodes.append({"name": "内层记录", "uin": "0", "parts": sp, "text": line})
                lines.extend(st)
                images.extend(si)
            if not nodes:
                first = content[0] if isinstance(content[0], dict) else {}
                logger.info(
                    "[aemeath_chishi] 内层转发 content 解析为空，首项键=%s，段键=%s",
                    list(first.keys()), list(d.keys()),
                )
            return lines, images, nodes

        if isinstance(content, str) and content.strip():
            return [content.strip()], [], []

        fid = str(d.get("id") or "")
        if fid and depth < 2:
            ftext, fnodes, fimages = await self._fetch_forward(event, fid, depth)
            if fnodes:
                return ftext.splitlines(), fimages, fnodes
            logger.info(
                "[aemeath_chishi] 内层转发拉取失败（id=%s，段键=%s），按未展开处理",
                fid, list(d.keys()),
            )
        return [], [], []

    async def _fetch_forward(self, event, fid: str, depth: int = 0) -> tuple[str, list[dict], list[str]]:
        """用协议端 get_forward_msg 拉取转发内容 → (拼接文本, 节点列表, 图片列表)

        二级转发（套娃）：内联 content 直接解析；内层转发整段只有它时还原成嵌套卡片，
        和其他内容混排时摊成 [内层转发]…[内层转发完] 文字，避免变成 XML 卡片。
        """
        bot = getattr(event, "bot", None)
        if bot is None:
            return "", [], []
        try:
            data = await bot.call_action("get_forward_msg", id=fid)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[aemeath_chishi] 拉取转发失败: %s", exc)
            return "", [], []
        texts, images, nodes = [], [], []
        seen_types = set()
        for m in (data or {}).get("messages") or []:
            if not isinstance(m, dict):
                continue
            sender = (m.get("sender") or {}).get("nickname") or ""
            uin = str((m.get("sender") or {}).get("user_id") or "0")
            for seg in m.get("message") or []:
                if isinstance(seg, dict):
                    seen_types.add(str(seg.get("type")))
            st, si, parts = await self._parts_of_segs(event, m.get("message") or [], depth)
            if parts:
                line = "\n".join(p[1] for p in parts if p[0] == "text").strip()
                nodes.append({"name": sender or "群友", "uin": uin, "parts": parts, "text": line})
                texts.extend(f"{sender}: {x}" if sender else x for x in st)
                images.extend(si)
        if depth == 0:
            logger.info("[aemeath_chishi] 转发段类型: %s", sorted(seen_types))
        return "\n".join(texts), nodes, images

    async def _fetch_message(self, event, mid: str) -> list:
        """拉取被引用消息的原始段（用来取引用的转发/图片）"""
        bot = getattr(event, "bot", None)
        if bot is None:
            return []
        try:
            mid_val = int(mid)
        except (TypeError, ValueError):
            mid_val = mid
        try:
            data = await bot.call_action("get_msg", message_id=mid_val)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[aemeath_chishi] 拉取引用消息失败: %s", exc)
            return []
        segs = (data or {}).get("message") or []
        return [s for s in segs if isinstance(s, dict)]

    async def collect(self, event) -> dict | None:
        """收集本轮可吃的内容（含被引用的消息）；返回 None 表示没有"""
        parts = parse_segments(event)
        texts = list(parts["card_texts"]) + list(parts["texts"])
        images = list(parts["image_urls"])
        forward_ids = list(parts["forward_ids"])
        nodes: list[dict] = list(parts.get("nodes") or [])
        urls: list[str] = list(parts.get("urls") or [])

        # 引用的那条（比如「引用转发 + 搬到xxx」）也摊出来读
        rid = reply_id_of(event)
        if rid:
            quoted = parse_seg_list(await self._fetch_message(event, rid))
            texts = quoted["card_texts"] + quoted["texts"] + texts
            images = quoted["image_urls"] + images
            forward_ids = quoted["forward_ids"] + forward_ids
            nodes = quoted.get("nodes", []) + nodes
            urls = quoted.get("urls", []) + urls

        for fid in dict.fromkeys(forward_ids):
            ftext, fnodes, fimages = await self._fetch_forward(event, fid)
            if ftext:
                texts.insert(0, ftext)
            images.extend(fimages)
            nodes.extend(fnodes)

        texts = [t for t in texts if t.strip()]
        if not texts and not images:
            return None
        kind = (forward_ids and "forward") or (parts["card_texts"] and "card") or \
               (images and "image") or "text"
        body = "\n\n".join(texts)
        body = body[: int(self.config.get("maxChars", 60000))]
        result = {
            "kind": kind, "text": body, "nodes": nodes,
            "urls": list(dict.fromkeys(urls))[:3],
            "images": images[: int(self.config.get("maxImages", 6))],
        }
        # 去重种子（保证「原转发」和「引用该转发的回复」落到同一个指纹）：
        # 1) 有转发段 → 用转发段 id（原发/引用拿到的都是同一个 id）
        # 2) 只是引用普通消息 → 用被引用消息 id
        if forward_ids:
            result["fp_seed"] = f"fwd:{forward_ids[0]}"
        elif rid:
            result["fp_seed"] = f"quote:{rid}"
            # 引用轮次用被引用消息 id 当 mid，和自动搬史的 mid 对齐，避免同一份史搬两次
            result["mid"] = str(rid)
            # 引用原消息时优先让协议端直接转发这条消息（图片链接会过期，重新下载常失败）
            result["quote_mid"] = str(rid)
        return result

    # ---------- 评价 ----------

    async def _comment(self, material: dict) -> tuple[int, str, list[str]]:
        """返回 (等级, 评语, 主题标签列表)"""
        provider = self.context.get_using_provider()
        if provider is None:
            return 0, "", []
        chars = int(self.config.get("summaryChars", 40))
        system = self._persona() + TASK.format(chars=chars)
        prompt = f"待评价内容：\n{material['text'] or '（只有图片）'}"
        try:
            resp = await provider.text_chat(
                prompt=prompt,
                system_prompt=system,
                image_urls=material["images"] or None,
            )
            text = getattr(resp, "completion_text", "") or ""
        except Exception as exc:  # noqa: BLE001
            logger.warning("[aemeath_chishi] 评价失败: %s", exc)
            return 0, "", []
        level, comment = level_comment(text)
        return level, comment, parse_tag(text)

    # ---------- 搬运 ----------

    def _umo(self, event, kind: str, target: str) -> str | None:
        prefix = (event.unified_msg_origin or "aiocqhttp:GroupMessage:0").split(":", 1)[0]
        if kind == "group":
            return f"{prefix}:GroupMessage:{target}"
        if kind == "private":
            return f"{prefix}:FriendMessage:{target}"
        return None

    @staticmethod
    def _raw_segs(event) -> list:
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None) or {}
        segs = raw.get("message") if isinstance(raw, dict) else None
        return segs if isinstance(segs, list) else []

    def _image_files(self, event) -> list[str]:
        """当前消息里图片段的缓存文件名（NapCat 可直接按文件名发送，不用下载 URL）"""
        out = []
        for seg in self._raw_segs(event):
            if isinstance(seg, dict) and seg.get("type") == "image":
                name = str((seg.get("data") or {}).get("file") or "")
                if name:
                    out.append(name)
        return out

    async def _send(self, umo: str, text: str, images: list[str], urls: list[str] | None = None,
                    image_files: list[str] | None = None) -> bool:
        try:
            files = [f for f in (image_files or []) if f]
            tail = "\n".join(u for u in (urls or []) if u)[:500]
            body = f"{text}\n{tail}" if tail else text
            chain = [Comp.Plain(body)]
            # 有 NapCat 缓存文件名时优先用文件直传（URL 带时效，重新下载常 400）
            for f in files[:4]:
                try:
                    chain.append(Comp.Image(file=f))
                except Exception:  # noqa: BLE001
                    pass
            if len(chain) == 1:
                for url in images[:4]:
                    chain.append(Comp.Image.fromURL(url))
            await self.context.send_message(umo, MessageChain(chain=chain))
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("[aemeath_chishi] 发送失败: %s", exc)
            return False

    def _seen_recently(self, key: str) -> bool:
        now = time.time()
        window = float(self.config.get("dedupeMinutes", 5)) * 60
        self._seen = {k: v for k, v in self._seen.items() if now - v < window}
        return key in self._seen

    def _mark_seen(self, key: str) -> None:
        self._seen[key] = time.time()

    def _feed_quota(self, gid: str) -> bool:
        now = time.time()
        dq = self._feed_hour.setdefault(gid, deque())
        while dq and now - dq[0] > 3600:
            dq.popleft()
        if len(dq) >= int(self.config.get("feedMaxPerHour", 20)):
            return False
        dq.append(now)
        return True

    # ---------- 内容级去重（防止同一份史在多个群各搬一遍） ----------

    def _fingerprint(self, material: dict) -> str:
        seed = material.get("fp_seed")
        if seed:
            return hashlib.sha1(str(seed).encode("utf-8", "ignore")).hexdigest()
        text = re.sub(r"\s+", "", material.get("text") or "")
        nodes = "|".join(re.sub(r"\s+", "", str(n.get("text") or "")) for n in material.get("nodes") or [])
        imgs = "|".join(sorted(material.get("images") or []))
        urls = "|".join(sorted(material.get("urls") or []))
        raw = f"{text[:800]}#{nodes[:800]}#{imgs}#{urls[:300]}"
        return hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()

    def _is_bili(self, material: dict) -> bool:
        """是否是 B站内容（链接域名或卡片标题含哔哩哔哩）"""
        for u in material.get("urls") or []:
            if "bilibili.com" in u or "b23.tv" in u:
                return True
        text = material.get("text") or ""
        return "哔哩哔哩" in text or "bilibili" in text.lower()

    def _content_dup(self, fp: str) -> bool:
        now = time.time()
        window = float(self.config.get("dupWindowMinutes", 60)) * 60
        if window <= 0:
            return False
        self._seen_content = {k: v for k, v in self._seen_content.items() if now - v < window}
        return fp in self._seen_content

    def _mark_content(self, fp: str) -> None:
        self._seen_content[fp] = time.time()

    async def _auto_feed(self, event, material: dict, gid: str) -> None:
        """自动搬运入口：去处/内容去重/配额统一在这里判断"""
        groups = [g for g in self._groups("feedGroups") if g != gid]
        if not groups:
            return
        fp = self._fingerprint(material)
        if self._content_dup(fp):
            logger.info("[aemeath_chishi] 相同内容已在窗口内搬过（%s），跳过", fp[:8])
            return
        if not self._feed_quota(gid or "p"):
            logger.info("[aemeath_chishi] 本轮已达每小时搬运上限，跳过")
            return
        moved = False
        for tg in groups:
            moved = await self.move(event, "group", tg, material) or moved
        if moved:
            self._mark_content(fp)

    def _build_node_list(self, nodes: list[dict], depth: int = 0, allow_video: bool = True) -> list:
        """把节点字典转成 Comp.Node 列表（支持二级转发嵌套）"""
        comps = []
        for n in nodes[:30]:
            parts = n.get("parts") or ([("text", n.get("text") or "")] if n.get("text") else [])
            content = []
            for kind, val in parts:
                if kind == "nodes" and depth < 3 and isinstance(val, list):
                    content.extend(self._build_node_list(val, depth + 1, allow_video))
                    continue
                val = str(val or "")
                if kind == "image" and val.startswith("http"):
                    content.append(Comp.Image.fromURL(val))
                elif kind == "video" and val:
                    if allow_video:
                        try:
                            content.append(Comp.Video.fromURL(val))
                            continue
                        except Exception:  # noqa: BLE001
                            pass
                    content.append(Comp.Plain("[视频]"))
                elif val.strip():
                    content.append(Comp.Plain(val[:500]))
            if content:
                comps.append(Comp.Node(
                    content=content,
                    name=str(n.get("name") or "群友")[:32],
                    uin=str(n.get("uin") or "0"),
                ))
        return comps

    async def _send_forward(self, umo: str, nodes: list[dict]) -> bool:
        """把节点列表以「合并转发」形式发送（视频/图片随节点携带；视频发不出去就退化成 [视频]）"""
        for allow_video in (True, False):
            try:
                comps = self._build_node_list(nodes, allow_video=allow_video)
                if not comps:
                    return False
                await self.context.send_message(umo, MessageChain(chain=[Comp.Nodes(comps)]))
                return True
            except Exception as exc:  # noqa: BLE001
                logger.warning("[aemeath_chishi] 转发发送失败（video=%s）: %s", allow_video, exc)
        return False

    async def move(self, event, kind: str, target: str, material: dict) -> bool:
        """搬一份内容；返回是否成功（有转发节点时发合并转发，否则发文字+图片）"""
        umo = self._umo(event, kind, target)
        if not umo:
            return False
        key = f"{material.get('mid')}|{umo}"
        if self._seen_recently(key):
            return False
        nodes = material.get("nodes") or []
        urls = material.get("urls") or []
        mid = str(material.get("mid") or "")
        quote_mid = str(material.get("quote_mid") or "")
        cmd_mid = str(getattr(getattr(event, "message_obj", None), "message_id", "") or "")
        # 这条消息本身就是指令（图片配「这条也搬」）时不能原生转发，否则会把指令一起搬走
        same_as_cmd = bool(mid) and mid == cmd_mid
        bot = getattr(event, "bot", None)
        can_native = bool(kind == "group" and mid.isdigit() and target.isdigit()
                          and bot is not None and not same_as_cmd)
        ok = False

        async def forward_original(message_id: str, tag: str) -> bool:
            """让协议端直接转发原消息：图片链接会过期，重新下载常 400，这是唯一保真的路"""
            if bot is None or not message_id.isdigit():
                return False
            if kind == "group" and target.isdigit():
                action, payload = "forward_group_single_msg", {"group_id": int(target)}
            elif kind == "private" and target.isdigit():
                action, payload = "forward_friend_single_msg", {"user_id": int(target)}
            else:
                return False
            payload["message_id"] = int(message_id)
            try:
                await bot.call_action(action, **payload)
                logger.info("[aemeath_chishi] 已原生转发消息 %s → %s %s（%s）", message_id, kind, target, tag)
                return True
            except Exception as exc:  # noqa: BLE001
                logger.info("[aemeath_chishi] 原生转发%s失败: %s", tag, exc)
                return False

        # 引用别人消息时：直接把被引用的原消息转发过去，最保真（转发卡片/图片/视频都不会丢）
        if quote_mid and (nodes or material.get("images") or material.get("kind") in ("forward", "image", "card")):
            if await forward_original(quote_mid, "引用原消息"):
                self._mark_seen(key)
                return True

        async def try_native(tag: str) -> bool:
            if not can_native:
                return False
            try:
                await bot.call_action(
                    "forward_group_single_msg",
                    group_id=int(target),
                    message_id=int(mid),
                )
                logger.info("[aemeath_chishi] 已用原生转发把消息 %s 搬到群 %s（%s）", mid, target, tag)
                return True
            except Exception as exc:  # noqa: BLE001
                logger.info("[aemeath_chishi] 原生转发%s失败: %s", tag, exc)
                return False

        # 有转发节点时首选原生转发：视频/文件这类没法重新上传的内容只有这条路保真
        if nodes and await try_native("首选"):
            self._mark_seen(key)
            return True
        if nodes:
            ok = await self._send_forward(umo, nodes)
        # 纯图消息：QQ 图片链接带时效，重新上传经常 400，直接让协议端转发原消息
        has_text = bool((material.get("text") or "").strip())
        if not ok and not nodes and material.get("images") and not has_text:
            ok = await try_native("纯图")
        if not ok:
            text = material["text"][:4000]
            ok = await self._send(umo, f"【搬史】\n{text}", material["images"], urls,
                                  material.get("image_files"))
            if not ok:
                ok = await try_native("兜底")
        if ok:
            self._mark_seen(key)
        return ok

    # ---------- 主流程 ----------

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        if not self.config.get("enabled", True):
            return
        gid = str(event.get_group_id() or "")
        sender = str(getattr(event, "get_sender_id", lambda: "")())
        if gid and sender == str(getattr(event, "get_self_id", lambda: "")()):
            return

        text = (event.message_str or "").strip()
        mv = match_move(text)
        wake = bool(getattr(event, "is_at_or_wake_command", False))
        # 观察白名单：不在名单里就不主动吃；但主人叫搬仍然照办
        watch = self._groups("watchGroups")
        if gid and watch and gid not in watch and not mv:
            return
        # @ 机器人的消息默认走正常对话，只有「搬运指令」才由本插件接管
        if wake and not mv:
            # 私聊里没有 @ 概念，随手转来的东西先记下来，方便下一条「这条也搬」
            if not gid:
                got = await self.collect(event) or {}
                if got.get("text") or got.get("images") or got.get("nodes"):
                    got.setdefault("mid", str(
                        getattr(getattr(event, "message_obj", None), "message_id", "") or uuid.uuid4().hex[:12]))
                    key = str(event.unified_msg_origin)
                    self._last[key] = got
                    self._last_ts[key] = time.time()
            return

        material = await self.collect(event)
        material = material or {}
        if not material.get("mid"):
            material["mid"] = str(getattr(getattr(event, "message_obj", None), "message_id", "") or uuid.uuid4().hex[:12])

        # 1) 搬运指令
        if mv:
            # 搬运轮次不把指令本身当正文
            if material.get("text"):
                keep = [p for p in material["text"].split("\n\n") if p.strip() and not match_move(p)]
                material["text"] = "\n\n".join(keep)
            # 图片配指令一起发时，把图片的缓存文件名带上，避免重新下载 URL 失败
            files = self._image_files(event)
            if files:
                material["image_files"] = files
            kind, target = mv
            owner = str(self.config.get("ownerQQ") or "").strip()
            if gid and owner and sender != owner:
                # 群里非主人叫搬：不理
                return
            if kind == "self":
                target = str(self.config.get("feedPrivateTarget") or "").strip()
                kind = "private" if target else "feed"
            # 解析目标列表：feedGroups 全部（排除来源群），或指定单群/私聊
            targets: list[str] = []
            if kind == "feed":
                targets = [g for g in self._groups("feedGroups") if g != gid]
                if not targets:
                    yield event.plain_result("没有设搬史白名单（feedGroups），搬不了")
                    return
                kind = "group"
            elif kind == "group":
                if not target:
                    return
                targets = [target]
            elif kind == "private":
                if not target:
                    return
                targets = [target]
            else:
                return
            real = material.get("kind") in ("forward", "card", "image") and (
                material.get("text") or material.get("images"))
            cache_key = gid or str(event.unified_msg_origin)
            use = material if real else self._last.get(cache_key, {})
            # 缓存超过 15 分钟就不再当「这条」用，避免引用无关消息时把很久以前的内容又搬一遍
            if not real and use:
                stamp = float(self._last_ts.get(cache_key) or 0)
                if time.time() - stamp > 15 * 60:
                    use = {}
            if not (use.get("text") or use.get("images")):
                yield event.plain_result("这条没有可搬的内容，引用要搬的转发或图片再说一次")
                return
            sent = 0
            for tg in targets:
                if await self.move(event, kind, tg, use):
                    sent += 1
            if sent:
                if kind == "private" or len(targets) == 1:
                    yield event.plain_result("搬过去了")
                else:
                    yield event.plain_result(f"搬到 {sent} 个群了")
            else:
                yield event.plain_result("没搬成：这条刚才已经搬过了，或者目标群无效")
            event.stop_event()
            return

        if not material.get("text") and not material.get("images"):
            return
        if material.get("kind") in ("none", "text") and not material.get("images"):
            return
        # 纯图片（截图/表情包）：默认不吃，需显式开启；图文混排的消息按普通聊天处理
        if material.get("kind") == "image":
            if not self.config.get("eatImages", False):
                return
            if material.get("text"):
                return

        # 记录最近一份可搬内容
        key = gid or str(event.unified_msg_origin)
        self._last[key] = material
        self._last_ts[key] = time.time()

        # 2) 评价
        comment_groups = self._groups("commentGroups")
        if not self.config.get("commentEnabled", True):
            return
        if gid and comment_groups and gid not in comment_groups:
            return

        level, comment, tag = await self._comment(material)
        if not comment:
            return
        logger.info("[aemeath_chishi] 内容判定：标签=%s 等级=%s", ",".join(tag) or "-", level)
        yield event.plain_result(comment)
        event.stop_event()

        # 3) 自动搬运
        # 0) 硬拦截：R18 等禁止搬运的标签，优先级最高（强制搬运也拦）
        skip_tags = str(self.config.get("feedSkipTags") or "")
        skip_hit = next((t for t in tag if in_list(skip_tags, t)), "")
        if not skip_hit:
            skip_hit = hit_keywords(str(self.config.get("feedSkipKeywords") or ""),
                                    material.get("text") or "")
        if skip_hit:
            logger.info("[aemeath_chishi] 命中禁止搬运「%s」，不搬", skip_hit)
            return

        # 指定群只搬「抽象」：这些来源群里，只有抽象标签才搬
        only_groups = str(self.config.get("abstractOnlyGroups") or "")
        if gid and in_list(only_groups, gid):
            if not in_list("抽象", *tag):
                logger.info("[aemeath_chishi] 群 %s 只搬抽象，标签「%s」不搬", gid, ",".join(tag) or "-")
                return
            logger.info("[aemeath_chishi] 群 %s 抽象内容，搬运", gid)
            await self._auto_feed(event, material, gid)
            return

        # B站卡片：默认不自动搬（容易刷屏/搬一堆无关视频），只在手动「这条也搬」时发
        if self._is_bili(material):
            if not self.config.get("biliAutoFeed", False):
                logger.info("[aemeath_chishi] B站卡片默认不自动搬，跳过（需手动搬）")
                return
            logger.info("[aemeath_chishi] B站卡片允许自动搬，继续后续规则")

        # 强制搬运（独立于等级/概率/黑名单）：模型主题标签里任一个命中 feedForceTags，
        # 或文本命中 feedForceKeywords → 直接搬，不看 Lv、不看概率、不看黑名单
        force_tags = str(self.config.get("feedForceTags") or "")
        force = any(in_list(force_tags, t) for t in tag)
        if force:
            logger.info("[aemeath_chishi] 主题标签「%s」强制搬", ",".join(tag))
        if not force:
            hit = hit_keywords(str(self.config.get("feedForceKeywords") or ""), material.get("text") or "")
            if hit:
                force = True
                logger.info("[aemeath_chishi] 命中强制关键词「%s」，直接搬", hit)
        if force:
            await self._auto_feed(event, material, gid)
            return
        # 常规搬运：黑名单里的群或用户不自动搬
        if in_list(str(self.config.get("feedBlockList") or ""), gid, sender):
            return
        if level >= int(self.config.get("feedMinLevel", 3)) and random.random() < float(
            self.config.get("feedProbability", 0.3)
        ):
            await self._auto_feed(event, material, gid)
