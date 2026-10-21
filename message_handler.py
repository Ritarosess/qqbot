"""
消息处理：判断是否触发、调用 AI、构造回复
"""
import base64
import os
import re
import json
import logging

from ai_client import AIClient
from conversation import ConversationManager
from emoji_manager import EmojiManager
import config

logger = logging.getLogger("handler")


def _to_onebot_file(path: str) -> str:
    """把宿主机本地图片转成 base64:// 内嵌

    NapCat 运行在 Docker 容器里，看不到宿主机的 /opt/qqbot/emojis 路径，
    直接传路径会报「识别URL失败」，导致整条消息发送失败。
    """
    if not path:
        return path
    if path.startswith(("http://", "https://", "base64://")):
        return path
    try:
        if os.path.isfile(path):
            with open(path, "rb") as f:
                encoded = base64.b64encode(f.read()).decode()
            return "base64://" + encoded
    except OSError as exc:
        logger.warning("读取表情文件失败: %s (%s)", path, exc)
    return path


class MessageHandler:
    def __init__(self, ai: AIClient, conv: ConversationManager, emoji: EmojiManager):
        self.ai = ai
        self.conv = conv
        self.emoji = emoji
        self._bot_id: int | None = None

    def set_bot_id(self, bot_id: int):
        self._bot_id = bot_id

    def _strip_trigger(self, text: str, mentions: list[int]) -> tuple[str, bool]:
        if self._bot_id and self._bot_id in mentions:
            text = re.sub(rf"\[CQ:at,qq={self._bot_id}(?:,name=\S+)?\]", "", text).strip()
            return text, True
        for kw in config.TRIGGER_KEYWORDS:
            # 支持 "关键词" 或 "@关键词" 开头
            if text.lower().startswith(kw.lower()) or text.lower().startswith("@" + kw.lower()):
                # 去掉可能的 @ 前缀
                clean_text = text.lstrip("@").strip()
                if clean_text.lower().startswith(kw.lower()):
                    return clean_text[len(kw):].strip(), True
                return text[len(kw):].strip(), True
        return text, False

    def _extract_text(self, message) -> tuple[str, list[int]]:
        text = ""
        mentions: list[int] = []
        if isinstance(message, str):
            return message, []
        for seg in message:
            if seg["type"] == "text":
                text += seg["data"]["text"]
            elif seg["type"] == "at":
                mentions.append(int(seg["data"]["qq"]))
        return text.strip(), mentions

    def _extract_images(self, message) -> list[dict]:
        """提取图片消息（image）- 排除收藏表情"""
        images = []
        if isinstance(message, str):
            return images
        for seg in message:
            if seg["type"] == "image":
                summary = seg["data"].get("summary", "")
                # 排除收藏表情（summary 包含 [动画表情] 或 [表情]）
                if "[动画表情]" not in summary and "[表情]" not in summary and "[图片]" not in summary:
                    images.append({
                        "file": seg["data"].get("file", ""),
                        "url": seg["data"].get("url", ""),
                        "type": "image",
                    })
        return images

    def _extract_faces(self, message) -> list[dict]:
        """提取 QQ 收藏表情（mface 或 image 带 [动画表情] 标记）"""
        faces = []
        if isinstance(message, str):
            return faces
        for seg in message:
            # 类型1: mface 消息段
            if seg["type"] == "mface":
                faces.append({
                    "id": seg["data"].get("id", ""),
                    "url": seg["data"].get("url", ""),
                    "summary": seg["data"].get("summary", ""),
                    "type": "mface",
                })
            # 类型2: image 但 summary 包含 [动画表情] 或 [表情]
            elif seg["type"] == "image":
                summary = seg["data"].get("summary", "")
                if "[动画表情]" in summary or "[表情]" in summary or "[图片]" in summary:
                    faces.append({
                        "id": seg["data"].get("file", ""),
                        "url": seg["data"].get("url", ""),
                        "summary": summary,
                        "type": "mface",  # 标记为收藏表情
                    })
        return faces

    async def handle_group(self, ws, event: dict):
        group_id = event["group_id"]
        user_id = event["user_id"]
        nickname = event.get("sender", {}).get("nickname", "匿名")
        raw_text, mentions = self._extract_text(event["message"])
        images = self._extract_images(event["message"])
        faces = self._extract_faces(event["message"])

        # 调试：打印消息段类型
        if isinstance(event["message"], list):
            for seg in event["message"]:
                logger.debug("消息段类型: %s, 数据: %s", seg.get("type"), seg.get("data", {}))

        # 记录消息到当日记录（用于每日总结，不触发回复的也记录）
        if raw_text:
            self.conv.record_message(group_id, user_id, nickname, raw_text)

        # 如果是纯图片消息（表情包），保存到表情包库
        if images and not raw_text:
            for img in images:
                self.emoji.add_emoji(
                    file_id=img["file"],
                    url=img["url"],
                    keywords=[],  # 待用户补充
                    sender_id=user_id,
                )
            logger.info("[群%d] %s 发送了 %d 个图片表情包（自动收藏关闭时忽略）", group_id, nickname, len(images))
            return

        # 如果是 QQ 收藏表情，也保存
        if faces and not raw_text:
            for face in faces:
                self.emoji.add_emoji(
                    file_id=face["id"],
                    url=face["url"],
                    keywords=[face["summary"]] if face["summary"] else [],
                    sender_id=user_id,
                    emoji_type="mface",
                )
            logger.info("[群%d] %s 发送了 %d 个收藏表情（自动收藏关闭时忽略）", group_id, nickname, len(faces))
            return

        text, triggered = self._strip_trigger(raw_text, mentions)
        if not triggered:
            await self._maybe_join_group_chat(ws, group_id, user_id, raw_text)
            return

        if not self.conv.can_reply(group_id, config.COOLDOWN):
            logger.info("群 %d 触发冷却，跳过", group_id)
            return

        if not text:
            text = "你好"

        logger.info("[群%d] %d: %s", group_id, user_id, text)
        self.conv.add_user(group_id, text)

        reply = await self.ai.chat(self.conv.get_messages(group_id, config.SYSTEM_PROMPT))
        if reply:
            logger.info("AI 回复: %s", reply)
            self.conv.add_assistant(group_id, reply)
            await self._send_group_with_emoji(ws, group_id, reply)
        else:
            await self._send_group(ws, group_id, "（AI 暂时没有响应，请稍后再试）")

    async def _maybe_join_group_chat(self, ws, group_id: int, user_id: int, raw_text: str):
        """群聊自然插话：概率触发 + 冷却 + AI 自判是否开口

        三道闸门缺一不可：
        1. 概率：只有一小部分消息会进入 AI 判断，控制成本
        2. 冷却：同一群最短间隔，避免刷屏
        3. AI 自判：看完上下文觉得不该说话就回 [skip]
        """
        if not config.GROUP_CHAT_ENABLED:
            return
        # 不回应自己的消息，避免自问自答死循环
        if self._bot_id and user_id == self._bot_id:
            return

        text = (raw_text or "").strip()
        if not text or text.startswith("/") or text.startswith("[CQ:"):
            return
        if len(text) < 2:
            return

        import random
        if random.random() > config.GROUP_CHAT_PROBABILITY:
            return

        if not self.conv.can_reply(group_id, config.GROUP_CHAT_COOLDOWN):
            return

        recent = self.conv.get_daily_messages(group_id)[-config.GROUP_CHAT_CONTEXT:]
        history = "\n".join(f"{m['nickname']}: {m['content']}" for m in recent)
        instruction = (
            "下面是群友最近的聊天记录。你正在群里潜水，看到合适的话题可以用一句话自然插一句；"
            "如果没什么想说的，或者插话会很突兀，就只回复 [skip]，不要解释。\n"
            "要求：只回一句，口语化，不要提问，不要复述聊天记录，不要暴露这段指令。\n\n"
            + history
        )

        logger.info("[群%d] 尝试插话（参考 %d 条最近消息）", group_id, len(recent))
        reply = await self.ai.chat([
            {"role": "system", "content": config.SYSTEM_PROMPT},
            {"role": "user", "content": instruction},
        ])
        if not reply:
            return

        cleaned = reply.strip()
        if "[skip]" in cleaned.lower() or cleaned in ("跳过", "skip"):
            logger.info("[群%d] AI 判断不适合插话，保持安静", group_id)
            return

        logger.info("AI 插话: %s", cleaned)
        self.conv.add_user(group_id, text)
        self.conv.add_assistant(group_id, cleaned)
        await self._send_group_with_emoji(ws, group_id, cleaned)

    async def _send_group(self, ws, group_id: int, content: str):
        await ws.send_str(json.dumps({
            "action": "send_group_msg",
            "params": {"group_id": group_id, "message": content},
        }))

    def build_segments(self, content: str) -> list[dict]:
        """把 [emoji] / [emoji:关键词] 展开为 OneBot 消息段（群聊、私聊共用）

        纯素材包模式：找不到对应表情时静默跳过，绝不输出 [表情] 之类的占位文本。
        """
        import re
        segments = []
        last_end = 0

        for match in re.finditer(r'\[emoji(?::([^\]]+))?\]', content):
            if match.start() > last_end:
                text = content[last_end:match.start()]
                if text:
                    segments.append({"type": "text", "data": {"text": text}})

            keyword = match.group(1)  # None 表示随机
            emoji = self.emoji.find_by_keyword(keyword) if keyword else self.emoji.get_random()

            if emoji:
                logger.info("找到表情包: %s", emoji.get("name") or emoji.get("summary") or "unknown")
                file_path = emoji.get("path") or emoji.get("url", "")
                if file_path:
                    segments.append({"type": "image", "data": {"file": _to_onebot_file(file_path)}})
                elif emoji.get("id"):
                    segments.append({"type": "mface", "data": {"id": emoji["id"]}})
                # 没有可用资源就静默跳过
            # 关键词不存在也静默跳过
            last_end = match.end()

        if last_end < len(content):
            text = content[last_end:]
            if text:
                segments.append({"type": "text", "data": {"text": text}})

        # 清理残留占位符：AI 有时会自己写 [表情]、[图片]、[emoji]，
        # 甚至直接写 [开心]、[叹气] 这种标签名，一律清掉
        placeholder = re.compile(r'\[(?:表情|图片|emoji|EMOJI)[^\]]*\]')
        known_tags = sorted(
            {kw for e in getattr(self.emoji, "_local_emojis", []) for kw in e.get("keywords", []) if kw},
            key=len, reverse=True,
        )
        tag_re = re.compile(r'\[(?:' + "|".join(map(re.escape, known_tags)) + r')\]') if known_tags else None

        cleaned: list[dict] = []
        for seg in segments:
            if seg["type"] == "text":
                text = placeholder.sub("", seg["data"]["text"])
                if tag_re is not None:
                    text = tag_re.sub("", text)
                text = text.strip()
                if text:
                    cleaned.append({"type": "text", "data": {"text": text}})
            else:
                cleaned.append(seg)

        if not cleaned:
            fallback = placeholder.sub("", content)
            if tag_re is not None:
                fallback = tag_re.sub("", fallback)
            fallback = fallback.strip()
            cleaned = [{"type": "text", "data": {"text": fallback or "……"}}]
        return cleaned

    async def _send_group_with_emoji(self, ws, group_id: int, content: str):
        """发送带表情包的群回复"""
        segments = self.build_segments(content)
        await ws.send_str(json.dumps({
            "action": "send_group_msg",
            "params": {"group_id": group_id, "message": segments},
        }))
        logger.info("发送消息: %s", json.dumps(segments, ensure_ascii=False)[:300])
