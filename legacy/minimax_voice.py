"""
语音合成模块
- MiniimaxTTS: 千问平台 MiniMax HTTP 合成（该渠道偶发不稳定）
- CosyVoiceTTS: 千问平台 CosyVoice WebSocket 合成，支持声音复刻（推荐）
"""
import asyncio
import base64
import json
import os
import uuid
import aiohttp
import logging
from typing import Optional

logger = logging.getLogger("tts")

# 可用音色列表（来自千问平台）
VOICES = {
    # 中文音色
    "中文女声-活泼": "female-shaonv",
    "中文女声-温柔": "female-yujie",
    "中文女声-御姐": "female-yujie",
    "中文女声-清澈": "female-shaonv",
    "中文男声-成熟": "male-qn-jingying",
    "中文男声-年轻": "male-qn-badao",
    "中文男声-温暖": "male-qn-qingse",
    "中文男声-深沉": "male-qn-jingying",
    # 通用音色
    "speech-01": "speech-01",
    "speech-02": "speech-02",
    "speech-03": "speech-03",
    "speech-04": "speech-04",
    "speech-05": "speech-05",
    "speech-06": "speech-06",
    "speech-07": "speech-07",
    "speech-08": "speech-08",
    "speech-09": "speech-09",
    "speech-10": "speech-10",
}


class TTS:
    """千问平台 Minimax 语音合成"""
    
    def __init__(self, api_key: str, voice: str = "female-shaonv", model: str = "MiniMax/speech-2.8-turbo"):
        self.api_key = api_key
        self.voice = voice
        self.model = model
        # 千问平台专用端点（可用环境变量 TTS_BASE_URL 覆盖）
        self.base_url = os.getenv(
            "TTS_BASE_URL",
            "https://maas.qianwenaiapi.com/api/v1/services/aigc/multimodal-generation/generation",
        )
        
    async def text_to_speech(
        self, 
        text: str, 
        voice: Optional[str] = None,
        speed: float = 1.0,
        vol: float = 1.0,
        pitch: float = 0,
        sample_rate: int = 16000,
        format: str = "mp3"
    ) -> Optional[bytes]:
        """
        文字转语音
        
        Args:
            text: 要转换的文字
            voice: 音色名称，默认使用初始化时的音色
            speed: 语速，0.5-2.0，默认 1.0
            vol: 音量，0.1-10.0，默认 1.0
            pitch: 音调，-12-12，默认 0
            sample_rate: 采样率，默认 16000
            format: 音频格式，支持 mp3, wav, flac, amr, silk
        
        Returns:
            音频数据的 bytes，失败返回 None
        """
        if not self.api_key:
            logger.error("API Key 未设置")
            return None
            
        voice_id = voice or self.voice
        voice_id = VOICES.get(voice_id, voice_id)
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "input": {
                "action": "tts",
                "text": text,
                "voice_setting": {
                    "voice_id": voice_id,
                    "speed": speed,
                    "vol": vol,
                    "pitch": pitch
                },
                "audio_setting": {
                    "sample_rate": sample_rate,
                    "format": format,
                    "channel": 1
                }
            },
            "model": self.model
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.base_url, json=payload, headers=headers) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.error(f"TTS API 错误: {resp.status} - {error_text}")
                        return None
                    
                    result = await resp.json()
                    
                    # 检查 API 返回
                    base_resp = (result.get("output") or {}).get("base_resp") or {}
                    if base_resp and base_resp.get("status_code") != 0:
                        logger.error(f"TTS 错误: {base_resp.get('status_msg')}")
                        return None
                    if result.get("code") and result.get("code") != "Success":
                        err_msg = result.get("message", "未知错误")
                        logger.error(f"TTS 错误: {err_msg}")
                        return None
                    
                    # 获取音频数据（千问 MiniMax 返回在 output.data.audio）
                    output = result.get("output") or {}
                    audio_data = (output.get("data") or {}).get("audio") or output.get("audio") or result.get("data", {}).get("audio")
                    if not audio_data:
                        logger.error("TTS 未返回音频数据: %s", str(result)[:300])
                        return None
                    
                    # 解码 base64
                    audio_bytes = base64.b64decode(audio_data)
                    logger.info(f"成功生成语音，原始大小: {len(audio_bytes)} bytes")
                    return audio_bytes
                    
        except aiohttp.ClientError as e:
            logger.error(f"TTS 网络错误: {e}")
            return None
        except Exception as e:
            logger.exception(f"TTS 异常: {e}")
            return None
    
    async def text_to_speech_base64(
        self, 
        text: str, 
        voice: Optional[str] = None,
        **kwargs
    ) -> Optional[str]:
        """
        返回 base64 编码的音频字符串（可直接用于 OneBot 消息）
        
        Returns:
            base64 字符串，格式: base64://...
        """
        audio_bytes = await self.text_to_speech(text, voice, **kwargs)
        if audio_bytes:
            return "base64://" + base64.b64encode(audio_bytes).decode()
        return None


class CosyVoiceTTS:
    """千问平台 CosyVoice 合成，WebSocket 协议，支持复刻音色（推荐后端）"""

    WS_URL = os.getenv("COSY_WS_URL", "wss://maas.qianwenaiapi.com/api-ws/v1/inference")

    def __init__(self, api_key: str, voice: str,
                 model: str = "cosyvoice-v3.5-flash", sample_rate: int = 22050):
        self.api_key = api_key
        self.voice = voice      # 复刻音色 ID 或系统音色名
        self.model = model
        self.sample_rate = sample_rate

    async def text_to_speech(
        self,
        text: str,
        voice: Optional[str] = None,
        speed: float = 1.0,
        volume: int = 60,
    ) -> Optional[bytes]:
        if not self.api_key:
            logger.error("API Key 未设置")
            return None

        task_id = str(uuid.uuid4())
        audio = bytearray()
        ok = False
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(
                    self.WS_URL,
                    headers={"Authorization": f"bearer {self.api_key}"},
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as ws:
                    await ws.send_str(json.dumps({
                        "header": {"action": "run-task", "task_id": task_id, "streaming": "duplex"},
                        "payload": {
                            "task_group": "audio", "task": "tts",
                            "function": "SpeechSynthesizer", "model": self.model,
                            "parameters": {
                                "text_type": "PlainText",
                                "voice": voice or self.voice,
                                "format": "mp3",
                                "sample_rate": self.sample_rate,
                                "volume": volume,
                                "rate": speed,
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
                                ok = True
                                break
                            elif event == "task-failed":
                                logger.error("CosyVoice 任务失败: %s %s",
                                             header.get("error_code"), header.get("error_message"))
                                return None
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
        except Exception as e:
            logger.exception(f"CosyVoice 异常: {e}")
            return None

        if ok and audio:
            logger.info("CosyVoice 合成成功: %d bytes", len(audio))
            return bytes(audio)
        logger.error("CosyVoice 未返回音频")
        return None

    async def text_to_speech_base64(
        self,
        text: str,
        voice: Optional[str] = None,
        **kwargs,
    ) -> Optional[str]:
        audio_bytes = await self.text_to_speech(text, voice, **kwargs)
        if audio_bytes:
            return "base64://" + base64.b64encode(audio_bytes).decode()
        return None


def build_tts(api_key: str, voice: str, model: str):
    """按模型名自动选择后端：cosyvoice/qwen-audio → WebSocket，MiniMax → HTTP"""
    if model.startswith(("cosyvoice", "qwen-audio")):
        return CosyVoiceTTS(api_key, voice, model=model)
    return TTS(api_key, voice, model=model)


# 兼容旧代码
MinimaxTTS = TTS


async def test_tts():
    """测试函数"""
    import os
    api_key = os.getenv("DASHSCOPE_API_KEY", "")
    if not api_key:
        print("请设置 DASHSCOPE_API_KEY 环境变量")
        return
    
    tts = TTS(api_key, voice="female-shaonv")
    
    text = "你好呀！我是爱弥斯，使用千问平台的语音合成功能为你发送消息！"
    
    print(f"正在合成: {text}")
    audio = await tts.text_to_speech(text)
    
    if audio:
        with open("test_voice.mp3", "wb") as f:
            f.write(audio)
        print(f"成功！已保存为 test_voice.mp3，大小: {len(audio)} bytes")
    else:
        print("语音合成失败")


if __name__ == "__main__":
    asyncio.run(test_tts())
