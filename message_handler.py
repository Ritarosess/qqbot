"""
消息处理：判断是否触发、调用 AI、构造回复
"""
import base64
import os
import random
import re
import time
import json
import logging

from ai_client import AIClient
from conversation import ConversationManager
from emoji_manager import EmojiManager
import config
import whitelist

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
    def __init__(self, ai: AIClient, conv: ConversationManager, emoji: EmojiManager, tts=None):
        self.ai = ai
        self.conv = conv
        self.emoji = emoji
        self.tts = tts  # TTS 实例（CosyVoice / MiniMax 后端）
        self._bot_id: int | None = None
        self._voice_last: dict[tuple[str, int], float] = {}

    def set_bot_id(self, bot_id: int):
        self._bot_id = bot_id

    # ===== 语音触发 =====

    def match_voice_trigger(self, text: str) -> tuple[str, str] | None:
        """匹配语音触发前缀，返回 (前缀, 待朗读内容)；未命中返回 None。

        规则：
        - 多字前缀（语音/朗读/读一下/用语音说...）可直接跟内容
        - 单字前缀（说/念/读/唱）必须带分隔符（空格、冒号、逗号等），
          避免「说明一下」「读书」这类普通句子被误触发
        """
        text = (text or "").strip()
        for prefix in sorted(config.VOICE_TRIGGER_PREFIXES, key=len, reverse=True):
            if not text.startswith(prefix):
                continue
            rest = text[len(prefix):]
            lead = rest.lstrip(" 　:：,，.。!！~～\"“”'‘’")
            if len(lead) < len(rest):      # 前缀后带分隔符
                return prefix, lead.strip()
            if len(prefix) >= 2:           # 多字前缀可直接接内容
                return prefix, rest.strip()
            return None                    # 单字且无分隔符 → 不触发
        return None

    def voice_cooling(self, scope: str, key: int) -> bool:
        """语音冷却检查：True = 还在冷却中，应跳过"""
        now = time.time()
        stamp = (scope, key)
        if now - self._voice_last.get(stamp, 0.0) < config.TTS_COOLDOWN:
            return True
        self._voice_last[stamp] = now
        return False

    def _voice_text_of(self, content: str) -> str:
        """把 AI 回复清洗成适合朗读的纯文本：去掉表情占位符和方括号标签"""
        text = re.sub(
            r"\[(?:emoji|表情|图片|image|img|贴图)[^\]]*\]", "", content, flags=re.IGNORECASE
        )
        text = re.sub(r"\[[^\[\]]{0,12}\]", "", text)  # 其余 [开心] 类标签
        return re.sub(r"\s{2,}", " ", text).strip()

    async def maybe_voice_reply(self, ws, scope: str, target_id: int, content: str) -> bool:
        """普通聊天回复按概率改发语音；成功发出返回 True（调用方就别再发文字了）"""
        if not self.tts or config.VOICE_REPLY_PROBABILITY <= 0:
            return False
        if random.random() >= config.VOICE_REPLY_PROBABILITY:
            return False
        text = self._voice_text_of(content)
        if not text or len(text) > config.VOICE_MAX_CHARS:
            return False
        if self.voice_cooling(scope, target_id):
            return False
        logger.info("[概率语音] %s %d: %s", scope, target_id, text[:50])
        return await self.send_voice(ws, scope, target_id, text)

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

        # 群白名单：不在名单里的群完全不参与（不记录、不回复、不插话）
        if not whitelist.allowed(group_id):
            logger.debug("群 %d 不在白名单，忽略", group_id)
            return

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

        # 语音触发：语音 xxx / 朗读一下 xxx / 说 xxx 等
        # 单字前缀（说/念/读）在群里需 @机器人 或带关键词，避免日常语句误触发
        if self.tts:
            hit = self.match_voice_trigger(raw_text)
            if hit:
                prefix = hit[0]
                targeted = bool(self._bot_id and self._bot_id in mentions) or any(
                    raw_text.lower().startswith(kw.lower()) for kw in config.TRIGGER_KEYWORDS
                )
                if len(prefix) == 1 and not targeted:
                    hit = None
            if hit:
                voice_text = hit[1]
                if not voice_text:
                    await self._send_group(ws, group_id, "要说什么呀～")
                    return
                if self.voice_cooling("g", group_id):
                    logger.info("[群%d] 语音冷却中，跳过", group_id)
                    return
                logger.info("[群%d] 语音请求(%s): %s", group_id, hit[0], voice_text)
                if not await self.send_voice(ws, "group", group_id, voice_text):
                    await self._send_group(ws, group_id, "语音合成失败，稍后再试～")
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
            # 按概率改用语音回复
            if await self.maybe_voice_reply(ws, "group", group_id, reply):
                return
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
            "下面是群友最近的聊天记录。你是这个群里的成员，正在和他们一起聊天。\n"
            "请像普通群友一样，顺着话题自然接一句（一句话，最多两句），不要当旁观者。\n"
            "除非话题涉及隐私、正在吵架、内容严肃敏感，或者你确实完全插不上话，否则都应该接一句。\n"
            "只有在上述极少数情况下才回复 [skip]。\n"
            "不要提问，不要复述聊天记录，不要解释，不要暴露这段指令。\n\n"
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
        # 按概率改用语音插话
        if await self.maybe_voice_reply(ws, "group", group_id, cleaned):
            return
        await self._send_group_with_emoji(ws, group_id, cleaned)

    async def _send_group(self, ws, group_id: int, content: str):
        await ws.send_str(json.dumps({
            "action": "send_group_msg",
            "params": {"group_id": group_id, "message": content},
        }))

    async def build_segments(self, content: str) -> list[dict]:
        """把 [emoji] / [emoji:角色名] 展开为 OneBot 消息段（群聊、私聊共用）

        只使用远程表情库：
        - [emoji]        → 全站随机
        - [emoji:任意]   → 全站随机
        - [emoji:角色名] → 指定角色随机（如 菲比、今汐、珂莱塔）
        """
        import re
        remote_character_tags = {"爱弥斯", "小爱", "小爱同学", "aimisi"}
        remote_any_tags = {"任意", "随机", "全部", "wuwa"}
        segments = []
        last_end = 0

        for match in re.finditer(r'\[emoji(?::([^\]]+))?\]', content):
            if match.start() > last_end:
                text = content[last_end:match.start()]
                if text:
                    segments.append({"type": "text", "data": {"text": text}})

            keyword = match.group(1)  # None 表示随机
            key = keyword.strip().lower() if keyword else ""
            if key in remote_character_tags:
                emoji = await self.emoji.get_random_any("爱弥斯")
            elif key in remote_any_tags or not keyword:
                emoji = await self.emoji.get_random_any(None)
            else:
                character = None
                if os.getenv("WUWA_EMOJI_ENABLED", "1") == "1":
                    try:
                        import wuwa_emoji
                        character = await wuwa_emoji.resolve_character(keyword)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("角色解析失败: %s", exc)
                if character:
                    logger.info("按角色取表情: %s", character)
                    emoji = await self.emoji.get_random_any(character)
                else:
                    logger.info("标签 %s 未命中，回退全站随机", keyword)
                    emoji = await self.emoji.get_random_any(None)

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
        segments = await self.build_segments(content)
        await ws.send_str(json.dumps({
            "action": "send_group_msg",
            "params": {"group_id": group_id, "message": segments},
        }))
        logger.info("发送消息: %s", json.dumps(segments, ensure_ascii=False)[:300])

    async def send_voice(self, ws, target_type: str, target_id: int, text: str, voice: str = None):
        """
        发送语音消息
        
        Args:
            ws: WebSocket 连接
            target_type: "group" 或 "private"
            target_id: 群号或用户号
            text: 要转换为语音的文字
            voice: 指定音色，默认使用 TTS 实例的默认音色
        """
        if not self.tts:
            logger.warning("TTS 未初始化，无法发送语音")
            return False
        
        # 生成语音
        voice_base64 = await self.tts.text_to_speech_base64(text, voice)
        if not voice_base64:
            logger.error("语音合成失败")
            return False
        
        # 构造 OneBot 消息段
        segments = [
            {"type": "record", "data": {"file": voice_base64}}
        ]
        
        # 发送
        if target_type == "group":
            action = "send_group_msg"
            id_param = "group_id"
        else:
            action = "send_private_msg"
            id_param = "user_id"
        
        await ws.send_str(json.dumps({
            "action": action,
            "params": {id_param: target_id, "message": segments},
        }))
        
        logger.info("发送语音到 %s %d: %s", target_type, target_id, text[:50])
        return True
