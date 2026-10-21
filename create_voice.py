"""
爱弥斯声音复刻脚本（千问平台 MiniMax）

用法:
    1. 准备一段爱弥斯的说话音频（10秒~5分钟，MP3/WAV/M4A，无BGM/噪音/他人声音），
       上传到公网可访问的 URL（如阿里云 OSS）
    2. 创建音色:
       $env:DASHSCOPE_API_KEY = "你的Key"
       python create_voice.py --audio-url "https://你的音频URL.wav" --voice-id aemeath
    3. 试听合成（首次使用复刻音色会扣 ¥9.9 解锁费）:
       python create_voice.py --synth "收到收到～交给小爱吧！" --voice-id aemeath
"""
import argparse
import asyncio
import base64
import io
import json
import os
import sys

import aiohttp

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

BASE_URL = os.getenv(
    "TTS_BASE_URL",
    "https://maas.qianwenaiapi.com/api/v1/services/aigc/multimodal-generation/generation",
)
MODEL = os.getenv("TTS_MODEL", "MiniMax/speech-2.8-turbo")


def extract_audio(data: dict) -> str | None:
    """从响应中提取 base64 音频（兼容 output.data.audio / output.audio / data.audio）"""
    output = data.get("output") or {}
    return (
        (output.get("data") or {}).get("audio")
        or output.get("audio")
        or (data.get("data") or {}).get("audio")
    )


async def post(api_key: str, payload: dict) -> dict:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json; charset=utf-8",
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(BASE_URL, json=payload, headers=headers) as resp:
            status = resp.status
            try:
                data = await resp.json()
            except Exception:
                print(f"HTTP {status} 非 JSON 响应: {(await resp.text())[:500]}")
                sys.exit(1)
            if status != 200:
                print(f"HTTP {status}: {json.dumps(data, ensure_ascii=False)[:800]}")
                sys.exit(1)
            return data


async def clone_voice(api_key: str, audio_url: str, voice_id: str, demo_text: str) -> None:
    print(f"正在用音频创建音色: {voice_id}")
    print(f"  参考音频: {audio_url}")
    print(f"  试听文本: {demo_text}")
    payload = {
        "input": {
            "action": "voice_clone",
            "voice_id": voice_id,
            "audio_url": audio_url,
            "text": demo_text,
        },
        "model": MODEL,
    }
    data = await post(api_key, payload)
    print("API 返回:", json.dumps(data, ensure_ascii=False)[:1000])

    # 试听音频在 output.data.audio（base64），保存下来听效果
    audio_b64 = extract_audio(data)
    if audio_b64:
        out_file = f"{voice_id}_demo.mp3"
        with open(out_file, "wb") as f:
            f.write(base64.b64decode(audio_b64))
        print(f"试听音频已保存: {out_file}")
    else:
        print("未返回试听音频")
    print(f"音色 ID: {voice_id}（记住它，配置 TTS_VOICE 时用）")


async def synth_test(api_key: str, voice_id: str, text: str) -> None:
    print(f"正在用音色 {voice_id} 合成: {text}")
    print("（注意：首次使用复刻音色会扣 ¥9.9 音色解锁费）")
    payload = {
        "input": {
            "action": "tts",
            "text": text,
            "voice_setting": {"voice_id": voice_id, "speed": 1, "vol": 1, "pitch": 0},
            "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": "mp3", "channel": 1},
        },
        "model": MODEL,
    }
    data = await post(api_key, payload)
    audio_b64 = extract_audio(data)
    if audio_b64:
        out_file = "aemeath_test.mp3"
        with open(out_file, "wb") as f:
            f.write(base64.b64decode(audio_b64))
        print(f"合成成功: {out_file}（{len(audio_b64)} bytes base64）")
    else:
        print("合成失败:", json.dumps(data, ensure_ascii=False)[:800])


async def main():
    parser = argparse.ArgumentParser(description="千问 MiniMax 声音复刻")
    parser.add_argument("--audio-url", help="参考音频的公网 URL（创建音色用）")
    parser.add_argument("--voice-id", default="aemeath", help="自定义音色 ID（默认 aemeath）")
    parser.add_argument("--text", default="你说是什么就是什么", help="试听文本")
    parser.add_argument("--synth", metavar="TEXT", help="用已有音色测试合成，跳过创建")
    args = parser.parse_args()

    api_key = os.getenv("DASHSCOPE_API_KEY", "")
    if not api_key:
        print("请先设置环境变量 DASHSCOPE_API_KEY")
        sys.exit(1)

    if args.synth:
        await synth_test(api_key, args.voice_id, args.synth)
    elif args.audio_url:
        await clone_voice(api_key, args.audio_url, args.voice_id, args.text)
    else:
        parser.print_help()
        print("\n创建音色: --audio-url <URL>；测试合成: --synth <文字>")


if __name__ == "__main__":
    asyncio.run(main())
