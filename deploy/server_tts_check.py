"""服务器端 TTS 冒烟测试：验证 ECS → 千问 CosyVoice 链路与复刻音色可用性"""
import asyncio
import os
import sys

sys.path.insert(0, "/opt/qqbot")
from minimax_voice import build_tts


async def main():
    tts = build_tts(
        os.environ["DASHSCOPE_API_KEY"],
        os.environ["TTS_VOICE"],
        os.environ.get("TTS_MODEL", "cosyvoice-v3.5-flash"),
    )
    print("后端:", type(tts).__name__)
    b64 = await tts.text_to_speech_base64("漂泊者，服务器上的我也能用爱弥斯的声音说话啦！")
    if b64:
        print(f"SERVER_TTS_OK size={len(b64)}")
    else:
        print("SERVER_TTS_FAIL")


asyncio.run(main())
