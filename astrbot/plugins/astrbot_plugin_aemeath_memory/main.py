"""爱弥斯 · 长期记忆（AstrBot）

设计参考 SurfCore 的记忆卡（`memory-card.js`）：不是「把聊天记录全塞进上下文」，
而是把对话里**值得长期记住的事**抽出来存成记录，按人类记忆的规律慢慢变模糊。

规则：
  · 永久锚点：主人说过的话、手动 `/记住` 的内容 → pinned，永远清晰。
  · 最近 `recentDays` 天内的事至少「清楚」。
  · 之后按天数逐级降档：清晰 → 清楚 → 模糊 → 只剩印象 → 遗忘（一次只降一档）。
  · 被再次提到会刷新 `last_seen`，从模糊变回清楚（复习效应）。
  · 注入时只取「当前会话 + 全局」的记忆，按清晰度/重要度排序，受字数上限约束。

指令：
    /记忆              查看记得多少、最近几条
    /记住 <内容>       手动记一条（主人默认永久）
    /忘记 <关键词|id>  删掉匹配的记忆
    /记忆清空          清空（管理员）
"""
import asyncio
import json
import os
import re
import time
import uuid

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

try:  # 注入到用户内容里，避免和别的插件抢 system_prompt
    from astrbot.core.agent.message import TextPart
except Exception:  # noqa: BLE001
    TextPart = None  # type: ignore

DATA_DIR = os.environ.get("AEMEATH_MEMORY_DIR")
if not DATA_DIR:
    DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CARD_FILE = os.path.join(DATA_DIR, "memory.json")
REQ_FILE = os.path.join(DATA_DIR, "backfill_request.json")
STATUS_FILE = os.path.join(DATA_DIR, "backfill_status.json")
DONE_FILE = os.path.join(DATA_DIR, "backfill_done.json")
DIARY_DIR = os.environ.get("AEMEATH_DIARY_DIR",
                           "/AstrBot/data/plugin_data/astrbot_plugin_aemeath_diary")


CLARITY = ["vivid", "clear", "fuzzy", "trace", "forgotten"]
CLARITY_LABEL = {"vivid": "清晰", "clear": "清楚", "fuzzy": "模糊", "trace": "只剩印象", "forgotten": "遗忘"}
KEEP_LEVEL = {"vivid": 0, "clear": 1, "fuzzy": 2, "trace": 3, "forgotten": 4}

REDACT_PATTERNS = [
    (re.compile(r"(密码|口令|password|passwd)\s*[:：=]\s*\S+", re.I), r"\1: [已隐藏]"),
    (re.compile(r"(token|api[_-]?key|secret)\s*[:：=]\s*\S+", re.I), r"\1: [已隐藏]"),
    (re.compile(r"sk-[A-Za-z0-9_\-]{16,}"), "[已隐藏密钥]"),
    (re.compile(r"\b1[3-9]\d{9}\b"), "[已隐藏手机号]"),
    (re.compile(r"\b\d{17}[\dXx]\b"), "[已隐藏身份证]"),
    (re.compile(r"\b\d{16,19}\b"), "[已隐藏卡号]"),
]


BACKFILL_PROMPT = """下面是一个 QQ 群某一天的聊天记录（取样，非全部）。

请整理出**长期有用**的信息，最多 6 条：
- 人物：谁是谁、称呼、关系、性格、明确说过的偏好或讨厌的东西
- 事件：正在做的事（备考/工作/生病/旅行）、悬而未决的事、约定
- 梗：反复出现的梗、玩笑、专属称呼

不要写：寒暄、一次性情绪、没有信息量的废话、任何密码或联系方式。

输出 JSON 数组：
[{"text": "群友阿伟在准备考研，最近压力很大", "importance": 70, "who": "阿伟"}]
importance 1-100。只输出 JSON。

聊天记录：
{chat}"""


def _sample_lines(path: str, limit: int = 140, max_chars: int = 4000) -> str:
    """从 jsonl 里均匀取样，拼成聊天文本"""
    try:
        with open(path, encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
    except OSError:
        return ""
    if not lines:
        return ""
    if len(lines) > limit:
        step = len(lines) / limit
        lines = [lines[int(i * step)] for i in range(limit)]
    out = []
    for raw in lines:
        try:
            item = json.loads(raw)
        except ValueError:
            continue
        name = str(item.get("name") or "群友")[:20]
        text = str(item.get("text") or "").strip()
        if text:
            out.append(f"{name}: {text[:120]}")
    chat = "\n".join(out)
    return chat[-max_chars:]


EXTRACT_PROMPT = """你在帮一个叫爱弥斯的角色整理长期记忆。下面是最近的聊天记录。

只挑**值得长期记住**的内容，宁缺毋滥：
- 人物：谁是谁、称呼、关系、性格特点、明确说过的偏好/讨厌的东西
- 事件：正在做的事（备考、找工作、生病、旅行）、约定、承诺、重要的日子
- 梗：群里反复出现的梗、玩笑、专属称呼

不要记：闲聊寒暄、一次性情绪、复述别人的原话、无法理解的碎片、任何密码/联系方式。

输出 JSON 数组，最多 5 条，形如：
[{"text": "群友阿伟在准备考研，压力很大", "importance": 70, "who": "阿伟"}]
importance 1-100：主人相关、约定、长期状态给高分；一般偏好 40-60。
只输出 JSON，不要解释。

聊天记录：
{chat}"""


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, data) -> None:
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except OSError:
        pass


def redact(text: str) -> str:
    out = str(text or "")
    for pattern, repl in REDACT_PATTERNS:
        out = pattern.sub(repl, out)
    return out.strip()


def normalize_text(text: str) -> str:
    return re.sub(r"[\s，。,.!！?？~～]+", "", str(text or "")).lower()


def clarity_for(days: float, importance: int, pinned: bool, cfg: dict) -> str:
    """按天数算清晰度；pinned/高分记忆最差也保持「清楚」"""
    if pinned:
        return "vivid"
    recent = float(cfg.get("recentDays", 7))
    forget = float(cfg.get("forgetAfterDays", 60))
    if days <= 1:
        base = 0
    elif days <= recent:
        base = 1
    else:
        span = max(1.0, forget - recent)
        base = 1 + int((days - recent) / span * 3)
    level = CLARITY[min(4, max(0, base))]
    floor = 1 if (importance >= 65 or pinned) else 4
    if KEEP_LEVEL[level] > floor:
        level = CLARITY[floor]
    return level


def parse_extracted(raw: str) -> list[dict]:
    """从模型输出里抠出 JSON 数组，容错 markdown 代码块和前后废话"""
    text = str(raw or "").strip()
    if not text:
        return []
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return []
    out = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and str(item.get("text") or "").strip():
                try:
                    importance = int(item.get("importance") or 50)
                except (TypeError, ValueError):
                    importance = 50
                out.append({
                    "text": str(item["text"]).strip()[:300],
                    "importance": max(1, min(100, importance)),
                    "who": str(item.get("who") or "")[:40],
                })
            elif isinstance(item, str) and item.strip():
                out.append({"text": item.strip()[:300], "importance": 50, "who": ""})
    return out[:5]


class AemeathMemoryPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._lock = asyncio.Lock()
        self._records: list[dict] = []
        self._buffer: dict[str, list[str]] = {}
        self._last_extract: dict[str, float] = {}
        self._mtime = 0.0
        self._backfilling = False
        os.makedirs(DATA_DIR, exist_ok=True)
        self._load()
        logger.info("[aemeath_memory] 记忆插件已加载，现有 %d 条记忆", len(self._records))

    # ---------- 与设置面板协作 ----------

    def _maybe_reload(self) -> None:
        """面板改了 memory.json、或放了回填请求 → 热加载 / 触发回填"""
        try:
            mtime = os.path.getmtime(CARD_FILE)
        except OSError:
            mtime = 0.0
        if mtime and mtime != self._mtime:
            self._load()
            self._mtime = mtime
        req = read_json(REQ_FILE)
        if isinstance(req, dict) and req.get("days") and not self._backfilling:
            try:
                os.remove(REQ_FILE)
            except OSError:
                pass
            self._backfilling = True
            asyncio.create_task(self._backfill_task(int(req.get("days") or 7)))

    @filter.on_astrbot_loaded()
    async def start_watcher(self):
        async def loop():
            while True:
                try:
                    self._maybe_reload()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[aemeath_memory] 监听失败: %s", exc)
                await asyncio.sleep(20)

        asyncio.create_task(loop())
        logger.info("[aemeath_memory] 已启动面板协作监听")

    # ---------- 存取 ----------

    def _load(self) -> None:
        try:
            with open(CARD_FILE, encoding="utf-8") as f:
                data = json.load(f)
            records = data.get("records") if isinstance(data, dict) else data
            self._records = [r for r in (records or []) if isinstance(r, dict) and r.get("text")]
        except (OSError, ValueError):
            self._records = []
        self._decay()

    def _save(self) -> None:
        try:
            tmp = CARD_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "updated": int(time.time()), "records": self._records},
                          f, ensure_ascii=False, indent=1)
            os.replace(tmp, CARD_FILE)
        except OSError as exc:
            logger.warning("[aemeath_memory] 保存失败: %s", exc)

    def _decay(self) -> None:
        now = time.time()
        changed = False
        for rec in self._records:
            days = (now - float(rec.get("last_seen") or rec.get("created") or now)) / 86400
            target = clarity_for(days, int(rec.get("importance") or 50),
                                 bool(rec.get("pinned")), self.config)
            if rec.get("clarity") != target:
                rec["clarity"] = target
                changed = True
        if len(self._records) > int(self.config.get("maxRecords", 500)):
            self._records.sort(key=lambda r: (bool(r.get("pinned")), int(r.get("importance") or 0),
                                              float(r.get("last_seen") or 0)), reverse=True)
            keep = int(self.config.get("maxRecords", 500))
            forgotten = [r for r in self._records[keep:] if not r.get("pinned")]
            self._records = self._records[:keep] + forgotten[: max(0, 50 - (keep - len(self._records)))]
            changed = True
        if changed:
            self._save()

    def _add(self, text: str, scope: str, importance: int = 50, pinned: bool = False,
             source: str = "auto", who: str = "") -> dict | None:
        clean = redact(text)[:300]
        if len(clean) < 4:
            return None
        norm = normalize_text(clean)
        for rec in self._records:
            if normalize_text(rec.get("text")) == norm:
                rec["last_seen"] = time.time()
                rec["recalls"] = int(rec.get("recalls") or 0) + 1
                rec["clarity"] = "clear" if rec.get("clarity") in ("fuzzy", "trace") else rec.get("clarity")
                self._save()
                return None
        rec = {
            "id": uuid.uuid4().hex[:8],
            "text": clean,
            "scope": scope,
            "importance": max(1, min(100, int(importance))),
            "pinned": bool(pinned),
            "who": who,
            "created": time.time(),
            "last_seen": time.time(),
            "recalls": 0,
            "clarity": "clear",
            "source": source,
        }
        self._records.append(rec)
        self._save()
        return rec

    def _remember_owner(self, event: AstrMessageEvent) -> None:
        """主人开口就记一条永久锚点，之后被问「我是谁」至少知道是谁"""
        owner = str(self.config.get("ownerQQ") or "").strip()
        if not owner:
            return
        try:
            qq = str(event.get_sender_id() or "").strip()
            if qq != owner:
                return
            name = str(event.get_sender_name() or "").strip()
        except Exception:  # noqa: BLE001
            return
        tag = f"主人是群里的「{name}」"
        if any(r.get("scope") == "global" and r.get("pinned") and "主人是" in str(r.get("text"))
               for r in self._records):
            return
        self._add(f"{tag}，小爱平时叫他「漂泊者」，对他说话可以更亲近一点",
                  "global", 95, True, "owner", name)

    def _scope_of(self, event: AstrMessageEvent) -> str:
        gid = str(event.get_group_id() or "")
        return f"group:{gid}" if gid else "private"

    def _visible(self, scope: str) -> list[dict]:
        items = [r for r in self._records
                 if r.get("clarity") != "forgotten" and r.get("scope") in (scope, "global")]
        order = {"vivid": 0, "clear": 1, "fuzzy": 2, "trace": 3}
        items.sort(key=lambda r: (order.get(r.get("clarity"), 4), -int(r.get("importance") or 0),
                                  -float(r.get("last_seen") or 0)))
        return items

    def render_block(self, scope: str) -> str:
        budget = int(self.config.get("injectMaxChars", 1200))
        lines, used = [], 0
        for rec in self._visible(scope):
            line = f"-（{CLARITY_LABEL.get(rec.get('clarity'), '清楚')}）{rec['text']}"
            if used + len(line) > budget:
                break
            lines.append(line)
            used += len(line) + 1
            rec["last_seen"] = time.time()
            rec["recalls"] = int(rec.get("recalls") or 0) + 1
        if not lines:
            return ""
        return ("<memory>\n以下是你记得的事（按清晰度排序，模糊的是记不太清了）：\n"
                + "\n".join(lines) + "\n</memory>")

    # ---------- 自动抽取 ----------

    def _should_extract(self, scope: str) -> bool:
        if not self.config.get("autoExtract", True):
            return False
        buf = self._buffer.get(scope) or []
        if len(buf) < int(self.config.get("extractEvery", 20)):
            return False
        gap = float(self.config.get("extractCooldown", 900))
        return time.time() - self._last_extract.get(scope, 0) >= gap

    async def _extract(self, scope: str, chat: str) -> None:
        provider = self.context.get_using_provider()
        if provider is None:
            return
        try:
            resp = await provider.text_chat(
                prompt=EXTRACT_PROMPT.replace("{chat}", chat[-4000:]),
                system_prompt="你是一个只输出 JSON 的记忆整理器。",
                contexts=[],
            )
            items = parse_extracted(getattr(resp, "completion_text", "") or "")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[aemeath_memory] 抽取失败: %s", exc)
            return
        added = 0
        for item in items:
            rec = self._add(item["text"], scope, item.get("importance", 50),
                            source="auto", who=item.get("who", ""))
            added += 1 if rec else 0
        self._buffer[scope] = []
        self._last_extract[scope] = time.time()
        if added:
            logger.info("[aemeath_memory] %s 新记住 %d 条", scope, added)


    # ---------- 历史回填 ----------

    def _backfill_targets(self, days: int) -> list[tuple[str, str, str]]:
        done = set(read_json(DONE_FILE, []) or [])
        target = []
        daily_dir = os.path.join(DIARY_DIR, "daily")
        try:
            dates = sorted(os.listdir(daily_dir))[-max(1, days):]
        except OSError:
            logger.warning("[aemeath_memory] 找不到历史记录目录: %s", daily_dir)
            return []
        for date in dates:
            day_dir = os.path.join(daily_dir, date)
            if not os.path.isdir(day_dir):
                continue
            for name in sorted(os.listdir(day_dir)):
                if not name.endswith(".jsonl"):
                    continue
                gid = name[:-6]
                key = f"{date}:{gid}"
                if key in done:
                    continue
                target.append((date, gid, os.path.join(day_dir, name), key))
        return target

    async def _extract_profile(self, provider, chat: str) -> list[dict]:
        resp = await provider.text_chat(
            prompt=BACKFILL_PROMPT.replace("{chat}", chat),
            system_prompt="你是一个只输出 JSON 的记忆整理器。",
            contexts=[],
        )
        return parse_extracted(getattr(resp, "completion_text", "") or "")

    async def _backfill_task(self, days: int) -> None:
        status = {"state": "running", "days": days, "done": 0, "total": 0,
                  "added": 0, "error": "", "started": time.time()}
        write_json(STATUS_FILE, status)
        done = set(read_json(DONE_FILE, []) or [])
        try:
            provider = self.context.get_using_provider()
            if provider is None:
                raise RuntimeError("没有可用的对话模型")
            targets = self._backfill_targets(days)
            status["total"] = len(targets)
            write_json(STATUS_FILE, status)
            for date, gid, path, key in targets:
                chat = _sample_lines(path)
                if len(chat) > 120:
                    for item in await self._extract_profile(provider, chat):
                        rec = self._add(item["text"], f"group:{gid}",
                                        max(50, int(item.get("importance") or 60)),
                                        source="backfill", who=item.get("who", ""))
                        if rec:
                            status["added"] += 1
                    done.add(key)
                    write_json(DONE_FILE, sorted(done))
                status["done"] += 1
                write_json(STATUS_FILE, status)
                await asyncio.sleep(1.5)
            status["state"] = "done"
        except Exception as exc:  # noqa: BLE001
            status["state"] = "error"
            status["error"] = str(exc)
            logger.warning("[aemeath_memory] 回填失败: %s", exc)
        finally:
            status["finished"] = time.time()
            write_json(STATUS_FILE, status)
            self._backfilling = False
            logger.info("[aemeath_memory] 回填结束：%s 条", status.get("added"))

    @filter.command("记忆回填")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def backfill_cmd(self, event: AstrMessageEvent):
        """把历史日记回填成记忆（管理员）"""
        if self._backfilling:
            yield event.plain_result("正在回填，别急")
            return
        parts = (event.message_str or "").split()
        days = 7
        if len(parts) > 1 and parts[1].isdigit():
            days = max(1, min(14, int(parts[1])))
        self._backfilling = True
        asyncio.create_task(self._backfill_task(days))
        yield event.plain_result(f"开始回填最近 {days} 天的群聊记录，完成后我会告诉你")

    # ---------- 事件 ----------

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        if not self.config.get("enabled", True):
            return
        text = (event.message_str or "").strip()
        if not text or text.startswith("/") or len(text) < 4:
            return
        scope = self._scope_of(event)
        self._remember_owner(event)
        buf = self._buffer.setdefault(scope, [])
        sender = str(getattr(event, "get_sender_id", lambda: "")())
        buf.append(f"{sender}: {text[:200]}")
        if len(buf) > 40:
            del buf[:-40]
        if self._should_extract(scope):
            chat = "\n".join(buf[-40:])
            self._last_extract[scope] = time.time()
            asyncio.create_task(self._extract(scope, chat))

    @filter.on_llm_request()
    async def inject_memory(self, event: AstrMessageEvent, req):
        if not self.config.get("enabled", True):
            return
        block = self.render_block(self._scope_of(event))
        # 让模型知道「这条消息是谁发的」，否则被问「我是谁」只能干瞪眼
        who = ""
        try:
            name = str(event.get_sender_name() or "").strip()
            qq = str(event.get_sender_id() or "").strip()
            if qq:
                # 用群昵称指代人（主人另外标一下身份），不报 QQ 号给模型念
                label = name or "群友"
                owner_tag = "（你的主人）" if qq == str(self.config.get("ownerQQ") or "") else ""
                who = f"<speaker>这条消息来自 {label}{owner_tag}</speaker>\n"
        except Exception:  # noqa: BLE001
            who = ""
        if not block and not who:
            return
        block = who + block
        logger.info("[aemeath_memory] 注入 %d 字（scope=%s）", len(block), self._scope_of(event))
        if TextPart is not None:
            req.extra_user_content_parts.append(TextPart(text=block).mark_as_temp())
        else:
            req.system_prompt = (getattr(req, "system_prompt", "") or "") + "\n\n" + block

    # ---------- 指令 ----------

    @filter.command("记忆")
    async def show_memory(self, event: AstrMessageEvent):
        """看看爱弥斯记得什么"""
        scope = self._scope_of(event)
        mine = [r for r in self._records if r.get("scope") in (scope, "global")
                and r.get("clarity") != "forgotten"]
        total = len([r for r in self._records if r.get("clarity") != "forgotten"])
        if not mine:
            yield event.plain_result(f"这个会话还没记下什么（全部记忆 {total} 条）")
            return
        top = self._visible(scope)[:5]
        lines = [f"- {r['text']}（{CLARITY_LABEL.get(r.get('clarity'), '清楚')}）" for r in top]
        yield event.plain_result(f"这个会话相关记忆 {len(mine)} 条，全部 {total} 条。最近记得的：\n"
                                 + "\n".join(lines) + "\n\n想删掉哪条用 /忘记 <关键词>")

    @filter.command("记住")
    async def remember(self, event: AstrMessageEvent):
        """手动记一条"""
        text = (event.message_str or "").strip()
        parts = text.split(maxsplit=1)
        if len(parts) < 2 or len(parts[1].strip()) < 2:
            yield event.plain_result("用法：/记住 阿伟在准备考研")
            return
        scope = self._scope_of(event)
        rec = self._add(parts[1].strip(), scope, importance=80, pinned=True, source="manual")
        if rec:
            yield event.plain_result("记住啦")
        else:
            yield event.plain_result("这条已经记过了")

    @filter.command("忘记")
    async def forget(self, event: AstrMessageEvent):
        """删掉匹配的记忆"""
        text = (event.message_str or "").strip()
        parts = text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            yield event.plain_result("用法：/忘记 考研（或 /忘记 记录的 id）")
            return
        key = parts[1].strip()
        scope = self._scope_of(event)
        hits = [r for r in self._records
                if r.get("scope") in (scope, "global")
                and (key == r.get("id") or key in r.get("text", ""))]
        if not hits:
            yield event.plain_result("没找到匹配的记忆")
            return
        for rec in hits:
            self._records.remove(rec)
        self._save()
        yield event.plain_result(f"忘了 {len(hits)} 条：" + "、".join(r["text"][:12] for r in hits[:3]))

    @filter.command("记忆清空")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def clear_memory(self, event: AstrMessageEvent):
        """清空全部记忆"""
        count = len(self._records)
        self._records = []
        self._buffer.clear()
        self._save()
        yield event.plain_result(f"已清空 {count} 条记忆")

    async def terminate(self):
        self._save()
