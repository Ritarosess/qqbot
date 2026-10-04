"""
表情包管理器：支持本地文件夹 + QQ 收藏表情
"""
import os
import logging
from pathlib import Path

logger = logging.getLogger("emoji")


class EmojiManager:
    def __init__(self, emoji_dir: str = "emojis"):
        self._emoji_dir = Path(emoji_dir)
        self._local_emojis: list[dict] = []  # 本地表情包
        self._qq_faces: list[dict] = []       # QQ 收藏表情
        self._scan_local()

    # ========== 本地表情包 ==========
    def _scan_local(self):
        """扫描本地表情包文件夹"""
        self._local_emojis.clear()
        if not self._emoji_dir.exists():
            return

        exts = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}
        for file in self._emoji_dir.iterdir():
            if file.suffix.lower() in exts:
                keywords = file.stem.lower().split('_')
                self._local_emojis.append({
                    "type": "local",
                    "path": str(file.absolute()),
                    "keywords": keywords,
                    "name": file.stem,
                })
        logger.info("已扫描 %d 个本地表情包", len(self._local_emojis))

    def reload_local(self):
        """重新扫描本地表情包"""
        self._scan_local()

    # ========== QQ 收藏表情 ==========
    def load_qq_faces(self, faces: list):
        """加载 QQ 收藏表情（从 NapCat API）"""
        self._qq_faces.clear()
        for face in faces:
            # 尝试提取关键词和信息
            summary = ""
            if isinstance(face, dict):
                summary = face.get("summary", "") or face.get("desc", "") or face.get("name", "")
                face_id = face.get("id", "") or face.get("faceId", "")
                url = face.get("url", "") or face.get("qp", "") or face.get("path", "")
            else:
                summary = str(face)
                face_id = ""
                url = ""

            # 从 summary 提取关键词
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

    def get_qq_face_stats(self) -> dict:
        return {"total": len(self._qq_faces)}

    # ========== 群友分享的表情包（自动保存） ==========
    def add_emoji(self, file_id: str, url: str, keywords: list[str], sender_id: int, emoji_type: str = "local"):
        """添加表情包并下载到本地"""
        import aiohttp
        import asyncio

        self._local_emojis.append({
            "type": emoji_type,
            "path": url or file_id,
            "keywords": keywords,
            "name": f"share_{sender_id}",
            "sender_id": sender_id,
        })
        logger.info("新增表情包，当前共 %d 个", len(self._local_emojis) + len(self._qq_faces))

        # 下载到本地文件夹
        if url and emoji_type in ("image", "mface"):
            self._download_emoji(url, len(self._local_emojis))

    def _download_emoji(self, url: str, index: int):
        """下载表情包到本地"""
        import aiohttp
        import asyncio

        async def do_download():
            try:
                # 确定文件扩展名
                ext = ".jpg"
                if ".gif" in url:
                    ext = ".gif"
                elif ".png" in url:
                    ext = ".png"
                elif ".webp" in url:
                    ext = ".webp"

                filename = f"emoji_{index:03d}{ext}"
                filepath = self._emoji_dir / filename

                async with aiohttp.ClientSession() as session:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            data = await resp.read()
                            with open(filepath, "wb") as f:
                                f.write(data)
                            logger.info("已下载表情包: %s", filename)
                        else:
                            logger.warning("下载表情包失败: HTTP %d", resp.status)
            except Exception as e:
                logger.exception("下载表情包出错: %s", e)

        # 在事件循环中运行下载任务
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(do_download())
            else:
                loop.run_until_complete(do_download())
        except RuntimeError:
            # 没有事件循环，直接同步下载
            import requests
            try:
                resp = requests.get(url, timeout=30)
                if resp.status_code == 200:
                    filename = f"emoji_{index:03d}.jpg"
                    filepath = self._emoji_dir / filename
                    with open(filepath, "wb") as f:
                        f.write(resp.content)
                    logger.info("已下载表情包: %s", filename)
            except Exception as e:
                logger.exception("同步下载表情包出错: %s", e)

    # ========== 查找表情 ==========
    def find_by_keyword(self, keyword: str) -> dict | None:
        """根据关键词查找表情（优先 QQ 收藏）"""
        keyword_lower = keyword.lower()

        # 先搜 QQ 收藏表情
        for face in self._qq_faces:
            for kw in face["keywords"]:
                if keyword_lower in kw or kw in keyword_lower:
                    return face
            # 也搜 summary
            if keyword_lower in face.get("summary", "").lower():
                return face

        # 再搜本地表情包
        for emoji in self._local_emojis:
            for kw in emoji["keywords"]:
                if keyword_lower in kw or kw in keyword_lower:
                    return emoji

        return None

    def get_random(self) -> dict | None:
        """随机获取一个表情（优先 QQ 收藏）"""
        import random
        all_emojis = self._qq_faces + self._local_emojis
        if not all_emojis:
            return None
        return random.choice(all_emojis)

    def get_qq_face_stats(self) -> dict:
        return {"total": len(self._qq_faces)}

    def tag_emoji(self, index: int, keyword: str) -> bool:
        """给表情包添加标签"""
        all_emojis = self._qq_faces + self._local_emojis
        if 0 <= index < len(all_emojis):
            emoji = all_emojis[index]
            if keyword.lower() not in emoji["keywords"]:
                emoji["keywords"].append(keyword.lower())
                logger.info("给表情包 %s 添加标签: %s", emoji.get("name", "unknown"), keyword)
            return True
        return False

    def list_all(self) -> str:
        """列出所有表情（供管理员查看）"""
        lines = []
        all_emojis = self._qq_faces + self._local_emojis
        if not all_emojis:
            return "暂无表情包"

        for i, e in enumerate(all_emojis):
            emoji_type = "QQ收藏" if e["type"] == "qq_face" else "本地"
            keywords = "/".join(e["keywords"]) if e["keywords"] else "无标签"
            lines.append(f"{i+1}. [{emoji_type}] {e.get('summary', e.get('name', 'unknown'))} | 标签: {keywords}")
        return "\n".join(lines)
