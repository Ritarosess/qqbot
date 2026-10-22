"""真实语音合成验证：CosyVoice wav 输出（需要 DASHSCOPE_API_KEY）

用法：
    $env:DASHSCOPE_API_KEY="sk-xxx"
    python astrbot/tests/test_tts_live.py
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugins" / "astrbot_plugin_aemeath_voice"))
from aemeath_voice_core.cosyvoice import CosyVoice  # noqa: E402

KEY = os.environ.get("DASHSCOPE_API_KEY", "")
VOICE = os.environ.get(
    "TTS_VOICE",
    "cosyvoice-v3.5-flash-aemeath-59638a19c09243cabf1144ce1be23d28",
)


async def main():
    if not KEY:
        print("SKIP: 未设置 DASHSCOPE_API_KEY")
        return
    client = CosyVoice(api_key=KEY, voice=VOICE, fmt="wav", sample_rate=22050)
    audio = await client.synthesize("你好呀，我是爱弥斯，语音链路测试。")
    ok = audio[:4] == b"RIFF" and audio[8:12] == b"WAVE"
    print(f"{'PASS' if ok else 'FAIL'} live tts: {len(audio)} bytes, header={audio[:12]!r}")
    out = Path(__file__).resolve().parent / "_tts_live.wav"
    out.write_bytes(audio)
    print("已保存:", out)
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
