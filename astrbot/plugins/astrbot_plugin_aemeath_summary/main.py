"""
爱弥斯 · 每日总结插件（AstrBot）

- 记录群消息到 data/plugin_data/astrbot_plugin_aemeath_summary/daily/YYYY-MM-DD/<群号>.jsonl
- 每天 21:00（可配）为当天有记录的群生成总结并主动发送
- 管理员可用 /summary 手动触发

注意：总结为纯文字，发送前会剔除 [emoji]/[表情]/[图片] 等标记。
"""
import json
import sys
import time
from datetime import date
from pathlib import Path

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
except ImportError:  # 个别精简部署可能没有 apscheduler，此时只保留手动 /summary
    AsyncIOScheduler = None

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except Exception:  # 旧版本兜底
    def get_astrbot_data_path():
        return "data"

PLUGIN_NAME = "astrbot_plugin_aemeath_summary"
MARKER = ("[emoji", "[表情]", "[图片]", "[image", "[img")


class AemeathSummaryPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._scheduler: AsyncIOScheduler | None = None
        logger.info("[aemeath_summary] 每日总结插件已加载")

    # ---------- 路径 ----------

    def _root(self) -> Path:
        path = Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _day_dir(self, day: str) -> Path:
        path = self._root() / "daily" / day
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _umo_file(self) -> Path:
        return self._root() / "umo.json"

    def _load_umo(self) -> dict:
        try:
            return json.loads(self._umo_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_umo(self, umo: dict) -> None:
        try:
            self._umo_file().write_text(json.dumps(umo, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning("[aemeath_summary] 保存 umo 失败: %s", exc)

    # ---------- 记录 ----------

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def record(self, event: AstrMessageEvent):
        if not self.config.get("record_enabled", True):
            return
        gid = str(event.get_group_id() or "")
        text = (event.message_str or "").strip()
        if not gid or not text:
            return

        umo = self._load_umo()
        if umo.get(gid) != event.unified_msg_origin:
            umo[gid] = event.unified_msg_origin
            self._save_umo(umo)

        item = {
            "t": time.strftime("%H:%M"),
            "name": event.get_sender_name(),
            "text": text[:200],
        }
        try:
            with open(self._day_dir(date.today().isoformat()) / f"{gid}.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning("[aemeath_summary] 写入记录失败: %s", exc)

    # ---------- 定时 ----------

    @filter.on_astrbot_loaded()
    async def start_scheduler(self):
        if AsyncIOScheduler is None:
            logger.warning("[aemeath_summary] 未安装 apscheduler，定时总结不可用（/summary 仍可用）")
            return
        hour = int(self.config.get("hour", 21))
        minute = int(self.config.get("minute", 0))
        self._scheduler = AsyncIOScheduler()
        self._scheduler.add_job(
            self.run_summary,
            "cron",
            hour=hour,
            minute=minute,
            id="aemeath_daily_summary",
            replace_existing=True,
        )
        self._scheduler.start()
        logger.info("[aemeath_summary] 每日总结调度已启动：%02d:%02d", hour, minute)

    async def terminate(self):
        if self._scheduler:
            self._scheduler.shutdown(wait=False)

    # ---------- 生成与发送 ----------

    @filter.command("summary")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def summary_cmd(self, event: AstrMessageEvent):
        yield event.plain_result("开始生成今日总结…")
        await self.run_summary()

    async def run_summary(self):
        day = date.today().isoformat()
        day_dir = self._day_dir(day)
        umo_map = self._load_umo()
        min_msgs = int(self.config.get("min_messages", 5))

        for file in day_dir.glob("*.jsonl"):
            gid = file.stem
            try:
                lines = [json.loads(x) for x in file.read_text(encoding="utf-8").splitlines() if x.strip()]
            except (OSError, ValueError) as exc:
                logger.warning("[aemeath_summary] 读取 %s 失败: %s", file, exc)
                continue

            if len(lines) < min_msgs:
                logger.info("[aemeath_summary] 群 %s 消息太少(%d)，跳过", gid, len(lines))
                continue

            text = "\n".join(f"[{x['t']}] {x['name']}: {x['text']}" for x in lines[-50:])
            summary = await self._ask_llm(text)
            if not summary:
                logger.warning("[aemeath_summary] 群 %s 总结生成失败", gid)
                continue

            umo = umo_map.get(gid)
            if not umo:
                logger.warning("[aemeath_summary] 群 %s 缺少会话 umo，跳过发送", gid)
                continue

            await self.context.send_message(
                umo, MessageChain().message(f"📋 今日群聊总结 📋\n\n{summary}")
            )
            logger.info("[aemeath_summary] 群 %s 总结已发送", gid)

    async def _ask_llm(self, chat_text: str) -> str:
        prompt = (
            "请为以下群聊内容生成一份简洁的每日总结。\n"
            "要求：用爱弥斯的语气（元气俏皮，不要客服腔）；概括话题与活跃发言者；"
            "结尾一句鼓励；200 字以内；纯文字，禁止输出 [emoji]、[表情]、[图片] 等标记。\n\n"
            f"{chat_text}"
        )
        try:
            provider = self.context.get_using_provider()
            if provider is None:
                return ""
            resp = await provider.text_chat(prompt=prompt, system_prompt=self.config.get("persona", ""))
            text = getattr(resp, "completion_text", "") or ""
        except Exception as exc:  # noqa: BLE001
            logger.error("[aemeath_summary] 调用模型失败: %s", exc)
            return ""

        # 兜底清理占位标记
        for mark in MARKER:
            text = text.replace(mark, "")
        return text.strip()
