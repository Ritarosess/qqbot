"""
爱弥斯 · 表情插件（AstrBot）

把 AI 回复里的 [emoji] / [emoji:角色名] 替换成图片消息段。
素材来源：呜哇小站远程仓库（下载后缓存）→ 本地缓存兜底。

指令（管理员）：
    /emoji  查看缓存素材数量
"""
import re
import sys
from pathlib import Path

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

sys.path.insert(0, str(Path(__file__).resolve().parent))
from aemeath_emoji_core.emoji_source import LocalEmojiSource  # noqa: E402
from aemeath_emoji_core.wuwa_source import WuwaEmojiSource  # noqa: E402

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except Exception:  # 旧版本兜底
    def get_astrbot_data_path():
        return "data"

MARKER = re.compile(r"\[emoji(?::([^\]]+))?\]")
STRAY = re.compile(r"\[(?:表情|图片|image|img|贴图)[^\]]*\]", re.IGNORECASE)


class AemeathEmojiPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        cache = self.config.get("cache_dir") or str(
            Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_aemeath_emoji" / "wuwa_cache"
        )
        self.local = LocalEmojiSource(cache)
        self.wuwa = None
        if self.config.get("remote_enabled", True):
            self.wuwa = WuwaEmojiSource(
                cache_dir=cache,
                api_base=self.config.get("api_base", ""),
                token=self.config.get("token", ""),
                character=self.config.get("character", "任意"),
            )
        logger.info(
            "[aemeath_emoji] 表情插件已加载，缓存目录: %s，远程源: %s",
            cache, "开" if self.wuwa else "关",
        )

    async def _pick(self, keyword):
        """远程优先，失败回退本地缓存"""
        if self.wuwa:
            try:
                path = await self.wuwa.pick(keyword)
                if path:
                    return path
            except Exception as exc:  # noqa: BLE001
                logger.warning("[aemeath_emoji] 远程取图失败，回退本地: %s", exc)
        return await self.local.pick(keyword)

    @filter.command("emoji")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def emoji_cmd(self, event: AstrMessageEvent):
        state = "开" if self.wuwa else "关"
        yield event.plain_result(
            f"表情缓存：{self.local.count()} 张（{self.local.cache_dir}）｜远程源：{state}"
        )

    @filter.on_decorating_result()
    async def decorate(self, event: AstrMessageEvent):
        """把文本里的表情标记换成图片消息段"""
        if not self.config.get("enabled", True):
            return
        result = event.get_result()
        if result is None:
            return

        chain = getattr(result, "chain", [])
        if not any(isinstance(seg, Comp.Plain) for seg in chain):
            return

        limit = int(self.config.get("max_per_message", 3))
        used = 0
        new_chain: list = []

        for seg in chain:
            if not isinstance(seg, Comp.Plain):
                new_chain.append(seg)
                continue

            text = seg.text
            pos = 0
            for m in MARKER.finditer(text):
                if used >= limit:
                    break
                keyword = (m.group(1) or "").strip() or None
                path = await self._pick(keyword)
                before = text[pos:m.start()]
                if before.strip():
                    new_chain.append(Comp.Plain(before))
                if path:
                    new_chain.append(Comp.Image.fromFileSystem(str(path)))
                    used += 1
                pos = m.end()

            rest = STRAY.sub("", text[pos:]).strip()
            if rest:
                new_chain.append(Comp.Plain(rest))

        # 清理整条链里残留的 [表情]/[图片] 等文字
        cleaned = []
        for seg in new_chain:
            if isinstance(seg, Comp.Plain):
                text = STRAY.sub("", seg.text).strip()
                if not text:
                    continue
                seg = Comp.Plain(text)
            cleaned.append(seg)

        if cleaned:
            result.chain = cleaned
