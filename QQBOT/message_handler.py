"""
消息处理：判断是否触发、调用 AI、构造回复
"""
import re
import json
import logging

from ai_client import AIClient
from conversation import ConversationManager
from emoji_manager import EmojiManager
import config

logger = logging.getLogger("handler")


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
            logger.info("[群%d] %s 发送了 %d 个图片表情包，已保存", group_id, nickname, len(images))
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
            logger.info("[群%d] %s 发送了 %d 个收藏表情，已保存", group_id, nickname, len(faces))
            return

        text, triggered = self._strip_trigger(raw_text, mentions)
        if not triggered:
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

    async def _send_group(self, ws, group_id: int, content: str):
        await ws.send_str(json.dumps({
            "action": "send_group_msg",
            "params": {"group_id": group_id, "message": content},
        }))

    async def _send_group_with_emoji(self, ws, group_id: int, content: str):
        """发送带表情包的回复，支持 [emoji] 或 [emoji:关键词] 语法"""
        import re
        segments = []
        last_end = 0

        # 匹配 [emoji] 或 [emoji:关键词] 格式
        for match in re.finditer(r'\[emoji(?::([^\]]+))?\]', content):
            # 添加前面的文本
            if match.start() > last_end:
                text = content[last_end:match.start()]
                if text:
                    segments.append({"type": "text", "data": {"text": text}})

            # 查找并添加表情包
            keyword = match.group(1)  # 可能是 None（随机）
            if keyword:
                emoji = self.emoji.find_by_keyword(keyword)
            else:
                emoji = self.emoji.get_random()

            if emoji:
                logger.info("找到表情包: type=%s, url=%s, path=%s", emoji.get("type"), emoji.get("url"), emoji.get("path"))
                if emoji["type"] in ("local", "image"):
                    # 发送本地图片或群聊图片文件（用 URL）
                    url = emoji.get("url") or emoji.get("path", "")
                    if url:
                        segments.append({"type": "image", "data": {"file": url}})
                    else:
                        segments.append({"type": "text", "data": {"text": "[图片]"}})
                elif emoji["type"] in ("qq_face", "mface"):
                    # 发送 QQ 收藏表情 - 使用 image 类型 + URL/path
                    url = emoji.get("url") or emoji.get("path", "")
                    if url:
                        segments.append({"type": "image", "data": {"file": url}})
                    else:
                        # 没有 URL，尝试用 file 作为 ID 发送 mface
                        face_id = emoji.get("id", "")
                        if face_id:
                            segments.append({"type": "mface", "data": {"id": face_id}})
                        else:
                            segments.append({"type": "text", "data": {"text": "[表情]"}})
            else:
                # 没找到，保留文本提示
                tag = keyword if keyword else "表情"
                segments.append({"type": "text", "data": {"text": f"[{tag}]"}})
            last_end = match.end()

        # 添加剩余文本
        if last_end < len(content):
            text = content[last_end:]
            if text:
                segments.append({"type": "text", "data": {"text": text}})

        # 如果没有匹配到表情，直接发送文本
        if not segments:
            segments = [{"type": "text", "data": {"text": content}}]

        await ws.send_str(json.dumps({
            "action": "send_group_msg",
            "params": {"group_id": group_id, "message": segments},
        }))
        logger.info("发送消息: %s", json.dumps(segments, ensure_ascii=False))
