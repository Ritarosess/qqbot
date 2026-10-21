"""表情库自检：远程随机、角色解析、消息段构造

用法：EMOJI_DIR=/opt/qqbot/emojis python deploy/verify_emojis.py
"""
import asyncio
import os
import sys

sys.path.insert(0, "/opt/qqbot")

import wuwa_emoji  # noqa: E402
from emoji_manager import EmojiManager  # noqa: E402
from message_handler import MessageHandler  # noqa: E402


async def main():
    chars = await wuwa_emoji.ensure_characters()
    print(f"角色列表: {len(chars)} 个")
    for name in ("菲比", "今汐", "达妮娅", "达尼娅", "达尼亚", "今夕",
                 "耙耙柑", "水蜜桃", "糯糯", "娅娅", "西西", "嘻嘻", "夕夕", "汐汐", "xixi",
                 "青霄", "来个青霄", "青霄的表情包", "嘻嘻哈哈", "不存在的角色"):
        print(f"  解析 {name} -> {wuwa_emoji.find_character(name)}")

    emoji = EmojiManager(os.getenv("EMOJI_DIR", "/opt/qqbot/emojis"))
    handler = MessageHandler(None, None, emoji)

    cases = ["随便来一张 [emoji]", "来张菲比 [emoji:菲比]", "来张达尼亚 [emoji:达尼亚]",
             "来张耙耙柑 [emoji:耙耙柑]", "来张水蜜桃 [emoji:水蜜桃]", "嗯[表情]", "在吗[emoji:不存在]"]
    for case in cases:
        segs = await handler.build_segments(case)
        desc = []
        for seg in segs:
            if seg["type"] == "text":
                desc.append("TEXT:" + seg["data"]["text"])
            else:
                desc.append(seg["type"].upper() + f"({len(seg['data'].get('file', ''))}B)")
        print(f"  {case!r:28} -> {desc}")


if __name__ == "__main__":
    asyncio.run(main())
