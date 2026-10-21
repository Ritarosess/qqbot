"""验证纯素材包模式：扫描数量、标签命中、占位符清理"""
import sys

sys.path.insert(0, "/opt/qqbot")

from emoji_manager import EmojiManager  # noqa: E402
from message_handler import MessageHandler  # noqa: E402

emoji = EmojiManager("/opt/qqbot/emojis")
print(f"扫描到素材包表情: {len(emoji._local_emojis)} 个")
print(f"QQ 收藏表情(应为0): {emoji.get_qq_face_stats()['total']} 个")

tags = ["开心", "喜欢", "难过", "生气", "害羞", "疑惑", "惊讶", "叹气",
        "睡觉", "卖萌", "打钱", "工作", "偷看", "自嘲", "卡住", "等待",
        "早安", "暧昧", "笨蛋", "happy", "sad"]
missing = [t for t in tags if not emoji.find_by_keyword(t)]
print(f"标签命中: {len(tags) - len(missing)}/{len(tags)}", end="")
print(f"，未命中: {missing}" if missing else "")

handler = MessageHandler(None, None, emoji)
cases = ["收到收到～ [emoji:开心]", "嗯[表情]", "在吗[emoji:不存在]", "只有[图片]",
         "好耶[开心]", "唉[叹气]", "正常一句话"]
for case in cases:
    segs = handler.build_segments(case)
    desc = []
    for s in segs:
        if s["type"] == "text":
            desc.append("TEXT:" + s["data"]["text"])
        else:
            desc.append(s["type"].upper() + ":" + str(s["data"].get("file", ""))[-24:])
    print(f"  {case!r:32} -> {desc}")
