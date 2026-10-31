"""呜哇小站「表情包仓鼠库」随机表情 API 客户端

- 匿名可用，配置 WUWA_TOKEN 可提高额度
- 接口：GET /v1alpha1/random?character=爱弥斯 → {id, url, format, ...}
- 接口：GET /v1alpha1/characters → 角色列表（69 个）
- 媒体链接是临时 ticket（约 15 分钟），拼盘后需立即下载并缓存到本地
"""
import difflib
import json
import logging
import os
import random
import time
from pathlib import Path

import aiohttp

logger = logging.getLogger("wuwa")

DEFAULT_API_BASE = "https://emoji.wuwa.games/apis/api.random-emoji.wuwa.games/v1alpha1"
CACHE_LIMIT = 200
MAX_BYTES = 3 * 1024 * 1024  # 单张超过 3MB 就跳过，避免 base64 后消息过大
CHARACTERS_TTL = 24 * 3600   # 角色列表缓存 1 天

# 常见别名 / 错别字 / 社区外号（拼音已能覆盖大部分谐音，这里补上高频写法）
NAME_ALIASES = {
    # 错别字
    "达尼亚": "达妮娅",
    "达尼娅": "达妮娅",
    "达妮雅": "达妮娅",
    # 昵称
    "小爱": "爱弥斯",
    "小爱同学": "爱弥斯",
    # 社区外号
    "耙耙柑": "西格莉卡",
    "水蜜桃": "达妮娅",
    "糯糯": "弗洛洛",
    "娅娅": "达妮娅",
    "西西": "西格莉卡",
    # 谐音
    "嘻嘻": "今汐",
    "xixi": "今汐",
    "夕夕": "今汐",
    "汐汐": "今汐",
    # 错别字
    "青霄": "清宵",
}


def _pinyin(text: str) -> str:
    """转无声调拼音，用于谐音/错别字匹配"""
    try:
        from pypinyin import lazy_pinyin
        return "".join(lazy_pinyin(text)).lower()
    except Exception:  # noqa: BLE001
        return ""


def _api_base() -> str:
    return os.getenv("WUWA_API_BASE", DEFAULT_API_BASE).rstrip("/")


ANY_CHARACTER = {"", "任意", "全部", "随机", "all", "any"}


def _character() -> str | None:
    """返回角色名；任意角色时返回 None（不传 character 参数 = 全站随机）"""
    raw = os.getenv("WUWA_CHARACTER", "任意").strip()
    return None if raw.lower() in ANY_CHARACTER else raw


def _cache_dir() -> Path:
    raw = os.getenv("WUWA_CACHE_DIR", "")
    if not raw:
        raw = str(Path(os.getenv("EMOJI_DIR", "emojis")) / "wuwa_cache")
    path = Path(raw)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _headers() -> dict:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
        "Referer": "https://emoji.wuwa.games/",
        "Accept": "application/json",
    }
    token = os.getenv("WUWA_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _prune_cache(directory: Path):
    """缓存超过上限时删掉最旧的文件，避免无限增长"""
    try:
        files = sorted(directory.glob("*"), key=lambda p: p.stat().st_mtime)
        for old in files[:-CACHE_LIMIT]:
            old.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("清理表情缓存失败: %s", exc)


def _characters_path() -> Path:
    return _cache_dir() / "characters.json"


def _load_characters() -> list[dict]:
    path = _characters_path()
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


async def ensure_characters(force: bool = False) -> list[dict]:
    """确保角色列表可用（缓存 1 天），返回 [{slug,name,count}]"""
    path = _characters_path()
    if not force and path.exists() and (time.time() - path.stat().st_mtime) < CHARACTERS_TTL:
        return _load_characters()

    timeout = aiohttp.ClientTimeout(total=20)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                f"{_api_base()}/characters",
                params={"page": 1, "size": 100},
                headers=_headers(),
            ) as resp:
                if resp.status != 200:
                    logger.warning("角色列表接口返回 %s", resp.status)
                    return _load_characters()
                data = await resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("获取角色列表失败: %s", exc)
        return _load_characters()

    items = data.get("items") or []
    try:
        path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        logger.warning("写入角色列表缓存失败: %s", exc)
    logger.info("已更新角色列表，共 %d 个", len(items))
    return items


def _match_alias(key: str, items: list[dict]) -> str | None:
    """别名匹配：完全相等优先，其次包含（取最长别名）

    这样「来个青霄」「青霄的表情包」也能命中。
    """
    names = {str(i.get("name", "")) for i in items}

    alias = NAME_ALIASES.get(key) or NAME_ALIASES.get(key.lower())
    if alias:
        return alias if alias in names else None

    low = key.lower()
    best: tuple[str, str] | None = None
    for src, target in NAME_ALIASES.items():
        if not src:
            continue
        hit = (src.lower() in low) if src.isascii() else (src in key)
        if hit and (best is None or len(src) > len(best[0])):
            best = (src, target)
    if best:
        return best[1] if best[1] in names else None
    return None


def find_character(keyword: str) -> str | None:
    """在角色列表里匹配关键词，返回官方角色名

    支持：别名（相等/包含）/ 精确名 / slug / 包含匹配 / 拼音 / 形近字容错
    """
    key = (keyword or "").strip()
    if not key:
        return None
    items = _load_characters()
    if not items:
        return None

    alias = _match_alias(key, items)
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

    # 拼音匹配：解决谐音/错别字，如「达尼亚」「达尼娅」→「达妮娅」
    key_py = _pinyin(key)
    if key_py:
        for item in items:
            name = str(item.get("name", ""))
            if name and (_pinyin(name) == key_py or str(item.get("slug", "")).lower() == key_py):
                logger.info("拼音匹配: %s -> %s", key, name)
                return name

    # 形近字容错：只对 3 字及以上生效，避免「开心」误匹配到「心」
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


async def resolve_character(keyword: str) -> str | None:
    """先查本地缓存，没有就刷新一次角色列表再查"""
    char = find_character(keyword)
    if char:
        return char
    await ensure_characters(force=True)
    return find_character(keyword)


async def _download_item(session, emoji_id: str | None, media_url: str | None, fmt: str | None) -> dict | None:
    """下载并缓存单张表情，返回 EmojiManager 兼容结构"""
    if not emoji_id or not media_url:
        return None
    suffix = (fmt or "png").lower()
    directory = _cache_dir()
    path = directory / f"{emoji_id}.{suffix}"

    if path.exists():
        if path.stat().st_size > MAX_BYTES:
            path.unlink(missing_ok=True)
        else:
            return {"type": "wuwa", "name": emoji_id, "path": str(path), "keywords": ["wuwa", "表情"]}

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
        _prune_cache(directory)
        return {"type": "wuwa", "name": emoji_id, "path": str(path), "keywords": ["wuwa", "表情"]}
    except Exception as exc:  # noqa: BLE001
        logger.warning("下载远程表情异常: %s", exc)
        return None


async def _random_any(session, character: str | None = None, attempts: int = 3) -> dict | None:
    """全站随机（或 /random 接口随机）"""
    params = {"character": character} if character else {}
    for attempt in range(1, attempts + 1):
        try:
            async with session.get(
                f"{_api_base()}/random",
                params=params,
                headers=_headers(),
            ) as resp:
                if resp.status != 200:
                    logger.warning("远程表情接口返回 %s（第 %d 次）", resp.status, attempt)
                    continue
                data = await resp.json()
            result = await _download_item(session, data.get("id"), data.get("url"), data.get("format"))
            if result:
                char_name = (data.get("character") or {}).get("name") or character or "随机"
                result["keywords"].append(char_name)
                return result
        except Exception as exc:  # noqa: BLE001
            logger.warning("获取远程表情失败（第 %d 次）: %s", attempt, exc)
    return None


async def _character_candidates(session, character: str, pages: int = 3) -> list[dict]:
    """取指定角色的「单角色合集」表情，排除多人合集（避免图里是别的角色）"""
    candidates: list[dict] = []
    for page in range(1, pages + 1):
        try:
            async with session.get(
                f"{_api_base()}/emojis",
                params={"character": character, "page": page, "size": 50},
                headers=_headers(),
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


async def random_emoji(character: str | None = None, attempts: int = 5) -> dict | None:
    """从远程仓库随机取一张表情，返回 EmojiManager 兼容的 dict

    - character 为空/「任意」：全站随机
    - 指定角色：只从单角色合集中取，避免多人合集里出现别的角色
    """
    if character is not None and character.strip().lower() in ANY_CHARACTER:
        character = None
    char = character if character is not None else _character()

    timeout = aiohttp.ClientTimeout(total=25)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            if not char:
                return await _random_any(session, None)

            candidates = await _character_candidates(session, char)
            if candidates:
                random.shuffle(candidates)
                for item in candidates[:attempts]:
                    result = await _download_item(session, item.get("id"), item.get("url"), item.get("format"))
                    if result:
                        result["keywords"].append(char)
                        return result
                logger.warning("角色 %s 的单角色表情下载失败，回退 /random", char)
            else:
                logger.warning("角色 %s 没有单角色合集，回退 /random", char)
            return await _random_any(session, char)
    except Exception as exc:  # noqa: BLE001
        logger.warning("创建 HTTP 会话失败: %s", exc)
        return None
