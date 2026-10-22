"""
爱弥斯 · 网易云点歌分享插件

说「演奏 xxx / 来一首 xxx / 点一首 xxx」→ 搜网易云 → 把歌曲链接分享到群里。
也注册了 LLM 工具 share_netease_music，自然对话里说"分享/演奏某首歌"也能触发。

分享形式：文本消息（歌名 - 歌手 + 歌曲链接），QQ 会展示链接卡片。
"""
import re
import time

import aiohttp

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

SEARCH_URL = "https://music.163.com/api/search/get"
SONG_URL = "https://music.163.com/song?id={id}"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": "https://music.163.com/",
}


class AemeathMusicPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._last: dict[str, float] = {}
        logger.info("[aemeath_music] 网易云点歌插件已加载")

    # ---------- 搜索 ----------

    async def search(self, keyword: str, limit: int = 5) -> list[dict]:
        """返回 [{id, name, artist}]；失败返回空列表"""
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15), headers=HEADERS
            ) as session:
                async with session.get(
                    SEARCH_URL, params={"s": keyword, "type": 1, "limit": limit}
                ) as resp:
                    if resp.status != 200:
                        logger.warning("[aemeath_music] 搜索接口 HTTP %s", resp.status)
                        return []
                    data = await resp.json(content_type=None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[aemeath_music] 搜索失败: %s", exc)
            return []

        songs = []
        for item in ((data.get("result") or {}).get("songs") or [])[:limit]:
            artists = item.get("artists") or []
            songs.append({
                "id": item.get("id"),
                "name": item.get("name", ""),
                "artist": "、".join(a.get("name", "") for a in artists if a.get("name")),
            })
        return songs

    @staticmethod
    def _format(song: dict) -> str:
        line = f"🎵 {song['name']}"
        if song.get("artist"):
            line += f" - {song['artist']}"
        return f"{line}\n{SONG_URL.format(id=song['id'])}"

    # ---------- 触发 ----------

    def _match(self, text: str):
        prefixes = sorted(self.config.get("prefixes", []), key=len, reverse=True)
        for prefix in prefixes:
            if prefix and text.startswith(prefix):
                rest = text[len(prefix):].lstrip(" 　:：,，!！~～")
                if rest:
                    return rest.strip()
        return None

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_request(self, event: AstrMessageEvent):
        if not self.config.get("enabled", True):
            return
        text = (event.message_str or "").strip()
        keyword = self._match(text)
        if not keyword:
            return

        event.stop_event()
        now = time.time()
        key = event.unified_msg_origin
        if now - self._last.get(key, 0.0) < int(self.config.get("cooldown", 5)):
            return
        self._last[key] = now

        songs = await self.search(keyword, limit=int(self.config.get("limit", 5)))
        # 点歌结果不转语音（链接/歌名念出来没意义）
        event._aemeath_voice_sent = True
        if not songs:
            yield event.plain_result(f"唔…没搜到《{keyword}》，换个名字试试？")
            return
        yield event.plain_result(self._format(songs[0]))

    # ---------- LLM 工具 ----------

    @filter.llm_tool(name="share_netease_music")
    async def share_tool(self, event: AstrMessageEvent, song_name: str):
        """当用户想听/想让你分享某首歌时调用：搜索网易云并把歌曲链接分享到当前会话。song_name 为歌名（可带歌手）。"""
        songs = await self.search(song_name, limit=int(self.config.get("limit", 5)))
        if not songs:
            return f"没有在网易云找到《{song_name}》。"
        return self._format(songs[0])
