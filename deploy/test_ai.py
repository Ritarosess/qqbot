"""验证 AI 接口可用性（不会发送任何 QQ 消息）"""
import asyncio
import sys

sys.path.insert(0, "/opt/qqbot")

import config  # noqa: E402
from ai_client import AIClient  # noqa: E402


async def main():
    client = AIClient(
        config.AI_API_BASE,
        config.AI_API_KEY,
        config.AI_MODEL,
        timeout=30,
        max_retries=1,
        backoff=2,
    )
    reply = await client.chat([{"role": "user", "content": "只回复两个字：正常"}])
    print("模型:", config.AI_MODEL)
    print("返回:", reply if reply else "（空响应，可能鉴权或额度问题）")


asyncio.run(main())
