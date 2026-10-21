"""
AI 对话客户端，兼容 OpenAI 格式接口
"""
import asyncio
import time
import aiohttp
import logging

logger = logging.getLogger("ai")


class AIClient:
    def __init__(self, api_base: str, api_key: str, model: str,
                 timeout: int = 60, max_retries: int = 3, backoff: int = 30):
        self._api_base = api_base.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff = backoff
        self._next_allowed_time = 0.0

    async def chat(self, messages: list[dict]) -> str | None:
        now = time.time()
        if now < self._next_allowed_time:
            wait = int(self._next_allowed_time - now)
            logger.warning("AI 限流中，还需等待 %d 秒", wait)
            return None

        url = f"{self._api_base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {"model": self._model, "messages": messages}

        for attempt in range(1, self._max_retries + 1):
            try:
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=self._timeout)
                ) as session:
                    async with session.post(url, json=payload, headers=headers) as resp:
                        if resp.status == 429:
                            self._next_allowed_time = time.time() + self._backoff
                            logger.warning("收到 429，触发 %ds 退避", self._backoff)
                            return None
                        resp.raise_for_status()
                        data = await resp.json()
                        return data["choices"][0]["message"]["content"].strip()
            except aiohttp.ClientError as e:
                logger.error("AI 请求失败(第%d次): %s", attempt, e)
                if attempt < self._max_retries:
                    await asyncio.sleep(2 * attempt)
        return None
