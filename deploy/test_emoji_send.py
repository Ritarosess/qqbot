"""端到端测试：用一个远程随机表情给管理员发私聊

用法：ADMIN_ID=你的QQ号 EMOJI_DIR=/opt/qqbot/emojis python test_emoji_send.py
"""
import asyncio
import json
import os
import sys

import aiohttp

sys.path.insert(0, "/opt/qqbot")

from emoji_manager import EmojiManager  # noqa: E402
from message_handler import MessageHandler  # noqa: E402


async def main():
    target = int(os.getenv("ADMIN_ID", "0"))
    if not target:
        print("缺少 ADMIN_ID")
        return
    emoji = EmojiManager(os.getenv("EMOJI_DIR", "/opt/qqbot/emojis"))
    handler = MessageHandler(None, None, emoji)
    content = sys.argv[1] if len(sys.argv) > 1 else "远程表情测试 [emoji]"
    segments = await handler.build_segments(content)
    print("测试内容:", content)
    kinds = [s["type"] for s in segments]
    print("消息段:", kinds, "| 图片字节数:", [len(s["data"].get("file", "")) for s in segments if s["type"] == "image"])

    payload = {
        "action": "send_private_msg",
        "echo": "emoji-test",
        "params": {"user_id": target, "message": segments},
    }
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect("ws://127.0.0.1:3001", timeout=25) as ws:
            await ws.send_str(json.dumps(payload))
            while True:
                msg = await asyncio.wait_for(ws.receive(), timeout=30)
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                data = json.loads(msg.data)
                if data.get("echo") != "emoji-test":
                    continue
                print("发送结果:", data.get("status"), data.get("retcode"), data.get("message") or data.get("msg"))
                return


if __name__ == "__main__":
    asyncio.run(main())
