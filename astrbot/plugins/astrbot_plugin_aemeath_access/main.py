"""
爱弥斯 · 群白名单插件（AstrBot）

指令（管理员，私聊/群聊均可）：
    /allow <群号>   加入白名单
    /allow list     查看白名单
    /deny <群号>    移出白名单

白名单为空 = 不限制（对所有群生效）。
数据存于 AstrBot 插件 KV，重启/升级不丢。
"""
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

KV_KEY = "groups"


class AemeathAccessPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        logger.info("[aemeath_access] 群白名单插件已加载")

    # ---------- 存储 ----------

    async def _groups(self) -> list[int]:
        raw = await self.get_kv_data(KV_KEY, [])
        return sorted({int(g) for g in raw})

    async def _save(self, groups) -> None:
        await self.put_kv_data(KV_KEY, sorted({int(g) for g in groups}))

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
                f"已把群 {gid} 移出白名单。⚠️ 白名单已为空，现在对所有群生效，需要限制请再 /allow"
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
