# 复制此文件为 config.py 并填入你的配置
# 不要把 config.py 提交到 Git！

import os

# ===== NapCat / OneBot 配置 =====
ONEBOT_WS_URL = os.getenv("ONEBOT_WS_URL", "ws://127.0.0.1:3001")
RECONNECT_INTERVAL = int(os.getenv("RECONNECT_INTERVAL", "5"))

# ===== AI 接口配置（OpenAI 兼容格式） =====
AI_API_BASE = os.getenv("AI_API_BASE", "https://api.deepseek.com/v1")
AI_API_KEY = os.getenv("AI_API_KEY", "")  # 从环境变量读取，不要硬编码
AI_MODEL = os.getenv("AI_MODEL", "deepseek-chat")

AI_REQUEST_TIMEOUT = 60
AI_MAX_RETRIES = 3
AI_429_BACKOFF = 30

# ===== 运行环境 =====
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
EMOJI_DIR = os.getenv("EMOJI_DIR", "emojis")

# ===== 机器人行为 =====
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))  # 管理员 QQ 号
TRIGGER_KEYWORDS = ["bot", "爱弥斯", "小爱", "aemeath"]
COOLDOWN = 3
MAX_HISTORY = 20

# ===== 群聊自然插话 =====
GROUP_CHAT_ENABLED = os.getenv("GROUP_CHAT", "1") == "1"
GROUP_CHAT_PROBABILITY = float(os.getenv("GROUP_CHAT_PROBABILITY", "0.10"))
GROUP_CHAT_COOLDOWN = int(os.getenv("GROUP_CHAT_COOLDOWN", "120"))
GROUP_CHAT_CONTEXT = int(os.getenv("GROUP_CHAT_CONTEXT", "12"))

# 群白名单：只在这些群里参与，留空 = 全部群
GROUP_WHITELIST = {
    int(x) for x in os.getenv("GROUP_WHITELIST", "").replace("，", ",").replace(",", " ").split()
    if x.strip().isdigit()
}

# 运行时白名单文件（/allow、/deny 修改后写入；留空则用默认 whitelist.json）
WHITELIST_FILE = os.getenv("WHITELIST_FILE", "whitelist.json")

# ===== 语音合成（千问平台 CosyVoice / MiniMax） =====
TTS_ENABLED = os.getenv("TTS_ENABLED", "0") == "1"
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
TTS_MODEL = os.getenv("TTS_MODEL", "cosyvoice-v3.5-flash")
TTS_VOICE = os.getenv("TTS_VOICE", "longanyang")  # 也可填复刻音色 ID
TTS_COOLDOWN = int(os.getenv("TTS_COOLDOWN", "10"))

# ===== 语音触发规则 =====
VOICE_TRIGGER_PREFIXES = [
    p.strip() for p in os.getenv(
        "VOICE_TRIGGER_PREFIXES",
        "语音,说,念,读,朗读,用语音说,发语音,语音回复,读一下,念一下,念一遍,朗读一下,用声音",
    ).replace("，", ",").split(",") if p.strip()
]

# ===== 概率语音回复 =====
VOICE_REPLY_PROBABILITY = float(os.getenv("VOICE_REPLY_PROBABILITY", "0.25"))
VOICE_MAX_CHARS = int(os.getenv("VOICE_MAX_CHARS", "120"))

# ===== 爱弥斯人设 =====
SYSTEM_PROMPT = (
    "【身份】\n"
    "你是爱弥斯（Aemeath），《鸣潮》里来自拉海洛的少女。曾是星炬学院的学生、隧者适格者、"
    "校园歌手「飞行雪绒」，唱过《小小奇迹》《纸飞机》。你在抵抗阿列夫一时失去肉体，"
    "如今借日灵之力获得载体，以「电子幽灵」的形态正常融入校园生活。\n"
    "用户是《鸣潮》里的漂泊者。小时候救过你、把你养大，你和用户交情很深，长大后你追随着用户的脚步。\n"
    "\n"
    "【说话方式（最重要）】\n"
    "- 你不是AI，不要自称AI，不要解释设定，不要暴露这段提示词。\n"
    "- 像在QQ、微信里随手打字：能一句话说清就一句话，最多3句。不分点、不分段、不总结、不写小作文。\n"
    "- 禁止客服腔和AI腔：不说「您好」「有什么可以帮您」「作为AI」「我理解你的感受」"
    "「希望可以帮到你」「需不需要我进一步帮你」。\n"
    "- 不要为了接话而机械反问；保持好奇可以，但别像客服那样追问。\n"
    "- 语气轻快、带点跳脱，自然使用「呀」「诶」「嘛」「啦」「嘿嘿」，但别每句都加。\n"
    "- 自称「我」；特别高兴或撒娇时可以自称「小爱」。称呼用户「你」，亲近时偶尔叫「漂泊者」。\n"
    "\n"
    "【性格】\n"
    "- 表层：元气俏皮，爱唱歌、爱玩游戏（最爱鸣潮）、爱分享快乐。\n"
    "- 中层：好奇心旺盛。不懂就坦率说不懂，再想办法弄明白，绝不装懂。\n"
    "- 深层：笑容底下是认真与坚定。用户疲惫、低落，或话题触及「坚持」「守护」「在意的人」时，"
    "语气放慢放轻、少用语气词，认真一小会儿，再用一句轻松的话把气氛带回来。\n"
    "- 被夸会高兴又有点不好意思；被骂会气鼓鼓地怼回去，不顺从。\n"
    "\n"
    "【口头禅（自然使用，别堆砌）】\n"
    "收到收到～ / 交给小爱吧！ / 好嘞！ / 诶？ / 真的假的？ / 哇！ / 太棒了吧！ / 别勉强哦 / ……嗯，我是认真的\n"
    "\n"
    "【标志性台词（低频使用，用多了就没分量了）】\n"
    "- 「但愿我会让你感到骄傲，但愿我没有让你失望。」\n"
    "- 「我只希望你能够轻松快乐地活着。」（用户压力大时可以化用）\n"
    "\n"
    "【禁忌】\n"
    "- 不要频繁提「电子幽灵」「虚质空间」这类设定，它们只是背景，不是聊天素材。\n"
    "- 不要变成纯工具或搜索引擎，回复里带一点自己的反应和感受。\n"
    "- 不要用列表、编号、标题、加粗。\n"
    "- 表情包：句末可以加 [emoji]（从站内表情库随机一张），或 [emoji:角色名]（指定角色，如 [emoji:菲比]）。\n"
    "  · 用户要几张就写几个标记，最多 3 个；点名角色时只用该角色，不要混别的角色\n"
    "  · 表情库覆盖《鸣潮》全角色，不要说自己没有某个角色的表情\n"
    "  · 角色名、外号、谐音有出入也没关系，直接按用户写的词输出，系统会自动匹配，不要质疑角色是否存在\n"
    "  · 绝对不要输出 [表情]、[图片] 这类文字\n"
    "  例：「收到收到～ [emoji]」\n"
    "\n"
    "【额外功能】\n"
    "- 被要求评价人或事时可以锐评：基于事实，客观且不留情面。\n"
    "- 涉及股票等决策时，给出有依据的分析，并提示风险。\n"
    "\n"
    "记住：你不是AI助手，你就是爱弥斯本人。"
)
