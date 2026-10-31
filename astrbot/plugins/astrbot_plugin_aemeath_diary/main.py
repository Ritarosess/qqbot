"""
爱弥斯 · 日记插件（AstrBot）

- 记录各群消息到 data/plugin_data/astrbot_plugin_aemeath_diary/daily/YYYY-MM-DD/<群号>.jsonl
- 每天 23:30（可配）把当天**所有群**的聊天记录合并成**一篇**爱弥斯第一人称日记：
  · 存档到 diary/YYYY-MM-DD.md（按天备份）
  · 追加到 diary/合集.md（长期记忆备份，上下文丢了也能翻）
  · 只发送给**管理员私聊**（send_to_admin，默认开；不往群里发）
- /日记 [YYYY-MM-DD] 查看某天日记（仅管理员，默认今天）
- /写日记（管理员）立即生成一次

说明：这份日记就是机器人的「记忆备份」——每日原始消息 JSONL + 日记 Markdown
都留在插件数据目录，换机或上下文丢失后可直接回看/注入。
"""
import json
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
except ImportError:  # 精简部署可能没有 apscheduler，此时只保留手动 /写日记
    AsyncIOScheduler = None

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except Exception:  # 旧版本兜底
    def get_astrbot_data_path():
        return "data"

PLUGIN_NAME = "astrbot_plugin_aemeath_diary"
MARKER = ("[emoji", "[表情]", "[图片]", "[image", "[img")

DIARY_PERSONA = (
    "你是爱弥斯（Aemeath），《鸣潮》里来自拉海洛的少女，星炬学院学生、校园歌手「飞行雪绒」，"
    "性格元气俏皮、好奇心旺盛。用户是《鸣潮》里的漂泊者，也是从小把你养大的人。\n"
    "现在你在写今天的日记。日记可以写得长一些、可以抒情，但不要客服腔、不要自称 AI、"
    "不要罗列条目，不要输出 [emoji]/[表情]/[图片] 这类标记。"
)

DIARY_PROMPT = (
    "下面是今天你在几个群里看到的聊天记录（【群1】【群2】…只是区分来源，写日记时不要提群号）。\n"
    "请以爱弥斯的身份写今天的日记。\n\n"
    "要求：\n"
    "- 第一人称，像真的日记：有自己的感受、细节和小情绪，不要写成总结报告；\n"
    "- 把几个群里有意思的事揉在一起写，像回忆一天的经历；\n"
    "- 提到今天最有趣的几件事、聊天里出现过的人，以及你自己插过的话；\n"
    "- 150 到 300 字，1 到 3 段，不要标题、不要列表、不要 [emoji] 等标记；\n"
    "- 按时间自然推进，别逐条复述聊天记录。\n\n"
    "聊天记录：\n{chat}\n"
)


class AemeathDiaryPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._scheduler: AsyncIOScheduler | None = None
        logger.info("[aemeath_diary] 日记插件已加载")

    # ---------- 路径 ----------

    def _root(self) -> Path:
        path = Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _day_dir(self, day: str) -> Path:
        path = self._root() / "daily" / day
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _diary_file(self, day: str) -> Path:
        path = self._root() / "diary"
        path.mkdir(parents=True, exist_ok=True)
        return path / f"{day}.md"

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
            logger.warning("[aemeath_diary] 保存 umo 失败: %s", exc)

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
            logger.warning("[aemeath_diary] 写入记录失败: %s", exc)

    # ---------- 定时 ----------

    @filter.on_astrbot_loaded()
    async def start_scheduler(self):
        if AsyncIOScheduler is None:
            logger.warning("[aemeath_diary] 未安装 apscheduler，定时写日记不可用（/写日记 仍可用）")
            return
        hour = int(self.config.get("hour", 23))
        minute = int(self.config.get("minute", 30))
        self._scheduler = AsyncIOScheduler()
        self._scheduler.add_job(
            self.run_diary,
            "cron",
            hour=hour,
            minute=minute,
            id="aemeath_daily_diary",
            replace_existing=True,
        )
        self._scheduler.start()
        logger.info("[aemeath_diary] 日记调度已启动：%02d:%02d", hour, minute)

    async def terminate(self):
        if self._scheduler:
            self._scheduler.shutdown(wait=False)

    # ---------- 指令 ----------

    @filter.command("日记")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def diary_cmd(self, event: AstrMessageEvent):
        """查看日记（仅管理员）：/日记 或 /日记 2026-10-24"""
        raw = (event.message_str or "").strip().split(maxsplit=1)
        arg = raw[1].strip() if len(raw) > 1 else ""
        day = self._parse_day(arg)
        if day is None:
            yield event.plain_result("日期格式：/日记 2026-10-24（也可以只写 10-24）")
            return

        path = self._diary_file(day)
        if not path.exists():
            yield event.plain_result(f"那天（{day}）还没有日记呢")
            return
        text = path.read_text(encoding="utf-8").strip()
        yield event.plain_result(f"📔 爱弥斯の日记 · {day}\n\n{text}")

    @filter.command("写日记")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def write_cmd(self, event: AstrMessageEvent):
        yield event.plain_result("爱弥斯拿起笔，开始写今天的日记…")
        await self.run_diary()

    # ---------- 生成 ----------

    @staticmethod
    def _parse_day(arg: str) -> str | None:
        arg = (arg or "").strip()
        if not arg:
            return date.today().isoformat()
        for fmt in ("%Y-%m-%d", "%m-%d"):
            try:
                dt = datetime.strptime(arg, fmt)
                if fmt == "%m-%d":
                    dt = dt.replace(year=date.today().year)
                return dt.date().isoformat()
            except ValueError:
                continue
        return None

    def _admin_umos(self) -> list[str]:
        """管理员的私聊 UMO 列表：优先配置 admin_qq，否则读 AstrBot 的 admins_id"""
        raw = str(self.config.get("admin_qq") or "").strip()
        ids: list[str] = []
        if raw:
            ids = [x for x in re.split(r"[,，\s]+", raw) if x]
        else:
            try:
                admins = self.context.get_config().get("admins_id") or []
                if isinstance(admins, str):
                    admins = [admins]
                ids = [str(x).strip() for x in admins if str(x).strip()]
            except Exception:  # noqa: BLE001
                logger.warning("[aemeath_diary] 读取管理员列表失败")
                ids = []

        prefix = "aiocqhttp"
        for value in self._load_umo().values():
            if isinstance(value, str) and value.count(":") >= 2:
                prefix = value.split(":", 1)[0]
                break
        return [f"{prefix}:FriendMessage:{i}" for i in ids]

    async def run_diary(self):
        day = date.today().isoformat()
        day_dir = self._day_dir(day)
        min_msgs = int(self.config.get("min_messages", 5))
        max_total = int(self.config.get("max_messages", 120))
        per_group = int(self.config.get("max_per_group", 40))

        groups: list[tuple[str, list[dict]]] = []
        for file in sorted(day_dir.glob("*.jsonl")):
            try:
                lines = [json.loads(x) for x in file.read_text(encoding="utf-8").splitlines() if x.strip()]
            except (OSError, ValueError) as exc:
                logger.warning("[aemeath_diary] 读取 %s 失败: %s", file, exc)
                continue
            if lines:
                groups.append((file.stem, lines[-per_group:]))

        total = sum(len(lines) for _, lines in groups)
        if total < min_msgs:
            logger.info("[aemeath_diary] %s 消息太少(%d)，跳过写日记", day, total)
            return

        merged: list[tuple[str, int, dict]] = []
        for idx, (gid, lines) in enumerate(groups, 1):
            for x in lines:
                merged.append((x.get("t", "00:00"), idx, x))
        merged.sort(key=lambda item: (item[0], item[1]))
        merged = merged[-max_total:]

        chat = "\n".join(
            f"[群{i} {t}] {x.get('name', '群友')}: {x.get('text', '')}"
            for t, i, x in merged
        )
        text = await self._write_diary(chat)
        if not text:
            logger.warning("[aemeath_diary] 日记生成失败")
            return

        # 落盘：按天存档 + 合集（长期记忆备份）
        diary_path = self._diary_file(day)
        try:
            diary_path.write_text(text + "\n", encoding="utf-8")
            with open(self._root() / "diary" / "合集.md", "a", encoding="utf-8") as f:
                f.write(f"\n## {day}\n\n{text}\n")
        except OSError as exc:
            logger.warning("[aemeath_diary] 写入日记失败: %s", exc)
        self._prune(day)
        logger.info(
            "[aemeath_diary] 日记已生成: %s（%d 个群、%d 条消息）",
            diary_path, len(groups), len(merged),
        )

        if not self.config.get("send_to_admin", True):
            return
        umos = self._admin_umos()
        if not umos:
            logger.warning("[aemeath_diary] 没有可用的管理员账号，日记只落盘不发送")
            return
        for umo in umos:
            try:
                await self.context.send_message(
                    umo, MessageChain().message(f"📔 爱弥斯の日记 · {day[5:]}\n\n{text}")
                )
                logger.info("[aemeath_diary] 日记已发送给管理员: %s", umo)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[aemeath_diary] 发送给 %s 失败: %s", umo, exc)

    async def _write_diary(self, chat_text: str) -> str:
        prompt = DIARY_PROMPT.format(chat=chat_text)
        persona = (self.config.get("persona") or "").strip() or DIARY_PERSONA
        try:
            provider = self.context.get_using_provider()
            if provider is None:
                return ""
            resp = await provider.text_chat(prompt=prompt, system_prompt=persona)
            text = getattr(resp, "completion_text", "") or ""
        except Exception as exc:  # noqa: BLE001
            logger.error("[aemeath_diary] 调用模型失败: %s", exc)
            return ""

        for mark in MARKER:
            text = text.replace(mark, "")
        return text.strip()

    def _prune(self, keep_after: str) -> None:
        """keep_days>0 时清理过老的按天日记（合集与原始记录不动）"""
        keep_days = int(self.config.get("keep_days", 0))
        if keep_days <= 0:
            return
        cutoff = date.today().toordinal() - keep_days
        diary_root = self._root() / "diary"
        if not diary_root.exists():
            return
        for f in diary_root.glob("*.md"):
            if f.stem == "合集":
                continue
            try:
                if datetime.strptime(f.stem, "%Y-%m-%d").date().toordinal() < cutoff:
                    f.unlink(missing_ok=True)
            except (ValueError, OSError):
                continue
