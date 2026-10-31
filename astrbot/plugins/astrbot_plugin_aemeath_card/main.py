"""
爱弥斯 · 卡片消息解析插件

QQ 分享卡片（B站视频/音乐、小程序等）是 JSON/XML 消息段，AstrBot 默认提取不到文字，
消息字符串为空，LLM 阶段会直接跳过。本插件在消息处理前把卡片里的标题和链接拼成文本，
让后续对话能正常进行。

优先级 100：早于其他插件和默认流程执行。
"""
import json
import re

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star

URL_KEYS = ("jumpUrl", "qqdocurl", "url", "link", "jump_url")
TITLE_KEYS = ("title", "desc", "summary", "brief")


def _find(obj, keys):
    """递归查找命中的键，返回第一个非空字符串"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys and isinstance(v, str) and v.strip():
                return v.strip()
        for v in obj.values():
            hit = _find(v, keys)
            if hit:
                return hit
    elif isinstance(obj, list):
        for item in obj:
            hit = _find(item, keys)
            if hit:
                return hit
    return None


def _from_json(payload: str):
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        return None
    title = _find(data, TITLE_KEYS)
    url = _find(data, URL_KEYS)
    if not title and not url:
        return None
    parts = ["[分享卡片]"]
    if title:
        parts.append(title)
    if url:
        parts.append(url)
    return " ".join(parts)


def _from_xml(payload: str):
    title = re.search(r"<title>(.*?)</title>", payload, re.S)
    summary = re.search(r"<summary>(.*?)</summary>", payload, re.S)
    url = re.search(r"<url>(.*?)</url>", payload, re.S)
    text = (title or summary)
    if not text and not url:
        return None
    parts = ["[分享卡片]"]
    if text:
        parts.append(text.group(1).strip())
    if url:
        parts.append(url.group(1).strip())
    return " ".join(parts)


class AemeathCardPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        logger.info("[aemeath_card] 卡片解析插件已加载")

    @filter.event_message_type(filter.EventMessageType.ALL, priority=100)
    async def parse_card(self, event: AstrMessageEvent):
        """把 JSON/XML 卡片消息拼成文本，避免空消息被跳过"""
        if not self.config.get("enabled", True):
            return
        if (event.message_str or "").strip():
            return  # 已有文字，无需处理

        raw = getattr(event, "message_obj", None)
        if raw is None:
            return

        text = None
        raw_msg = getattr(raw, "raw_message", None)
        if raw_msg is None:
            return

        segments = getattr(raw_msg, "message", None) or []
        if isinstance(segments, list):
            for seg in segments:
                if not isinstance(seg, dict):
                    continue
                seg_type = seg.get("type")
                data = seg.get("data") or {}
                payload = data.get("data") or data.get("content") or ""
                if not isinstance(payload, str) or not payload.strip():
                    continue
                if seg_type == "json":
                    text = _from_json(payload)
                elif seg_type == "xml":
                    text = _from_xml(payload)
                if text:
                    break

        # 兜底：raw_message 字符串中的 CQ 码
        if not text:
            raw_str = getattr(raw_msg, "raw_message", "") or ""
            m = re.search(r"\[CQ:json,data=(.*?)\]", raw_str, re.S)
            if m:
                text = _from_json(m.group(1))
            if not text:
                m = re.search(r"\[CQ:xml,data=(.*?)\]", raw_str, re.S)
                if m:
                    text = _from_xml(m.group(1))

        if not text:
            return

        text = text[: int(self.config.get("max_chars", 300))]
        event.message_str = text
        if getattr(event, "message_obj", None) is not None:
            try:
                event.message_obj.message_str = text
                event.message_obj.message.append(Plain(text))
            except Exception:  # noqa: BLE001
                pass
        logger.info("[aemeath_card] 解析卡片: %s", text[:80])
