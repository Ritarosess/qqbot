"""
QQ 机器人主入口
依赖 NapCat 提供 OneBot v11 正向 WebSocket
"""
import asyncio
import json
import logging
import os
import re

import aiohttp
from datetime import datetime, timedelta

import config
from ai_client import AIClient
from conversation import ConversationManager
from message_handler import MessageHandler
from emoji_manager import EmojiManager
from minimax_voice import build_tts

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bot")


class QQBot:
    def __init__(self):
        self.ai = AIClient(
            config.AI_API_BASE, config.AI_API_KEY, config.AI_MODEL,
            timeout=config.AI_REQUEST_TIMEOUT,
            max_retries=config.AI_MAX_RETRIES,
            backoff=config.AI_429_BACKOFF,
        )
        self.conv = ConversationManager(config.MAX_HISTORY)
        self.emoji = EmojiManager(config.EMOJI_DIR)
        
        # 初始化语音合成（千问平台 CosyVoice / MiniMax）
        self.tts = None
        api_key = config.DASHSCOPE_API_KEY or config.MINIMAX_API_KEY
        if config.TTS_ENABLED and api_key:
            self.tts = build_tts(api_key, config.TTS_VOICE, config.TTS_MODEL)
            logger.info("语音合成已启用，模型: %s, 音色: %s", config.TTS_MODEL, config.TTS_VOICE)

        self.handler = MessageHandler(self.ai, self.conv, self.emoji, self.tts)
        self.queue: asyncio.Queue[dict] = asyncio.Queue()
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self._summary_task: asyncio.Task | None = None
        # 语音冷却记录
        self._voice_cooldown: dict[int, float] = {}
        # API 调用相关
        self._pending_requests: dict[str, asyncio.Future] = {}
        self._echo_counter = 0

    async def _consume(self):
        while True:
            event = await self.queue.get()
            try:
                # 处理 API 响应（echo 标识）
                if event.get("echo") is not None:
                    echo = str(event.get("echo"))
                    if echo in self._pending_requests:
                        future = self._pending_requests.pop(echo)
                        if event.get("status") == "ok":
                            future.set_result(event.get("data"))
                        else:
                            future.set_exception(Exception(event.get("msg", "API 调用失败")))
                        continue

                if (event.get("post_type") == "meta_event"
                        and event.get("meta_event_type") == "lifecycle"):
                    bot_id = event.get("self_id")
                    if bot_id:
                        self.handler.set_bot_id(bot_id)
                        logger.info("机器人登录号: %d", bot_id)
                    continue

                if event.get("post_type") != "message":
                    continue

                if event["message_type"] == "group":
                    await self.handler.handle_group(self.ws, event)
                elif event["message_type"] == "private":
                    logger.info("收到私聊消息: user_id=%d, raw=%s", event["user_id"], event.get("raw_message", ""))
                    await self._handle_private(self.ws, event)
            except Exception as e:
                logger.exception("处理事件出错: %s", e)

    async def _handle_private(self, ws, event: dict):
        if event["user_id"] != config.ADMIN_ID:
            return

        # 先提取图片（收藏表情）
        images = []
        if isinstance(event.get("message"), list):
            for seg in event["message"]:
                if seg.get("type") == "image":
                    images.append(seg["data"])

        text = event["raw_message"].strip()

        # 如果是纯图片（收藏表情），保存
        if images:
            for img in images:
                self.handler.emoji.add_emoji(
                    file_id=img.get("file", ""),
                    url=img.get("url", ""),
                    keywords=[],
                    sender_id=event["user_id"],
                    emoji_type="mface",
                )
            logger.info("私聊保存了 %d 个收藏表情", len(images))
            return

        if text == "/status":
            groups = "、".join(str(g) for g in sorted(config.GROUP_WHITELIST)) if config.GROUP_WHITELIST else "全部群"
            reply = f"运行中｜白名单：{groups}"
        elif text.startswith("/clear"):
            try:
                gid = int(text.split()[1])
                self.conv.clear(gid)
                reply = f"已清空群 {gid} 的上下文"
            except (IndexError, ValueError):
                reply = "用法: /clear <群号>"
        elif text == "/help":
            reply = (
                "/status  状态\n/clear <群号>  清空上下文\n/summary  手动触发今日总结\n"
                "/emoji  查看表情包列表\n/voice <文字>  发送语音\n\n"
                "语音触发（私聊/群里都能用）：\n"
                "  语音 你好呀 / 朗读 今天天气不错 / 念一遍 晚安\n"
                "  说 你好、念：晚安（单字前缀需带空格或冒号）\n"
                "群里单字触发需 @我 或带触发词，避免误触发"
            )

        # 语音指令：/voice <内容>
        elif text.startswith("/voice"):
            voice_text = text[len("/voice"):].strip().lstrip(":：").strip()
            if not self.tts:
                reply = "语音功能未启用，请设置 DASHSCOPE_API_KEY"
            elif not voice_text:
                reply = "用法: /voice <文字>\n例如: /voice 你好呀"
            else:
                ok = await self.handler.send_voice(ws, "private", event["user_id"], voice_text)
                reply = None if ok else "语音合成失败，请检查 API Key"

        # 自然语言语音触发：语音 xxx / 朗读 xxx / 说 xxx
        elif self.tts:
            hit = self.handler.match_voice_trigger(text)
            if hit:
                voice_text = hit[1]
                if not voice_text:
                    reply = "要说什么呀～"
                elif self.handler.voice_cooling("p", event["user_id"]):
                    reply = "刚说过啦，歇一下嘛～"
                else:
                    ok = await self.handler.send_voice(ws, "private", event["user_id"], voice_text)
                    reply = None if ok else "语音合成失败，检查下 API Key 呢"
            else:
                await self._private_chat(ws, event, text)
                return
        elif text == "/summary":
            await self._daily_summary()
            reply = "已触发今日总结任务"
        elif text == "/emoji":
            reply = self.emoji.list_all()
        elif text == "/fetch_emoji":
            if os.getenv("EMOJI_QQ_FACES", "0") == "1":
                await self._fetch_qq_collection()
                stats = self.emoji.get_qq_face_stats()
                reply = f"已重新获取 QQ 收藏表情，当前共 {stats['total']} 个"
            else:
                reply = "QQ 收藏表情已禁用（当前为纯素材包模式）"
        elif text.startswith("/tag"):
            # /tag 序号 关键词
            # 例如: /tag 1 开心
            parts = text.split(maxsplit=2)
            if len(parts) < 3:
                reply = "用法: /tag <序号> <关键词>\n先发送 /emoji 查看表情包列表"
            else:
                try:
                    idx = int(parts[1]) - 1
                    keyword = parts[2]
                    if self.emoji.tag_emoji(idx, keyword):
                        reply = f"已给表情包 {idx+1} 添加标签: {keyword}"
                    else:
                        reply = f"序号 {idx+1} 不存在，请发送 /emoji 查看"
                except ValueError:
                    reply = "序号必须是数字"
        elif text.startswith("/"):
            reply = "未知指令，发送 /help 查看全部指令"
        else:
            # 普通私聊内容 → 交给 AI 对话
            await self._private_chat(ws, event, text)
            return
        if reply:
            await self._send_private(ws, event["user_id"], reply)

    async def _private_chat(self, ws, event: dict, text: str):
        """管理员私聊普通内容 → AI 对话（与群聊上下文隔离）"""
        user_id = int(event["user_id"])
        key = -user_id  # 负数 key，避免与群号冲突
        if not self.conv.can_reply(key, config.COOLDOWN):
            logger.info("私聊冷却中，跳过: %s", text)
            return
        logger.info("私聊对话: %s", text)
        self.conv.add_user(key, text)
        reply = await self.ai.chat(self.conv.get_messages(key, config.SYSTEM_PROMPT))
        if reply:
            logger.info("私聊 AI 回复: %s", reply)
            self.conv.add_assistant(key, reply)
            # 按概率改用语音回复
            if await self.handler.maybe_voice_reply(ws, "private", user_id, reply):
                return
            await self._send_private_with_emoji(ws, user_id, reply)
        else:
            await self._send_private(ws, user_id, "（AI 暂时没有响应，请稍后再试）")

    async def _send_private(self, ws, user_id: int, content: str):
        await ws.send_str(json.dumps({
            "action": "send_private_msg",
            "params": {"user_id": user_id, "message": content},
        }))

    async def _send_private_with_emoji(self, ws, user_id: int, content: str):
        """发送带表情包的私聊回复（复用群聊的 [emoji] 解析逻辑）"""
        segments = await self.handler.build_segments(content)
        await ws.send_str(json.dumps({
            "action": "send_private_msg",
            "params": {"user_id": user_id, "message": segments},
        }))
        logger.info("私聊发送消息段: %s", json.dumps(segments, ensure_ascii=False)[:300])

    async def _send_group(self, ws, group_id: int, content: str):
        await ws.send_str(json.dumps({
            "action": "send_group_msg",
            "params": {"group_id": group_id, "message": content},
        }))

    async def call_api(self, action: str, params: dict | None = None) -> dict:
        """通过 WebSocket 发送 API 请求并等待响应"""
        if not self.ws or self.ws.closed:
            raise RuntimeError("WebSocket 未连接")

        self._echo_counter += 1
        echo = str(self._echo_counter)
        future = asyncio.get_event_loop().create_future()
        self._pending_requests[echo] = future

        payload = {"action": action, "echo": echo}
        if params:
            payload["params"] = params

        await self.ws.send_str(json.dumps(payload))

        try:
            return await asyncio.wait_for(future, timeout=30)
        except asyncio.TimeoutError:
            self._pending_requests.pop(echo, None)
            raise TimeoutError(f"API {action} 调用超时")

    async def _fetch_qq_collection(self):
        """获取 QQ 收藏的表情包（纯素材包模式下禁用）"""
        if os.getenv("EMOJI_QQ_FACES", "0") != "1":
            logger.info("QQ 收藏表情已禁用（使用远程表情库）")
            return
        try:
            logger.info("正在获取 QQ 收藏表情...")
            result = await self.call_api("fetch_custom_face", {"count": 100})
            logger.info("fetch_custom_face 返回原始数据: %s", json.dumps(result, ensure_ascii=False))

            # 处理返回结果
            faces = []
            if isinstance(result, list):
                faces = result
            elif isinstance(result, dict):
                faces = result.get("data", []) or result.get("faces", []) or result.get("faceList", []) or []

            if faces:
                self.emoji.load_qq_faces(faces)
                logger.info("成功加载 %d 个 QQ 收藏表情", len(faces))
                # 打印前几个表情的信息
                for i, face in enumerate(faces[:5]):
                    logger.info("表情 %d: %s", i+1, json.dumps(face, ensure_ascii=False)[:200])
            else:
                logger.warning("QQ 收藏表情为空")

        except Exception as e:
            logger.exception("获取 QQ 收藏表情失败: %s", e)

    async def _daily_summary(self):
        """执行每日总结：为每个活跃群生成 AI 总结并发送"""
        logger.info("开始执行每日总结任务...")
        active_groups = self.conv.get_active_groups()
        # 群白名单：非白名单群不发总结
        if config.GROUP_WHITELIST:
            active_groups = [g for g in active_groups if g in config.GROUP_WHITELIST]
        if not active_groups:
            logger.info("昨日无活跃群聊，跳过总结")
            return

        for group_id in active_groups:
            try:
                messages = self.conv.get_daily_messages(group_id)
                if len(messages) < 5:
                    logger.info("群 %d 消息太少(%d条)，跳过总结", group_id, len(messages))
                    continue

                # 构造 AI 总结请求
                summary_prompt = (
                    "请为以下群聊内容生成一份简洁的每日总结。\n"
                    "要求：\n"
                    "1. 用爱弥斯的语气（活泼俏皮，带「呀」「诶」「嘿嘿」，不要客服腔）\n"
                    "2. 概括主要话题和有趣的内容\n"
                    "3. 提到活跃的发言者\n"
                    "4. 结尾可以说一句鼓励或期待的话\n"
                    "5. 总长度控制在200字以内\n"
                    "6. 这是纯文字总结，绝对不要输出 [emoji]、[表情]、[图片] 等任何方括号占位标记\n\n"
                    "以下是昨日群聊记录：\n"
                )

                # 整理消息为文本
                chat_text = ""
                for msg in messages[-50:]:  # 最多取50条
                    chat_text += f"[{msg['time']}] {msg['nickname']}: {msg['content']}\n"

                full_prompt = summary_prompt + chat_text

                summary = await self.ai.chat([
                    {"role": "system", "content": config.SYSTEM_PROMPT},
                    {"role": "user", "content": full_prompt}
                ])

                if summary:
                    # 清理残留占位符：总结是纯文字，不允许出现 [emoji]/[表情]/[图片]
                    summary = re.sub(
                        r"\[(?:emoji|表情|图片|image|img|贴图)[^\]]*\]",
                        "",
                        summary,
                        flags=re.IGNORECASE,
                    ).strip()
                if summary:
                    await self._send_group(self.ws, group_id, f"📋 昨日群聊总结 📋\n\n{summary}")
                    logger.info("群 %d 总结已发送", group_id)
                    # 总结后清空当日记录，开始新的一天
                    self.conv.clear_daily(group_id)
                else:
                    logger.warning("群 %d AI 总结生成失败", group_id)

            except Exception as e:
                logger.exception("群 %d 总结出错: %s", group_id, e)

    async def _summary_scheduler(self):
        """每日 21:00 定时总结调度器"""
        while True:
            now = datetime.now()
            # 计算今天21点（晚上9点）
            target = now.replace(hour=21, minute=0, second=0, microsecond=0)
            if now >= target:
                # 已经过了今天21点，等明天
                target += timedelta(days=1)

            wait_seconds = (target - now).total_seconds()
            logger.info("下次总结时间: %s，等待 %d 秒", target.strftime("%Y-%m-%d %H:%M"), int(wait_seconds))

            await asyncio.sleep(wait_seconds)
            await self._daily_summary()

    async def _reader(self, ws):
        """独立任务：把 WebSocket 收到的所有消息投递到队列。

        必须先于 call_api 运行，否则 API 响应（echo）没有消费者，call_api 会自锁到超时。
        """
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    await self.queue.put(json.loads(msg.data))
                except json.JSONDecodeError:
                    logger.warning("非 JSON 数据: %s", msg.data[:100])
            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                break

    async def _sync_self_id(self):
        """主动获取自身 QQ 号：NapCat 先于本程序启动时不会补发 lifecycle 事件。"""
        try:
            info = await self.call_api("get_login_info")
            uid = info.get("user_id") if isinstance(info, dict) else None
            if uid:
                self.handler.set_bot_id(int(uid))
                logger.info("机器人登录号: %s", uid)
            else:
                logger.warning("get_login_info 未返回 user_id: %s", info)
        except Exception as e:
            logger.warning("获取登录号失败: %s", e)

    async def run(self):
        asyncio.create_task(self._consume())
        # 启动每日总结调度器
        self._summary_task = asyncio.create_task(self._summary_scheduler())
        logger.info("每日总结调度器已启动，每天 21:00 自动总结")

        while True:
            try:
                logger.info("连接 NapCat: %s", config.ONEBOT_WS_URL)
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(config.ONEBOT_WS_URL) as ws:
                        self.ws = ws
                        logger.info("已连接")
                        # 先启动接收任务，再调用 API，否则会等待一个没人投递的响应
                        reader = asyncio.create_task(self._reader(ws))
                        try:
                            await self._sync_self_id()
                            await self._fetch_qq_collection()
                            await reader
                        finally:
                            if not reader.done():
                                reader.cancel()
            except aiohttp.ClientError as e:
                logger.error("连接失败: %s", e)
            logger.info("%d 秒后重连...", config.RECONNECT_INTERVAL)
            await asyncio.sleep(config.RECONNECT_INTERVAL)


if __name__ == "__main__":
    asyncio.run(QQBot().run())
