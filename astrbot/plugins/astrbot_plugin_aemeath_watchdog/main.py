"""爱弥斯 · 掉线看门狗（通知侧）

主机侧的 `/opt/qqbot/watchdog.py`（systemd timer 每 2 分钟）负责：
- 检查 NapCat 的 QQ 进程是否还在
- 掉线时先尝试 NapCat 快捷登录，失败则重启 NapCat 生成新二维码
- 把「掉线开始/恢复」写进 `plugin_data/astrbot_plugin_aemeath_watchdog/state.json`

本插件轮询该文件，恢复后给管理员私聊发一条「刚才掉线 X 分钟，已恢复」；
`/掉线状态` 可随时查看当前看门狗状态。
"""
import asyncio
import json
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star
import astrbot.api.message_components as Comp

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except Exception:  # noqa: BLE001
    def get_astrbot_data_path():
        return "data"

PLUGIN_NAME = "astrbot_plugin_aemeath_watchdog"


class AemeathWatchdogPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._task = None
        logger.info("[aemeath_watchdog] 掉线看门狗（通知侧）已加载")

    # ---------- 路径 / 状态 ----------

    def _state_path(self) -> Path:
        return Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME / "state.json"

    def read_state(self) -> dict:
        try:
            return json.loads(self._state_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def write_state(self, data: dict) -> None:
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    # ---------- 通知 ----------

    def _admin_qq(self) -> str:
        qq = str(self.config.get("admin_qq") or "").strip()
        if qq:
            return qq
        try:
            admins = self.context.get_config().get("admins_id") or []
        except Exception:  # noqa: BLE001
            admins = []
        return str(admins[0]) if admins else ""

    async def notify_recovered(self) -> bool:
        data = self.read_state()
        pending = data.get("pending")
        if not pending:
            return False
        qq = self._admin_qq()
        if not qq:
            logger.warning("[aemeath_watchdog] 没有管理员 QQ，跳过通知")
            return False
        prefix = str(self.config.get("platform") or "aiocqhttp")
        umo = f"{prefix}:FriendMessage:{qq}"
        mins = max(1, int(pending.get("seconds") or 60) // 60)
        reason = str(pending.get("reason") or "").strip()
        text = f"⚠️ 刚才掉线了约 {mins} 分钟"
        if reason:
            text += f"（{reason}）"
        text += "，现在已经恢复。"
        try:
            await self.context.send_message(umo, MessageChain(chain=[Comp.Plain(text)]))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[aemeath_watchdog] 发送恢复通知失败: %s", exc)
            return False
        data["pending"] = None
        data["last_notified"] = pending.get("until")
        self.write_state(data)
        logger.info("[aemeath_watchdog] 已通知管理员：掉线 %s 分钟", mins)
        return True

    # ---------- 后台轮询 ----------

    @filter.on_astrbot_loaded()
    async def start_loop(self):
        if not self.config.get("enabled", True):
            return
        if self._task and not self._task.done():
            return
        self._task = asyncio.get_running_loop().create_task(self._loop())

    async def _loop(self) -> None:
        interval = max(20, int(self.config.get("interval", 60)))
        while True:
            try:
                await self.notify_recovered()
            except Exception as exc:  # noqa: BLE001
                logger.warning("[aemeath_watchdog] 轮询失败: %s", exc)
            await asyncio.sleep(interval)

    # ---------- 指令 ----------

    @filter.command("掉线状态", alias={"看门狗"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def status_cmd(self, event: AstrMessageEvent):
        data = self.read_state()
        if not data:
            yield event.plain_result("看门狗还没有状态文件（主机侧 watchdog 可能未安装/未运行）")
            return
        from datetime import datetime

        def fmt(ts):
            try:
                return datetime.fromtimestamp(float(ts)).strftime("%m-%d %H:%M")
            except (TypeError, ValueError, OSError):
                return "-"

        off = data.get("offline_since")
        lines = [
            "看门狗状态",
            f"当前：{'掉线中（自 ' + fmt(off) + '）' if off else '在线'}",
            f"原因：{data.get('reason') or '-'}",
            f"上次重启 NapCat：{fmt(data.get('last_restart')) if data.get('last_restart') else '-'}",
        ]
        yield event.plain_result("\n".join(lines))
        event.stop_event()
