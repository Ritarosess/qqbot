"""表情管理：只使用远程表情仓库（呜哇小站）

本地素材包已停用。所有表情都通过 wuwa_emoji 实时获取，并缓存到
emojis/wuwa_cache/（不上传 Git）。
"""
import logging
import os

logger = logging.getLogger("emoji")


class EmojiManager:
    def __init__(self, emoji_dir: str = "emojis"):
        # 保留参数以兼容旧调用；不再扫描本地文件
        self._emoji_dir = emoji_dir
        self._local_emojis: list[dict] = []

    # ---------- 远程表情 ----------
    async def get_random_any(self, character: str | None = None) -> dict | None:
        """远程随机表情；character 为 None 表示全站随机"""
        if os.getenv("WUWA_EMOJI_ENABLED", "1") != "1":
            return None
        try:
            import wuwa_emoji
            return await wuwa_emoji.random_emoji(character)
        except Exception as exc:  # noqa: BLE001
            logger.warning("获取远程表情失败: %s", exc)
            return None

    # ---------- 旧接口兼容（本地相关全部停用） ----------
    def reload_local(self):
        logger.info("本地素材包已停用，仅使用远程表情库")

    def load_qq_faces(self, faces: list):
        return

    def get_qq_face_stats(self) -> dict:
        return {"total": 0}

    def add_emoji(self, *args, **kwargs):
        return

    def find_by_keyword(self, keyword: str) -> dict | None:
        return None

    def get_random(self) -> dict | None:
        return None

    def tag_emoji(self, index: int, keyword: str) -> bool:
        return False

    def list_all(self) -> str:
        return "仅使用远程表情库（呜哇小站），不再使用本地素材包"
