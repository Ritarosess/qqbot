"""端到端验证：通过 NapCat 发送一条带 base64 表情的私聊消息

用法：ADMIN_ID=你的QQ号 python test_send.py
"""
import asyncio
import base64
import json
import os
from pathlib import Path

import aiohttp

TARGET_QQ = int(os.getenv("ADMIN_ID", "0"))
IMAGE = Path("/opt/qqbot/emojis/开心_happy_01.gif")


async def main() -> int:
    b64 = base64.b64encode(IMAGE.read_bytes()).decode()
    payload = {
        "action": "send_private_msg",
        "echo": "test-emoji",
        "params": {
            "user_id": TARGET_QQ,
            "message": [
                {"type": "text", "data": {"text": "[测试] 表情链路检查"}},
                {"type": "image", "data": {"file": "base64://" + b64}},
            ],
        },
    }
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect("ws://127.0.0.1:3001", timeout=15) as ws:
            await ws.send_str(json.dumps(payload))
            while True:
                msg = await asyncio.wait_for(ws.receive(), timeout=15)
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                data = json.loads(msg.data)
                if data.get("echo") != "test-emoji":
                    continue
                print("status:", data.get("status"), "retcode:", data.get("retcode"))
                print("message:", data.get("message") or data.get("msg") or data.get("wording"))
                return 0 if data.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
