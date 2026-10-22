"""
群白名单运行时管理

- 初始值来自环境变量 GROUP_WHITELIST
- 首次运行后持久化到 WHITELIST_FILE（项目目录下 whitelist.json）
- 管理员可在私聊中用 /allow、/deny 动态增删
- 集合为空 = 不限制（对所有群生效）
"""
import json
import logging
import os

import config

logger = logging.getLogger("whitelist")

_groups: set[int] | None = None


def _path() -> str:
    return config.WHITELIST_FILE


def load() -> set[int]:
    """惰性加载白名单（首次从文件或环境变量初始化）"""
    global _groups
    if _groups is not None:
        return _groups

    path = _path()
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                _groups = {int(g) for g in data}
                logger.info("白名单已加载：%d 个群 (%s)", len(_groups), path)
                return _groups
            logger.warning("白名单文件格式异常，回退环境变量: %s", path)
        except (OSError, ValueError) as exc:
            logger.warning("读取白名单失败(%s): %s", path, exc)

    _groups = set(config.GROUP_WHITELIST)
    logger.info("白名单由环境变量初始化：%d 个群", len(_groups))
    _save()
    return _groups


def _save() -> bool:
    """原子写入，失败只记日志（不阻塞机器人运行）"""
    global _groups
    if _groups is None:
        return False
    path = _path()
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(sorted(_groups), f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except OSError as exc:
        logger.error("保存白名单失败(%s): %s", path, exc)
        return False


def all_groups() -> set[int]:
    return set(load())


def has_restriction() -> bool:
    """是否启用白名单限制（空集合 = 对所有群生效）"""
    return bool(load())


def allowed(group_id: int) -> bool:
    groups = load()
    return not groups or group_id in groups


def add(group_id: int) -> bool:
    """加入白名单；已存在返回 False"""
    groups = load()
    if group_id in groups:
        return False
    groups.add(group_id)
    _save()
    logger.info("白名单新增群 %d（当前 %d 个）", group_id, len(groups))
    return True


def remove(group_id: int) -> bool:
    """移出白名单；不存在返回 False"""
    groups = load()
    if group_id not in groups:
        return False
    groups.discard(group_id)
    _save()
    logger.info("白名单移除群 %d（剩余 %d 个）", group_id, len(groups))
    return True
