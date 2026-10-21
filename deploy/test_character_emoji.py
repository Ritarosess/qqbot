"""测试按角色索要表情：角色解析 + 远程取图 + AI 是否输出角色标签"""
import asyncio
import os
import sys

sys.path.insert(0, "/opt/qqbot")

import config  # noqa: E402
import wuwa_emoji  # noqa: E402
from ai_client import AIClient  # noqa: E402
from emoji_manager import EmojiManager  # noqa: E402
from message_handler import MessageHandler  # noqa: E402


async def main():
    chars = await wuwa_emoji.ensure_characters()
    print(f"角色列表: {len(chars)} 个")
    for name in ("菲比", "今汐", "珂莱塔", "不存在角色"):
        print(f"  解析 {name} -> {wuwa_emoji.find_character(name)}")

    emoji = EmojiManager(os.getenv("EMOJI_DIR", "/opt/qqbot/emojis"))
    handler = MessageHandler(None, None, emoji)
    segments = await handler.build_segments("来一张 [emoji:菲比]")
    images = [s for s in segments if s["type"] == "image"]
    print("按角色取图:", "成功" if images else "失败", "| 图片段长度:", len(images[0]["data"]["file"]) if images else 0)

    client = AIClient(
        config.AI_API_BASE, config.AI_API_KEY, config.AI_MODEL,
        timeout=60, max_retries=1, backoff=2,
    )
    question = os.getenv("TEST_PROMPT", "给我发两个菲比表情包")
    reply = await client.chat([
        {"role": "system", "content": config.SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ])
    print("提问:", question)
    print("AI 回复:", reply)


if __name__ == "__main__":
    asyncio.run(main())
