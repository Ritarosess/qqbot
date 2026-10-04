"""
QQ 机器人主入口
依赖 NapCat 提供 OneBot v11 正向 WebSocket
"""
import asyncio
import json
import logging
import aiohttp
from datetime import datetime, timedelta

import config
from ai_client import AIClient
from conversation import ConversationManager
from message_handler import MessageHandler
from emoji_manager import EmojiManager

logging.basicConfig(
    level=logging.DEBUG,
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
        self.emoji = EmojiManager()
        self.handler = MessageHandler(self.ai, self.conv, self.emoji)
        self.queue: asyncio.Queue[dict] = asyncio.Queue()
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self._summary_task: asyncio.Task | None = None
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
            reply = "运行中"
        elif text.startswith("/clear"):
            try:
                gid = int(text.split()[1])
                self.conv.clear(gid)
                reply = f"已清空群 {gid} 的上下文"
            except (IndexError, ValueError):
                reply = "用法: /clear <群号>"
        elif text == "/help":
            reply = "/status  状态\n/clear <群号>  清空上下文\n/summary  手动触发今日总结\n/emoji  查看表情包列表\n/fetch_emoji  重新获取 QQ 收藏表情"
        elif text == "/summary":
            await self._daily_summary()
            reply = "已触发今日总结任务"
        elif text == "/emoji":
            reply = self.emoji.list_all()
        elif text == "/fetch_emoji":
            await self._fetch_qq_collection()
            stats = self.emoji.get_qq_face_stats()
            reply = f"已重新获取 QQ 收藏表情，当前共 {stats['total']} 个"
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
        else:
            return
        await self._send_private(ws, event["user_id"], reply)

    async def _send_private(self, ws, user_id: int, content: str):
        await ws.send_str(json.dumps({
            "action": "send_private_msg",
            "params": {"user_id": user_id, "message": content},
        }))

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
        """获取 QQ 收藏的表情包"""
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
                    "1. 用樱井莉奈的语气（活泼可爱，带「的说」「喵」口癖）\n"
                    "2. 概括主要话题和有趣的内容\n"
                    "3. 提到活跃的发言者\n"
                    "4. 结尾可以说一句鼓励或期待的话\n"
                    "5. 总长度控制在200字以内\n\n"
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
                        # 连接成功后获取 QQ 收藏表情
                        await self._fetch_qq_collection()
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    await self.queue.put(json.loads(msg.data))
                                except json.JSONDecodeError:
                                    logger.warning("非 JSON 数据: %s", msg.data[:100])
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
            except aiohttp.ClientError as e:
                logger.error("连接失败: %s", e)
            logger.info("%d 秒后重连...", config.RECONNECT_INTERVAL)
            await asyncio.sleep(config.RECONNECT_INTERVAL)


if __name__ == "__main__":
    asyncio.run(QQBot().run())
