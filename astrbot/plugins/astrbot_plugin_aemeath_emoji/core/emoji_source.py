"""表情素材来源（骨架）

迁移时把项目的 wuwa_emoji.py / emoji_manager.py 的下载与角色匹配逻辑搬到这里，
对外保持 pick(keyword) -> Optional[Path] 接口即可，主插件不用改。

当前实现：扫描缓存目录里的图片，按文件名/子目录名做简单匹配，够先跑通链路。
"""
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

    def pick(self, keyword: Optional[str] = None) -> Optional[Path]:
        files = self._files()
        if not files:
            return None
        if keyword:
            key = keyword.strip().lower()
            matched = [p for p in files if key in str(p).lower()]
            if matched:
                files = matched
        return random.choice(files)
