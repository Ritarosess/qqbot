"""本地表情缓存源：扫描缓存目录里的图片（远程失败时的兜底）"""
import random
from pathlib import Path
from typing import Optional

EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


class LocalEmojiSource:
    def __init__(self, cache_dir: str):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _files(self) -> list[Path]:
        return [p for p in self.cache_dir.rglob("*") if p.suffix.lower() in EXTS]

    def count(self) -> int:
        return len(self._files())

    async def pick(self, keyword: Optional[str] = None) -> Optional[Path]:
        files = self._files()
        if not files:
            return None
        if keyword:
            key = keyword.strip().lower()
            matched = [p for p in files if key in str(p).lower()]
            if matched:
                files = matched
        return random.choice(files)
