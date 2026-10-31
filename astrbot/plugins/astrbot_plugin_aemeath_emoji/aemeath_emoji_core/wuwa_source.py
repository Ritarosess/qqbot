"""呜哇小站「表情包仓鼠库」客户端（从旧版 wuwa_emoji.py 移植，配置驱动）

- 匿名可用，配置 token 可提高额度
- GET /v1alpha1/random?character=爱弥斯 → {id, url, format, ...}
- GET /v1alpha1/characters → 角色列表（69 个）
- 媒体链接是临时 ticket（约 15 分钟），命中后立即下载并缓存到本地
"""
import difflib
import json
import logging
import random
import time
from pathlib import Path

import aiohttp

logger = logging.getLogger("aemeath_emoji.wuwa")

DEFAULT_API_BASE = "https://emoji.wuwa.games/apis/api.random-emoji.wuwa.games/v1alpha1"
CACHE_LIMIT = 200
MAX_BYTES = 3 * 1024 * 1024  # 单张超过 3MB 就跳过，避免 base64 后消息过大
CHARACTERS_TTL = 24 * 3600  # 角色列表缓存 1 天

# 常见别名 / 错别字 / 社区外号（拼音已能覆盖大部分谐音，这里补上高频写法）
NAME_ALIASES = {
    "达尼亚": "达妮娅",
    "达尼娅": "达妮娅",
    "达妮雅": "达妮娅",
    "小爱": "爱弥斯",
    "小爱同学": "爱弥斯",
    "耙耙柑": "西格莉卡",
    "水蜜桃": "达妮娅",
    "糯糯": "弗洛洛",
    "娅娅": "达妮娅",
    "西西": "西格莉卡",
    "嘻嘻": "今汐",
    "xixi": "今汐",
    "夕夕": "今汐",
    "汐汐": "今汐",
    "青霄": "清宵",
}

ANY_CHARACTER = {"", "任意", "全部", "随机", "all", "any"}


def _pinyin(text: str) -> str:
    """转无声调拼音，用于谐音/错别字匹配"""
    try:
        from pypinyin import lazy_pinyin

        return "".join(lazy_pinyin(text)).lower()
    except Exception:  # noqa: BLE001
        return ""


class WuwaEmojiSource:
    def __init__(
        self,
        cache_dir: str,
        api_base: str = DEFAULT_API_BASE,
        token: str = "",
        character: str = "任意",
        cache_limit: int = CACHE_LIMIT,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.api_base = (api_base or DEFAULT_API_BASE).rstrip("/")
        self.token = (token or "").strip()
        self.character = (character or "任意").strip()
        self.cache_limit = cache_limit

    # ---------- 基础 ----------

    def _headers(self) -> dict:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            "Referer": "https://emoji.wuwa.games/",
            "Accept": "application/json",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _prune_cache(self):
        try:
            files = sorted(self.cache_dir.glob("*"), key=lambda p: p.stat().st_mtime)
            for old in files[:- self.cache_limit]:
                old.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("清理表情缓存失败: %s", exc)

    def count(self) -> int:
        exts = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
        return len([p for p in self.cache_dir.rglob("*") if p.suffix.lower() in exts])

    # ---------- 角色列表 ----------

    def _characters_path(self) -> Path:
        return self.cache_dir / "characters.json"

    def _load_characters(self) -> list[dict]:
        path = self._characters_path()
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []

    async def ensure_characters(self, force: bool = False) -> list[dict]:
        path = self._characters_path()
        if not force and path.exists() and (time.time() - path.stat().st_mtime) < CHARACTERS_TTL:
            return self._load_characters()

        timeout = aiohttp.ClientTimeout(total=20)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    f"{self.api_base}/characters",
                    params={"page": 1, "size": 100},
                    headers=self._headers(),
                ) as resp:
                    if resp.status != 200:
                        logger.warning("角色列表接口返回 %s", resp.status)
                        return self._load_characters()
                    data = await resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("获取角色列表失败: %s", exc)
            return self._load_characters()

        items = data.get("items") or []
        try:
            path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            logger.warning("写入角色列表缓存失败: %s", exc)
        logger.info("已更新角色列表，共 %d 个", len(items))
        return items

    # ---------- 角色匹配 ----------

    def _match_alias(self, key: str, items: list[dict]):
        names = {str(i.get("name", "")) for i in items}
        alias = NAME_ALIASES.get(key) or NAME_ALIASES.get(key.lower())
        if alias:
            return alias if alias in names else None

        low = key.lower()
        best = None
        for src, target in NAME_ALIASES.items():
            if not src:
                continue
            hit = (src.lower() in low) if src.isascii() else (src in key)
            if hit and (best is None or len(src) > len(best[0])):
                best = (src, target)
        if best:
            return best[1] if best[1] in names else None
        return None

    def find_character(self, keyword: str):
        key = (keyword or "").strip()
        if not key:
            return None
        items = self._load_characters()
        if not items:
            return None

        alias = self._match_alias(key, items)
        if alias:
            logger.info("别名匹配: %s -> %s", key, alias)
            return alias

        low = key.lower()
        for item in items:
            if low == str(item.get("name", "")).lower() or low == str(item.get("slug", "")).lower():
                return item.get("name")
        for item in items:
            name = str(item.get("name", ""))
            if name and (key in name or name in key):
                return name

        key_py = _pinyin(key)
        if key_py:
            for item in items:
                name = str(item.get("name", ""))
                if name and (_pinyin(name) == key_py or str(item.get("slug", "")).lower() == key_py):
                    logger.info("拼音匹配: %s -> %s", key, name)
                    return name

        if len(key) >= 3:
            best_name, best_score = None, 0.0
            for item in items:
                name = str(item.get("name", ""))
                if len(name) < 2:
                    continue
                score = difflib.SequenceMatcher(None, key, name).ratio()
                if score > best_score:
                    best_name, best_score = name, score
            if best_score >= 0.6:
                logger.info("角色名容错: %s -> %s (相似度 %.2f)", key, best_name, best_score)
                return best_name
        return None

    async def resolve_character(self, keyword: str):
        char = self.find_character(keyword)
        if char:
            return char
        await self.ensure_characters(force=True)
        return self.find_character(keyword)

    # ---------- 取图 ----------

    async def _download_item(self, session, emoji_id, media_url, fmt):
        if not emoji_id or not media_url:
            return None
        suffix = (fmt or "png").lower()
        path = self.cache_dir / f"{emoji_id}.{suffix}"

        if path.exists():
            if path.stat().st_size > MAX_BYTES:
                path.unlink(missing_ok=True)
            else:
                return {"path": str(path)}

        try:
            async with session.get(media_url, allow_redirects=True) as media:
                if media.status != 200:
                    logger.warning("下载远程表情失败 HTTP %s", media.status)
                    return None
                payload = await media.read()
            if len(payload) > MAX_BYTES:
                logger.warning("表情过大（%d 字节），跳过", len(payload))
                return None
            path.write_bytes(payload)
            self._prune_cache()
            return {"path": str(path)}
        except Exception as exc:  # noqa: BLE001
            logger.warning("下载远程表情异常: %s", exc)
            return None

    async def _random_from_list(self, session, character=None, attempts: int = 3):
        """从 /emojis 列表里随机取一张（/random 接口已 404，改用列表随机）"""
        for page in range(1, attempts + 1):
            params = {"page": page, "size": 50}
            if character:
                params["character"] = character
            try:
                async with session.get(
                    f"{self.api_base}/emojis",
                    params=params,
                    headers=self._headers(),
                ) as resp:
                    if resp.status != 200:
                        logger.warning("表情列表接口返回 %s（第 %d 页）", resp.status, page)
                        continue
                    data = await resp.json()
            except Exception as exc:  # noqa: BLE001
                logger.warning("获取表情列表失败（第 %d 页）: %s", page, exc)
                continue
            items = data.get("items") or []
            if not items:
                break
            random.shuffle(items)
            for item in items[:5]:
                result = await self._download_item(
                    session, item.get("id"), item.get("url"), item.get("format")
                )
                if result:
                    return result
            if not data.get("hasNext"):
                break
        return None

    async def _character_candidates(self, session, character: str, pages: int = 3) -> list[dict]:
        """取指定角色的「单角色合集」表情，排除多人合集（避免图里是别的角色）"""
        candidates: list[dict] = []
        for page in range(1, pages + 1):
            try:
                async with session.get(
                    f"{self.api_base}/emojis",
                    params={"character": character, "page": page, "size": 50},
                    headers=self._headers(),
                ) as resp:
                    if resp.status != 200:
                        break
                    data = await resp.json()
            except Exception as exc:  # noqa: BLE001
                logger.warning("查询角色表情失败: %s", exc)
                break
            for item in data.get("items", []):
                characters = item.get("characters") or []
                if len(characters) == 1 and characters[0].get("name") == character:
                    candidates.append(item)
            if not data.get("hasNext"):
                break
        return candidates

    async def random_emoji(self, character=None, attempts: int = 5):
        if character is not None and str(character).strip().lower() in ANY_CHARACTER:
            character = None
        char = character if character is not None else self.character
        if str(char).strip().lower() in ANY_CHARACTER:
            char = None

        timeout = aiohttp.ClientTimeout(total=25)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                if not char:
                    return await self._random_from_list(session, None)

                candidates = await self._character_candidates(session, char)
                if candidates:
                    random.shuffle(candidates)
                    for item in candidates[:attempts]:
                        result = await self._download_item(session, item.get("id"), item.get("url"), item.get("format"))
                        if result:
                            return result
                    logger.warning("角色 %s 的单角色表情下载失败，回退列表随机", char)
                else:
                    logger.warning("角色 %s 没有单角色合集，回退列表随机", char)
                return await self._random_from_list(session, char)
        except Exception as exc:  # noqa: BLE001
            logger.warning("创建 HTTP 会话失败: %s", exc)
            return None

    async def pick(self, keyword=None) -> Path | None:
        """keyword 为空 → 按配置角色随机；否则先匹配角色再取图"""
        character = None
        if keyword:
            character = await self.resolve_character(keyword)
            if not character:
                logger.info("关键词 %s 未匹配到角色，回退随机", keyword)
        result = await self.random_emoji(character)
        if not result:
            return None
        return Path(result["path"])
