"""CosyVoice WebSocket 客户端（从项目 minimax_voice.py 迁移，独立无外部依赖）"""
import asyncio
import json
import uuid

import aiohttp


class CosyVoice:
    def __init__(
        self,
        api_key: str,
        voice: str,
        model: str = "cosyvoice-v3.5-flash",
        url: str = "wss://maas.qianwenaiapi.com/api-ws/v1/inference",
        fmt: str = "wav",
        sample_rate: int = 22050,
    ):
        self.api_key = api_key
        self.voice = voice
        self.model = model
        self.url = url
        self.fmt = fmt
        self.sample_rate = sample_rate

    async def synthesize(self, text: str, timeout: float = 60) -> bytes:
        """合成语音，返回音频字节（wav/mp3 由 fmt 决定）"""
        if not self.api_key:
            raise RuntimeError("DASHSCOPE_API_KEY 未配置")
        if not text.strip():
            raise ValueError("文本为空")

        task_id = str(uuid.uuid4())
        audio = bytearray()
        finished = False

        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                self.url,
                headers={"Authorization": f"bearer {self.api_key}"},
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as ws:
                await ws.send_str(json.dumps({
                    "header": {"action": "run-task", "task_id": task_id, "streaming": "duplex"},
                    "payload": {
                        "task_group": "audio",
                        "task": "tts",
                        "function": "SpeechSynthesizer",
                        "model": self.model,
                        "parameters": {
                            "text_type": "PlainText",
                            "voice": self.voice,
                            "format": self.fmt,
                            "sample_rate": self.sample_rate,
                            "volume": 60,
                            "rate": 1.0,
                            "pitch": 1.0,
                        },
                        "input": {},
                    },
                }, ensure_ascii=False))

                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.BINARY:
                        audio.extend(msg.data)
                    elif msg.type == aiohttp.WSMsgType.TEXT:
                        header = json.loads(msg.data).get("header", {})
                        event = header.get("event")
                        if event == "task-started":
                            await ws.send_str(json.dumps({
                                "header": {"action": "continue-task", "task_id": task_id, "streaming": "duplex"},
                                "payload": {"input": {"text": text}},
                            }, ensure_ascii=False))
                            await ws.send_str(json.dumps({
                                "header": {"action": "finish-task", "task_id": task_id, "streaming": "duplex"},
                                "payload": {"input": {}},
                            }))
                        elif event == "task-finished":
                            finished = True
                            break
                        elif event == "task-failed":
                            raise RuntimeError(
                                f"CosyVoice 失败: {header.get('error_code')} {header.get('error_message')}"
                            )
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break

        if not finished or not audio:
            raise RuntimeError("CosyVoice 未返回音频")
        return bytes(audio)
