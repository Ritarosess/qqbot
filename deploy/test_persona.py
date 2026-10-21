"""人格自测：用真实 API 检查回复是否符合爱弥斯人设（不会发 QQ 消息）"""
import asyncio
import sys

sys.path.insert(0, "/opt/qqbot")

import config  # noqa: E402
from ai_client import AIClient  # noqa: E402

CASES = [
    "你好，你是谁呀？",
    "今天好累，报告写不完了",
    "你觉得我这个方案怎么样？",
]


async def main():
    client = AIClient(
        config.AI_API_BASE,
        config.AI_API_KEY,
        config.AI_MODEL,
        timeout=60,
        max_retries=1,
        backoff=2,
    )
    for q in CASES:
        reply = await client.chat([
            {"role": "system", "content": config.SYSTEM_PROMPT},
            {"role": "user", "content": q},
        ])
        print(f"用户: {q}")
        print(f"爱弥斯: {reply}")
        print("-" * 40)


asyncio.run(main())
