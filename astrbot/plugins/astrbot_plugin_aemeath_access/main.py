"""
爱弥斯 · 群白名单插件（AstrBot）

白名单存**插件配置文件**的 `whitelistGroups` 字段（逗号分隔），因此可以直接在设置面板里
添加/删除群，保存后**无需重启**（每次判断都会读一次配置文件，mtime 变了就重读）。

指令（管理员，私聊/群聊均可）：
    /allow <群号>   加入白名单
    /allow list     查看白名单
    /deny <群号>    移出白名单
    /status         查看运行状态与白名单

白名单为空 = 不限制（对所有群生效）。
旧版本把白名单存在插件 KV 里，首次运行会自动迁移到配置字段，方便面板统一管理。
"""
import inspect
import json
import os
import re

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

KV_KEY = "groups"
CFG_KEY = "whitelistGroups"


def parse_ids(raw) -> list[int]:
    """把逗号/空格分隔的群号（或 KV 列表）解析成去重排序的 int 列表"""
    out = set()
    if isinstance(raw, (list, tuple, set)):
        items = raw
    else:
        items = re.split(r"[,，\s]+", str(raw or ""))
    for item in items:
        text = str(item).strip()
        if text.isdigit():
            out.add(int(text))
    return sorted(out)


class AemeathAccessPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._file_mtime = 0.0
        self._file_ids: list[int] | None = None
        logger.info("[aemeath_access] 群白名单插件已加载")

    # ---------- 存储 ----------

    def _config_path(self) -> str:
        path = getattr(self.config, "config_path", "")
        return path if isinstance(path, str) else ""

    def _read_file_ids(self) -> list[int] | None:
        """直接读配置文件里的 whitelistGroups（面板保存后无需重启即可生效）"""
        path = self._config_path()
        if not path or not os.path.exists(path):
            return None
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return None
        if self._file_ids is not None and mtime == self._file_mtime:
            return self._file_ids
        try:
            with open(path, encoding="utf-8-sig") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return self._file_ids
        if isinstance(data, dict) and CFG_KEY in data:
            self._file_ids = parse_ids(data.get(CFG_KEY))
            self._file_mtime = mtime
            return self._file_ids
        return None

    async def _persist(self) -> None:
        fn = getattr(self.config, "save_config_async", None)
        if fn is not None:
            try:
                result = fn()
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:  # noqa: BLE001
                logger.warning("[aemeath_access] 保存配置失败: %s", exc)
            return
        fn = getattr(self.config, "save_config", None)
        if fn is not None:
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                logger.warning("[aemeath_access] 保存配置失败: %s", exc)

    async def _groups(self) -> list[int]:
        from_file = self._read_file_ids()
        if from_file is not None:
            groups = from_file
        else:
            groups = parse_ids(self.config.get(CFG_KEY))

        # 迁移：旧版把白名单存在 KV 里
        legacy = parse_ids(await self.get_kv_data(KV_KEY, []))
        if legacy:
            groups = sorted(set(groups) | set(legacy))
            self.config[CFG_KEY] = ",".join(map(str, groups))
            await self._persist()
            await self.put_kv_data(KV_KEY, [])
            logger.info("[aemeath_access] 已把旧 KV 白名单迁移到配置（%d 个群）", len(groups))
        return groups

    async def _save(self, groups) -> None:
        clean = sorted({int(g) for g in groups})
        self.config[CFG_KEY] = ",".join(map(str, clean))
        await self._persist()
        self._file_ids = None
        self._file_mtime = 0.0

    # ---------- 指令 ----------

    @staticmethod
    def _args(event: AstrMessageEvent) -> list[str]:
        parts = (event.message_str or "").strip().split()
        return parts[1:] if parts else []

    @filter.command("allow")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def allow(self, event: AstrMessageEvent):
        """把群加入白名单；/allow list 查看"""
        args = self._args(event)
        groups = await self._groups()

        if not args:
            yield event.plain_result("用法: /allow <群号>  或  /allow list")
            return

        if args[0].lower() in ("list", "列表"):
            if not groups:
                yield event.plain_result("白名单为空（对所有群生效）")
            else:
                yield event.plain_result("白名单：" + "、".join(map(str, groups)))
            return

        if not args[0].isdigit():
            yield event.plain_result("群号必须是数字，例如 /allow 123456789")
            return

        gid = int(args[0])
        if gid in groups:
            yield event.plain_result(f"群 {gid} 已经在白名单里啦")
            return

        groups.append(gid)
        await self._save(groups)
        logger.info("[aemeath_access] 白名单新增群 %d（共 %d 个）", gid, len(groups))
        yield event.plain_result(f"已把群 {gid} 加入白名单（当前 {len(groups)} 个）")

    @filter.command("deny")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def deny(self, event: AstrMessageEvent):
        """把群移出白名单"""
        args = self._args(event)
        if not args or not args[0].isdigit():
            yield event.plain_result("用法: /deny <群号>")
            return

        gid = int(args[0])
        groups = await self._groups()
        if gid not in groups:
            yield event.plain_result(f"群 {gid} 不在白名单里")
            return

        groups.remove(gid)
        await self._save(groups)
        logger.info("[aemeath_access] 白名单移除群 %d（剩 %d 个）", gid, len(groups))
        if groups:
            yield event.plain_result(f"已把群 {gid} 移出白名单（剩余 {len(groups)} 个）")
        else:
            yield event.plain_result(
                f"已把群 {gid} 移出白名单。注意：白名单已为空，现在对所有群生效，需要限制请再 /allow"
            )

    @filter.command("status")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def status(self, event: AstrMessageEvent):
        groups = await self._groups()
        if groups:
            yield event.plain_result("运行中｜白名单：" + "、".join(map(str, groups)))
        else:
            yield event.plain_result("运行中｜白名单：全部群（未限制）")

    # ---------- 群消息拦截 ----------

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def gate_group(self, event: AstrMessageEvent):
        """不在白名单的群直接终止事件，不进入后续 LLM 流程"""
        if not self.config.get("enforce", True):
            return
        groups = await self._groups()
        if not groups:
            return
        try:
            gid = int(event.get_group_id())
        except (TypeError, ValueError):
            return
        if gid not in groups:
            logger.debug("[aemeath_access] 群 %s 不在白名单，拦截", gid)
            event.stop_event()
