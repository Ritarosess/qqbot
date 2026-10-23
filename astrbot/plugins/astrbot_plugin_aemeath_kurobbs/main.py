"""
爱弥斯 · 库街区 WIKI 查询插件（AstrBot）

数据源：库街区（kurobbs.com）公开 WIKI JSON API，经由 vendor 的 kurobbs-wiki
查询脚本（https://github.com/Alphamancer/kurobbs-wiki ，MIT License）子进程调用。

指令：
    /材料 <角色名>          角色突破材料合计（0→90，全角色覆盖，补 B站WIKI 缺口）
    /攻略 <角色名>          武器推荐排名 + 声骸套装 + 主流队伍（库街区官方攻略）
    /配队 <角色名> [池子]    配队引擎：不给池子用攻略点名补池，给了就用你的池子
    /kuro <关键词>          库街区全站搜索（图鉴/攻略/道具都能搜）

LLM 工具 wuwa_kurobbs_lookup：自然对话里问攻略/材料/配队也能触发。

注意：
    - 库街区是无鉴权私有 API，脚本内置 0.05s 限速，插件再加会话冷却与串行锁
    - 攻略标注「仅供当期参考」，卡片会带上
    - 账号功能（my login/roles）与社区帖抓取（post，需 Playwright）未接入
"""
import asyncio
import html as html_mod
import json
import os
import re
import sys
import time
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except Exception:  # 旧版本兜底
    def get_astrbot_data_path():
        return "data"

SCRIPT = Path(__file__).resolve().parent / "vendor" / "kurobbs-wiki" / "scripts" / "wikiquery.py"
ITEM_URL = "https://wiki.kurobbs.com/mc/item/{id}"
MAX_OUTPUT = 400_000  # 子进程输出上限，防撑爆内存

# 常见别名（与 aemeath_wiki 的 ALIASES 保持同步）
ALIASES = {
    "小爱": "爱弥斯", "小爱同学": "爱弥斯",
    "达尼亚": "达妮娅", "达尼娅": "达妮娅", "达妮雅": "达妮娅", "娅娅": "达妮娅",
    "水蜜桃": "达妮娅", "耙耙柑": "西格莉卡", "西西": "西格莉卡",
    "糯糯": "弗洛洛", "嘻嘻": "今汐", "xixi": "今汐", "夕夕": "今汐", "汐汐": "今汐",
    "青霄": "清宵", "漂子": "漂泊者", "大爷": "卡卡罗",
}


# ============ 纯函数（可离线测试） ============

def strip_md(text: str) -> str:
    """markdown → 纯文本：链接留文字、去加粗、去实体、压空白"""
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text or "")
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", t)
    t = t.replace("**", "").replace("&middot;", "·")
    t = html_mod.unescape(t)
    t = re.sub(r"[ \t\u3000]+", " ", t)
    return t.strip()


def resolve_alias(name: str) -> str:
    key = (name or "").strip()
    return ALIASES.get(key) or ALIASES.get(key.lower()) or key


def pick_entry(results: list[dict], name: str, want: str) -> dict | None:
    """从 search --json 结果里挑词条

    want="codex"：游戏图鉴（共鸣者分类，19 位大 ID）
    want="guide"：角色攻略正文（优先【WIKI】官方攻略；占位卡片用 previewEntryId）
    """
    for r in results:
        if r.get("name") != name:
            continue
        cat = str(r.get("category", ""))
        eid = str(r.get("entryId") or "")
        if want == "codex" and "共鸣者" in cat and eid.isdigit() and int(eid) > 10**15:
            return {"name": name, "entryId": eid, "category": cat}
    if want == "guide":
        # 官方攻略正文词条：【WIKI】xxx角色攻略
        for r in results:
            if r.get("name") == f"【WIKI】{name}角色攻略":
                eid = str(r.get("entryId") or "")
                if eid.isdigit():
                    return {"name": r["name"], "entryId": eid, "category": r.get("category", "")}
        # 占位卡片（5 位小 ID）→ previewEntryId 才是正文
        for r in results:
            cat = str(r.get("category", ""))
            if "角色攻略" not in cat or r.get("name") != name:
                continue
            pid = str(r.get("previewEntryId") or "")
            if pid.isdigit():
                return {"name": name, "entryId": pid, "category": cat}
        for r in results:
            cat = str(r.get("category", ""))
            eid = str(r.get("entryId") or "")
            if "角色攻略" in cat and eid.isdigit() and int(eid) > 10**15:
                return {"name": r.get("name", name), "entryId": eid, "category": cat}
    return None


MAT_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)\s*[x×]\s*(\d+)")


def parse_material_totals(md: str) -> dict[str, int]:
    """从「角色突破材料」小节汇总 [材料](url)xN"""
    totals: dict[str, int] = {}
    for raw_name, qty in MAT_LINK.findall(md or ""):
        name = strip_md(raw_name)
        if not name:
            continue
        totals[name] = totals.get(name, 0) + int(qty)
    return totals


def format_wan(n: int) -> str:
    if n >= 10000:
        v = n / 10000
        return f"{v:.1f}".rstrip("0").rstrip(".") + "万"
    return f"{n:,}"


def format_totals(totals: dict[str, int]) -> str:
    items = [f"{k}×{format_wan(v) if v >= 10000 else v}"
             for k, v in totals.items() if k != "贝币"]
    if "贝币" in totals:
        items.append(f"贝币×{format_wan(totals['贝币'])}")
    return "、".join(items)


def extract_weapon_rank(md: str) -> str:
    """从「武器推荐」小节提取主流武器排名行：A > B > C"""
    for line in (md or "").splitlines():
        if re.search(r"\]\([^)]*\)\s*>", line):
            names = [n for n in re.findall(r"\[([^\]]+)\]\([^)]*\)", line) if strip_md(n)]
            names = [strip_md(n) for n in names]
            # 排名行特征：名字后跟 >；去掉重复图标名后至少 2 个
            seen = []
            for n in names:
                if n not in seen:
                    seen.append(n)
            if len(seen) >= 2:
                return " > ".join(seen)
    return ""


def extract_echo(md: str) -> dict:
    """从「声骸套装推荐」小节提取：套装名 / COST分配 / 主词条"""
    out = {"set": "", "cost": "", "main": ""}
    text = md or ""
    m = re.search(r"COST分配[^|]*\|[^|]*?毕业[：:]\s*(\d+)", text)
    if m:
        out["cost"] = m.group(1)
    m = re.search(r"\[?\*{0,2}([^\[\]|*]{2,12})\*{0,2}\]?\([^)]*\)", text)
    if m:
        out["set"] = strip_md(m.group(1))
    if not out["set"]:
        m = re.search(r"####\s+(\S+)", text.split("主流声骸", 1)[-1])
        if m:
            out["set"] = m.group(1)
    m = re.search(r"COST主词条[^|]*\|([^|\n]+)", text)
    if m:
        main = strip_md(m.group(1))
        main = re.sub(r"\s*/\s*", "/", main)
        main = main.replace("COST", "C")
        out["main"] = main[:90]
    return out


def extract_teams(md: str, limit: int = 5) -> list[str]:
    """从「编队&队伍轴推荐」小节提取队伍组成列表"""
    m = re.search(r"队伍组成[^|]*\|([^|\n]+)", md or "")
    if not m:
        return []
    cell = strip_md(m.group(1))
    teams = []
    for part in re.split(r"-{4,}", cell):
        t = re.sub(r"\s+", "", part).strip("*").strip()
        if t and t not in teams:
            teams.append(t)
        if len(teams) >= limit:
            break
    return teams


TEAM_LINE = re.compile(r"^([🥇🥈🥉🏅\d.]+)\s*\*{0,2}(.+?)\*{0,2}\s*\[(.+?)\]\s*(📚\S+)?\s*$")


def parse_team_output(text: str) -> list[dict]:
    """解析 team 命令输出里的奖牌行"""
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("[probe]"):
            continue
        m = TEAM_LINE.match(line)
        if m:
            out.append({
                "medal": m.group(1),
                "team": strip_md(m.group(2)).strip(),
                "source": m.group(3),
                "url": (m.group(4) or "").lstrip("📚"),
            })
    return out


def format_search(results: list[dict], limit: int = 5) -> str:
    lines = []
    for r in results[:limit]:
        name = r.get("name", "?")
        cat = str(r.get("category", "")).replace("全部 / ", "")
        eid = str(r.get("previewEntryId") or r.get("entryId") or "")
        big = eid.isdigit() and int(eid) > 10**15
        line = f"{len(lines) + 1}. {name}（{cat}）"
        if big:
            line += f"\n　{ITEM_URL.format(id=eid)}"
        lines.append(line)
    return "\n".join(lines)


# ============ 插件 ============

class AemeathKurobbsPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        cache = self.config.get("cache_dir") or str(
            Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_aemeath_kurobbs"
        )
        self.home = Path(cache)
        (self.home / "card_cache").mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()   # 串行化子进程，控制 API 频率
        self._last: dict[str, float] = {}
        self._warm_started = False
        logger.info(
            "[aemeath_kurobbs] 库街区插件已加载，脚本: %s，缓存: %s",
            "存在" if SCRIPT.exists() else "缺失!", cache,
        )

    # ---------- 子进程 ----------

    def _kick_warm(self) -> None:
        """首次真实查询时拉起后台预热任务（桩测试 patch 掉 _run 不会触发）"""
        if self._warm_started or not self.config.get("warm_cache", True):
            return
        self._warm_started = True
        try:
            asyncio.get_running_loop().create_task(self._warm_loop())
        except RuntimeError:
            self._warm_started = False

    async def _warm_loop(self) -> None:
        """后台缓存预热：脚本的列表缓存 24h 过期，冷启动全分类遍历要拉上百个列表。

        每 25h（缓存过期后）重新拉一遍常用分类，让用户查询永远打在热缓存上。
        """
        await asyncio.sleep(30)
        while True:
            for args in (
                ["search", "今汐", "--json", "--limit", "3", "--cats", "共鸣者"],
                ["search", "今汐", "--json", "--limit", "3", "--cats", "角色攻略"],
                ["search", "声骸", "--json", "--limit", "3"],
            ):
                try:
                    await self._run(args, timeout=600)
                    logger.info("[aemeath_kurobbs] 缓存预热完成: %s", " ".join(args[:5]))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[aemeath_kurobbs] 缓存预热失败: %s", exc)
            await asyncio.sleep(25 * 3600)

    async def _run(self, args: list[str], timeout: int | None = None) -> str:
        """跑 wikiquery.py 子进程，返回 stdout；超时/失败抛 RuntimeError"""
        self._kick_warm()
        if not SCRIPT.exists():
            raise RuntimeError("vendor 脚本缺失")
        env = {
            **os.environ,
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
        async with self._lock:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-X", "utf8", "-u", str(SCRIPT), *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env, cwd=str(SCRIPT.parent),
            )
            try:
                out, err = await asyncio.wait_for(
                    proc.communicate(), timeout or int(self.config.get("timeout", 150))
                )
            except asyncio.TimeoutError:
                proc.kill()
                try:
                    await proc.wait()
                except Exception:  # noqa: BLE001
                    pass
                raise RuntimeError(f"查询超时（{timeout or self.config.get('timeout', 150)}s）")
        text = out.decode("utf-8", "ignore")[:MAX_OUTPUT]
        if proc.returncode != 0:
            tail = err.decode("utf-8", "ignore")[-200:]
            logger.warning("[aemeath_kurobbs] 子进程失败 rc=%s: %s", proc.returncode, tail)
            raise RuntimeError(tail or f"退出码 {proc.returncode}")
        return text

    async def _search(self, keyword: str, cats: str = "") -> list[dict]:
        """搜索词条；cats 限定分类（如 共鸣者/角色攻略），留空遍历全部常用分类

        冷缓存时全分类遍历要拉上百个列表，限定分类能把首次查询压到秒级。
        """
        args = ["search", keyword, "--json", "--limit", "20"]
        if cats:
            args += ["--cats", cats]
        text = await self._run(args)
        try:
            start = text.index("[")
            return json.loads(text[start:])
        except (ValueError, json.JSONDecodeError):
            logger.warning("[aemeath_kurobbs] search 输出无法解析: %s", text[:120])
            return []

    async def _section(self, entry_id: str, section: str) -> str:
        text = await self._run(["detail", entry_id, "--section", section])
        if text.startswith("未找到小节"):
            return ""
        return text

    # ---------- 结果缓存 ----------

    def _cache_get(self, key: str):
        path = self.home / "card_cache" / f"{re.sub(r'[^0-9A-Za-z\u4e00-\u9fff_-]', '_', key)}.json"
        try:
            if path.exists() and time.time() - path.stat().st_mtime < float(self.config.get("cache_hours", 24)) * 3600:
                return json.loads(path.read_text(encoding="utf-8")).get("text")
        except (OSError, json.JSONDecodeError):
            pass
        return None

    def _cache_put(self, key: str, text: str) -> None:
        path = self.home / "card_cache" / f"{re.sub(r'[^0-9A-Za-z\u4e00-\u9fff_-]', '_', key)}.json"
        try:
            path.write_text(json.dumps({"text": text}, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    # ---------- 各查询 ----------

    async def query_materials(self, raw_name: str) -> str:
        name = resolve_alias(raw_name)
        cached = self._cache_get(f"mat_{name}")
        if cached:
            return cached
        results = await self._search(name, cats="共鸣者")
        entry = pick_entry(results, name, "codex")
        if not entry:
            return f"库街区没找到角色「{name}」的图鉴词条，用 /kuro {name} 搜搜看？"
        md = await self._section(entry["entryId"], "角色突破材料")
        if not md:
            return f"「{name}」的图鉴页还没有突破材料小节，过几天再看看。"
        totals = parse_material_totals(md)
        if not totals:
            return f"「{name}」的突破材料解析不出来，直接看页面吧：\n{ITEM_URL.format(id=entry['entryId'])}"
        card = (
            f"⚒ 库街区 · {name} 突破材料（0→90 合计）\n"
            f"{format_totals(totals)}\n"
            f"🔗 {ITEM_URL.format(id=entry['entryId'])}"
        )
        self._cache_put(f"mat_{name}", card)
        return card

    async def query_guide(self, raw_name: str) -> str:
        name = resolve_alias(raw_name)
        cached = self._cache_get(f"guide_{name}")
        if cached:
            return cached
        results = await self._search(name, cats="角色攻略")
        entry = pick_entry(results, name, "guide")
        if not entry:
            return f"库街区没找到「{name}」的角色攻略，用 /kuro {name} 搜搜看？"
        eid = entry["entryId"]
        lines = [f"📚 库街区攻略 · {name}（仅供当期参考）"]

        weapons = extract_weapon_rank(await self._section(eid, "武器推荐"))
        if weapons:
            lines.append(f"🗡 武器：{weapons}")

        echo = extract_echo(await self._section(eid, "声骸套装推荐"))
        if echo["set"]:
            line = f"🎵 声骸：{echo['set']}"
            if echo["cost"]:
                line += f"（毕业{echo['cost']}）"
            lines.append(line)
            if echo["main"]:
                lines.append(f"　主词条：{echo['main']}")

        teams = extract_teams(await self._section(eid, "编队&队伍轴推荐"))
        if teams:
            lines.append("👥 主流队伍：")
            lines += [f"{i}. {t}" for i, t in enumerate(teams, 1)]

        if len(lines) == 1:
            return f"「{name}」的攻略页还是空的，直接看链接吧：\n{ITEM_URL.format(id=eid)}"
        lines.append(f"🔗 {ITEM_URL.format(id=eid)}")
        card = "\n".join(lines)
        self._cache_put(f"guide_{name}", card)
        return card

    async def query_team(self, raw_name: str, pool: str = "") -> str:
        name = resolve_alias(raw_name)
        cache_key = f"team_{name}_{pool}"
        cached = self._cache_get(cache_key)
        if cached:
            return cached
        args = ["team", name]
        if pool:
            names = [resolve_alias(p.strip()) for p in re.split(r"[,，、\s]+", pool) if p.strip()]
            args += ["--pool", ",".join(dict.fromkeys(names))]
        else:
            args.append("--guide-pool")
        args += ["--top", str(int(self.config.get("top_teams", 3)))]
        text = await self._run(args, timeout=int(self.config.get("team_timeout", 300)))
        teams = parse_team_output(text)
        if not teams:
            return f"配队引擎没算出「{name}」的结果，可能攻略画像还没建好，晚点再试。"
        src_name = "你给的角色池" if pool else "攻略点名补池"
        lines = [f"🧮 {name} 配队（库街区引擎，{src_name}）"]
        for t in teams:
            line = f"{t['medal']} {t['team']}｜{t['source']}"
            if t.get("url"):
                line += f"\n　📚 {t['url']}"
            lines.append(line)
        lines.append("🟢攻略实锤 🟡混合 🔵引擎推断；攻略仅供当期参考")
        card = "\n".join(lines)
        self._cache_put(cache_key, card)
        return card

    async def query_search(self, keyword: str) -> str:
        keyword = (keyword or "").strip()
        if not keyword:
            return "用法：/kuro <关键词>"
        results = await self._search(resolve_alias(keyword))
        if not results:
            return f"库街区没搜到「{keyword}」。"
        return f"🔎 库街区搜索「{keyword}」\n{format_search(results)}"

    # ---------- 指令 ----------

    def _cooling(self, event: AstrMessageEvent) -> bool:
        now = time.time()
        key = event.unified_msg_origin
        if now - self._last.get(key, 0.0) < int(self.config.get("cooldown", 5)):
            return True
        self._last[key] = now
        return False

    @staticmethod
    def _args(event: AstrMessageEvent) -> str:
        parts = (event.message_str or "").strip().split(maxsplit=1)
        return parts[1].strip() if len(parts) > 1 else ""

    async def _safe(self, coro) -> str:
        """执行查询协程，异常转友好文案"""
        try:
            return await coro
        except RuntimeError as exc:
            logger.warning("[aemeath_kurobbs] 查询失败: %s", exc)
            return f"库街区查询翻车了：{exc}".strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[aemeath_kurobbs] 查询异常: %s", exc)
            return "库街区查询出错了，稍后再试。"

    async def _guard(self, event: AstrMessageEvent, coro) -> str:
        if not self.config.get("enabled", True):
            return "库街区查询没开启。"
        if self._cooling(event):
            return ""
        return await self._safe(coro)

    @filter.command("材料")
    async def cmd_materials(self, event: AstrMessageEvent):
        """查角色突破材料合计：/材料 今汐"""
        event._aemeath_voice_sent = True
        name = self._args(event)
        if not name:
            yield event.plain_result("用法：/材料 <角色名>，比如 /材料 今汐")
            return
        text = await self._guard(event, self.query_materials(name))
        if text:
            yield event.plain_result(text)

    @filter.command("攻略")
    async def cmd_guide(self, event: AstrMessageEvent):
        """查角色攻略（武器/声骸/队伍）：/攻略 今汐"""
        event._aemeath_voice_sent = True
        name = self._args(event)
        if not name:
            yield event.plain_result("用法：/攻略 <角色名>，比如 /攻略 今汐")
            return
        text = await self._guard(event, self.query_guide(name))
        if text:
            yield event.plain_result(text)

    @filter.command("配队")
    async def cmd_team(self, event: AstrMessageEvent):
        """配队引擎：/配队 今汐 或 /配队 今汐 折枝,维里奈"""
        event._aemeath_voice_sent = True
        raw = self._args(event)
        if not raw:
            yield event.plain_result("用法：/配队 <角色名> [队友池,逗号分隔]")
            return
        if not self.config.get("enabled", True):
            yield event.plain_result("库街区查询没开启。")
            return
        if self._cooling(event):
            return
        parts = raw.split(maxsplit=1)
        name, pool = parts[0], parts[1] if len(parts) > 1 else ""
        yield event.plain_result("翻攻略算配队中，第一次要建画像，可能要几十秒…")
        text = await self._safe(self.query_team(name, pool))
        if text:
            yield event.plain_result(text)

    @filter.command("kuro")
    async def cmd_search(self, event: AstrMessageEvent):
        """库街区全站搜索：/kuro 声骸"""
        event._aemeath_voice_sent = True
        text = await self._guard(event, self.query_search(self._args(event)))
        if text:
            yield event.plain_result(text)

    # ---------- LLM 工具 ----------

    @filter.llm_tool(name="wuwa_kurobbs_lookup")
    async def kurobbs_tool(self, event: AstrMessageEvent, keyword: str, want: str = "自动"):
        """当用户想查《鸣潮》角色的突破材料、武器推荐、声骸推荐、主流配队，或库街区攻略内容时调用。keyword 为角色名或关键词；want 可选：突破材料/武器推荐/声骸推荐/编队推荐/配队计算/搜索/自动。"""
        if not self.config.get("enabled", True):
            return "库街区查询当前未启用。"
        keyword = (keyword or "").strip()
        if not keyword:
            return "keyword 不能为空。"
        want = (want or "自动").strip()
        try:
            if want in ("配队计算", "配队"):
                return await self.query_team(keyword)
            if want in ("突破材料", "材料"):
                return await self.query_materials(keyword)
            if want in ("武器推荐", "声骸推荐", "编队推荐", "攻略", "自动"):
                return await self.query_guide(keyword)
            return await self.query_search(keyword)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[aemeath_kurobbs] LLM 工具失败: %s", exc)
            return f"库街区查询失败：{exc}"
