"""
表情包管理器（纯素材包模式）

- 只扫描 emojis/ 下「文件名含中文标签」的图片（素材包命名：开心_happy_01.gif）
- 不再加载 QQ 收藏表情，也不再自动收藏群友图片
- 设置环境变量 EMOJI_QQ_FACES=1 可重新开启 QQ 收藏表情
"""
import os
import logging
from pathlib import Path

logger = logging.getLogger("emoji")


def _is_pack_file(stem: str) -> bool:
    """素材包文件：文件名里含中文，用来和旧版 emoji_001.jpg 区分"""
    return any("\u4e00" <= ch <= "\u9fff" for ch in stem)


class EmojiManager:
    def __init__(self, emoji_dir: str = "emojis"):
        self._emoji_dir = Path(emoji_dir)
        self._local_emojis: list[dict] = []  # 素材包表情
        self._qq_faces: list[dict] = []       # QQ 收藏表情（默认禁用）
        self._scan_local()

    # ========== 素材包 ==========
    def _scan_local(self):
        """扫描素材包目录，只收文件名含中文标签的图片"""
        self._local_emojis.clear()
        if not self._emoji_dir.exists():
            return

        exts = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}
        for file in self._emoji_dir.iterdir():
            if not file.is_file() or file.suffix.lower() not in exts:
                continue
            if not _is_pack_file(file.stem):
                continue
            keywords = [kw for kw in file.stem.lower().split('_') if kw]
            self._local_emojis.append({
                "type": "local",
                "path": str(file.absolute()),
                "keywords": keywords,
                "name": file.stem,
            })
        logger.info("已扫描 %d 个素材包表情", len(self._local_emojis))

    def reload_local(self):
        """重新扫描素材包"""
        self._scan_local()

    # ========== QQ 收藏表情（默认关闭） ==========
    def load_qq_faces(self, faces: list):
        """加载 QQ 收藏表情；EMOJI_QQ_FACES != 1 时忽略"""
        if os.getenv("EMOJI_QQ_FACES", "0") != "1":
            logger.info("QQ 收藏表情已禁用，忽略 %d 个", len(faces))
            return
        self._qq_faces.clear()
        for face in faces:
            summary = ""
            if isinstance(face, dict):
                summary = face.get("summary", "") or face.get("desc", "") or face.get("name", "")
                face_id = face.get("id", "") or face.get("faceId", "")
                url = face.get("url", "") or face.get("qp", "") or face.get("path", "")
            else:
                summary = str(face)
                face_id = ""
                url = ""

            keywords = summary.lower().split() if summary else []

            self._qq_faces.append({
                "type": "qq_face",
                "id": face_id,
                "url": url,
                "summary": summary,
                "keywords": keywords,
                "raw": face if isinstance(face, dict) else {},
            })
        logger.info("已加载 %d 个 QQ 收藏表情", len(self._qq_faces))

    # ========== 自动收藏（默认关闭） ==========
    def add_emoji(self, file_id: str, url: str, keywords: list[str], sender_id: int, emoji_type: str = "local"):
        """自动收藏群友表情；EMOJI_AUTO_COLLECT != 1 时不做任何事"""
        if os.getenv("EMOJI_AUTO_COLLECT", "0") != "1":
            return
        self._local_emojis.append({
            "type": emoji_type,
            "path": url or file_id,
            "keywords": keywords,
            "name": f"share_{sender_id}",
            "sender_id": sender_id,
        })
        logger.info("新增表情包，当前共 %d 个", len(self._local_emojis) + len(self._qq_faces))

    # ========== 查找表情 ==========
    def find_by_keyword(self, keyword: str) -> dict | None:
        """按关键词查找素材包表情；找不到返回 None（调用方应静默跳过）"""
        keyword_lower = keyword.strip().lower()
        if not keyword_lower:
            return None

        # 1) 精确命中某个关键词片段（开心 -> 开心_happy_01）
        for emoji in self._local_emojis:
            if keyword_lower in emoji["keywords"]:
                return emoji
        # 2) 退化为包含匹配（开心 -> 不开心）
        for emoji in self._local_emojis:
            for kw in emoji["keywords"]:
                if keyword_lower in kw or kw in keyword_lower:
                    return emoji
        return None

    def get_random(self) -> dict | None:
        """随机取一个素材包表情"""
        if not self._local_emojis:
            return None
        import random
        return random.choice(self._local_emojis)

    def get_qq_face_stats(self) -> dict:
        return {"total": len(self._qq_faces)}

    def tag_emoji(self, index: int, keyword: str) -> bool:
        """给素材包表情添加标签（仅内存生效）"""
        if 0 <= index < len(self._local_emojis):
            emoji = self._local_emojis[index]
            tag = keyword.strip().lower()
            if tag and tag not in emoji["keywords"]:
                emoji["keywords"].append(tag)
                logger.info("给表情包 %s 添加标签: %s", emoji.get("name", "unknown"), keyword)
            return True
        return False

    def list_all(self) -> str:
        """列出所有素材包表情（供管理员查看）"""
        if not self._local_emojis:
            return "暂无表情包"

        lines = []
        for i, e in enumerate(self._local_emojis):
            keywords = "/".join(e["keywords"]) if e["keywords"] else "无标签"
            lines.append(f"{i+1}. {e.get('name', 'unknown')} | 标签: {keywords}")
        return "\n".join(lines)
