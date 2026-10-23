"""
爱弥斯 · 鸣潮 WIKI 查询插件（AstrBot）

数据源：B站游戏 WIKI「鸣潮WIKI」（wiki.biligame.com/wutheringwaves，MediaWiki API + Semantic MediaWiki）

触发：
    /wiki <角色名|武器名|关键词>    指令（wiki xxx 同样生效）
    「鸣潮wiki xxx」「查角色 xxx」  前缀（可在插件配置里改）
    LLM 工具 wuwa_character_info   自然对话里说"帮我查查今汐的资料"也能触发

命中角色 → 立绘 + 基本资料 + 养成材料（突破/技能合计）+ 同类型五星武器 + 链接；
命中武器 → 武器图 + 基本资料（含90级面板）+ 突破材料合计（0→90）+ 链接；
都没命中 → WIKI 全文搜索，发第一条摘要 + 链接。

角色列表来自 Category:共鸣者（缓存 7 天），武器索引/分类型武器表来自 SMW ask
（缓存 7 天），角色/武器资料解析自页面模板（缓存 3 天），图片下载后本地缓存。
注：角色技能升级消耗 wiki 仅个别角色维护（如今汐），缺失时卡片如实标注；
    升级经验药剂 wiki 未收录，不计入材料合计。
"""
import difflib
import html as html_mod
import json
import re
import time
from pathlib import Path

import aiohttp

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except Exception:  # 旧版本兜底
    def get_astrbot_data_path():
        return "data"

API_BASE = "https://wiki.biligame.com/wutheringwaves/api.php"
PAGE_BASE = "https://wiki.biligame.com/wutheringwaves/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": "https://wiki.biligame.com/wutheringwaves/",
}

CHAR_TTL = 7 * 24 * 3600    # 角色列表/武器索引缓存 7 天
INFO_TTL = 3 * 24 * 3600    # 单角色/武器资料缓存 3 天
INFO_VERSION = 2            # 资料缓存结构版本（升级后旧缓存自动失效）
IMAGE_LIMIT = 200           # 图片缓存最多保留张数
MAX_IMAGE_BYTES = 6 * 1024 * 1024  # 超过 6MB 的图跳过
REC_WEAPON_LIMIT = 8        # 角色卡片里同类型五星武器最多列几把

# 技能升级消耗相关字段（wiki 仅个别角色填写，缺则如实标注）
SKILL_COST_FIELDS = (
    "常态攻击升级消耗", "共鸣技能升级消耗", "共鸣回路升级消耗",
    "共鸣解放升级消耗", "变奏技能升级消耗",
    "固有技能1激活消耗", "固有技能2激活消耗",
    "属性加成1激活消耗", "属性加成2激活消耗",
    "属性加成3激活消耗", "属性加成4激活消耗",
)
WEAPON_COST_FIELDS = tuple(f"{lv}级突破材料" for lv in (20, 40, 50, 60, 70, 80))
# 武器 90 级面板系数（来自 wiki 模板:武器 的 #expr 公式）
WEAPON_LV90_ATK_MULT = 12.5
WEAPON_LV90_SUB_FLAT = 4.5   # 数值词条 90 级: trunc(初始*4.5)
WEAPON_LV90_SUB_PCT = 4500   # 百分比词条 90 级: trunc(初始*4500)/10

# 战斗风格 ID → 名称（来自 wiki 模板:共鸣者/基本资料 的 #switch 表）
STYLE_MAP = {
    "1": "生存治疗", "2": "主力输出", "3": "快速协奏", "4": "普攻伤害",
    "5": "重击伤害", "6": "共鸣技能伤害", "7": "共鸣解放伤害", "8": "牵引",
    "9": "协同攻击", "10": "凝滞", "11": "共鸣解放充能", "12": "共振摧毁",
    "13": "伤害加深", "14": "湮灭伤害加深", "15": "气动伤害加深",
    "16": "导电伤害加深", "17": "热熔伤害加深", "18": "冷凝伤害加深",
    "19": "衍射伤害加深", "20": "普攻伤害加深", "21": "重击伤害加深",
    "22": "共鸣技能伤害加深", "23": "共鸣解放伤害加深", "24": "风蚀",
    "25": "电磁", "26": "霜渐", "27": "聚爆", "28": "光噪", "29": "虚湮",
    "32": "声骸技能伤害加深", "33": "震谐响应", "34": "谐度破坏增幅",
    "35": "偏谐值累积效率", "36": "集谐响应", "37": "声骸技能伤害",
    "38": "骇破响应",
}

# 常见别名 / 错别字 / 社区外号（与 wuwa_emoji 保持一致，另按 WIKI 角色表补充）
ALIASES = {
    "小爱": "爱弥斯",
    "小爱同学": "爱弥斯",
    "达尼亚": "达妮娅",
    "达尼娅": "达妮娅",
    "达妮雅": "达妮娅",
    "娅娅": "达妮娅",
    "水蜜桃": "达妮娅",
    "耙耙柑": "西格莉卡",
    "西西": "西格莉卡",
    "糯糯": "弗洛洛",
    "嘻嘻": "今汐",
    "xixi": "今汐",
    "夕夕": "今汐",
    "汐汐": "今汐",
    "青霄": "清宵",
    "漂子": "漂泊者",
    "大爷": "卡卡罗",
    "长离姐姐": "长离",
}


# ============ 纯函数（可离线测试） ============

def strip_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def clean_value(value: str) -> str:
    """清理模板字段值：HTML 注释/换行标签/模板调用/链接/标签"""
    v = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    # {{引用|x}}、{{颜色|技能|x}} 等多级模板 → 保留最后一段内容
    v = re.sub(r"\{\{[^{}|]*(?:\|[^{}|]*)*?\|([^{}|]*?)\}\}", r"\1", v)
    v = re.sub(r"\{\{[^{}]*\}\}", "", v)                    # 其余无参模板
    v = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", v)  # [[a|b]] → b
    v = re.sub(r"<[^>]+>", "", v)                           # HTML 标签
    v = html_mod.unescape(v)
    v = re.sub(r"[ \t\u3000]+", " ", v)
    return v.strip()


def parse_template_fields(wikitext: str, template: str) -> dict:
    """解析页面里第一个 {{template ...}} 块，返回 {字段名: 清理后的值}

    逐行扫描：以 |xxx= 开头的行是新字段，其余行并入上一个字段（多行值）。
    """
    m = re.search(r"\{\{" + re.escape(template) + r"(.*?)\n\}\}", wikitext, re.S)
    if not m:
        return {}
    body = strip_comments(m.group(1))
    fields: dict[str, list[str]] = {}
    cur: str | None = None
    for line in body.splitlines():
        fm = re.match(r"\|([^=|]+?)=(.*)$", line)
        if fm:
            cur = fm.group(1).strip()
            fields.setdefault(cur, []).append(fm.group(2))
        elif cur is not None:
            fields[cur].append(line)
    return {k: clean_value("\n".join(v)) for k, v in fields.items()}


def format_date(raw: str) -> str:
    """20260205 → 2026-02-05；其它格式原样返回"""
    raw = (raw or "").strip()
    if re.fullmatch(r"\d{8}", raw):
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return raw


def format_styles(raw: str) -> str:
    """战斗风格：数字 ID 转名称（部分页面直接写名称），去重保序"""
    out: list[str] = []
    for part in re.split(r"[,，、]", raw or ""):
        part = part.strip()
        if not part:
            continue
        name = STYLE_MAP.get(part, part)
        if name not in out:
            out.append(name)
    return "、".join(out)


def clean_snippet(raw: str, limit: int = 150) -> str:
    """搜索接口返回的 snippet 去 HTML 标记/页面导航垃圾并截断"""
    text = html_mod.unescape(re.sub(r"<[^>]+>", "", raw or ""))
    text = re.sub(r"\s+", " ", text).strip()
    # 命中页首时 snippet 会带「首页 > 分类 > 标题 编 刷 历」这类导航残渣
    text = re.sub(r"^首页\s*>.*?(?:编\s*刷\s*历)\s*", "", text)
    text = re.sub(r"编\s*刷\s*历|暂无icon", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def page_url(title: str) -> str:
    """页面链接：空格换下划线，保留中文（QQ 点击时会自动转义）"""
    return PAGE_BASE + title.replace(" ", "_")


def parse_qty(raw: str) -> int | None:
    """解析数量：支持 60 / 2,252 / 1k5(=1500) / 3w(=30000) / 1w6(=16000) 记法"""
    s = (raw or "").strip().lower().replace(",", "").replace("，", "")
    if not s:
        return None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([kw万]?)(\d)?", s)
    if not m:
        return None
    base, unit, tail = m.group(1), m.group(2), m.group(3)
    mult = {"k": 1000, "w": 10000, "万": 10000}.get(unit, 1)
    tail_mult = {"k": 100, "w": 1000, "万": 1000}.get(unit, 0)
    total = int(float(base) * mult) + (int(tail) * tail_mult if tail else 0)
    return total or None


def parse_material_entries(raw: str) -> list[tuple[str, int]]:
    """解析「材料*数量,材料*数量;…」格式，返回 [(名称, 数量)]"""
    out: list[tuple[str, int]] = []
    for part in re.split(r"[,;，；\n]+", strip_comments(raw or "")):
        part = part.strip()
        if not part or "*" not in part:
            continue
        name, _, qty = part.rpartition("*")
        name = clean_value(name).strip()
        n = parse_qty(qty)
        if name and n:
            out.append((name, n))
    return out


def sum_material_entries(*raws: str) -> dict[str, int]:
    """把多段材料串合并求和，保持首次出现顺序"""
    totals: dict[str, int] = {}
    for raw in raws:
        for name, qty in parse_material_entries(raw):
            totals[name] = totals.get(name, 0) + qty
    return totals


def format_wan(n: int) -> str:
    """大数字用「万」：330000→33万，1730000→173万，16000→1.6万"""
    if n >= 10000:
        v = n / 10000
        return f"{v:.1f}".rstrip("0").rstrip(".") + "万"
    return f"{n:,}"


def format_material_totals(totals: dict[str, int]) -> str:
    """材料合计 → 「名称×数量、…」，贝币单独放最后"""
    if not totals:
        return ""
    items = [(k, v) for k, v in totals.items() if k != "贝币"]
    parts = [f"{k}×{format_wan(v) if v >= 10000 else v}" for k, v in items]
    if "贝币" in totals:
        parts.append(f"贝币×{format_wan(totals['贝币'])}")
    return "、".join(parts)


def build_material_section(info: dict) -> list[str]:
    """角色卡片的养成材料段（wiki 有什么显示什么，缺失如实标注）"""
    lines = ["⚒ 养成材料（0→90 合计）"]
    asc = format_material_totals(info.get("ascension_totals") or {})
    if asc:
        lines.append(f"角色突破：{asc}")
    else:
        lines.append("角色突破：WIKI 暂未收录该角色突破材料")
    skill = format_material_totals(info.get("skill_totals") or {})
    if skill:
        lines.append(f"技能满级：{skill}")
    else:
        lines.append("技能升级：WIKI 暂未收录该角色技能材料")
    lines.append("（升级经验药剂 WIKI 未收录，未计入）")
    return lines


def build_weapon_rec_section(info: dict, weapons: list[dict]) -> list[str]:
    """角色卡片的同类型五星武器段（按实装版本降序）"""
    wtype = info.get("weapon") or ""
    if not wtype or not weapons:
        return []

    def ver_key(w):
        try:
            return float(str(w.get("version", "")).strip() or 0)
        except ValueError:
            return 0.0

    ordered = sorted(weapons, key=ver_key, reverse=True)
    shown = ordered[:REC_WEAPON_LIMIT]
    parts = [
        f"{w.get('name', '')}({w.get('substat') or '?'})" if w.get("substat")
        else w.get("name", "")
        for w in shown if w.get("name")
    ]
    if not parts:
        return []
    line = f"🗡 五星{wtype}：{'、'.join(parts)}"
    if len(ordered) > len(shown):
        line += f"……共{len(ordered)}把"
    return [line]


def build_weapon_card(info: dict) -> str:
    """武器资料卡片"""
    lines = [f"🗡 鸣潮WIKI · {info.get('name', '')}（武器）"]
    head = []
    quality = str(info.get("quality", "")).strip()
    if quality.isdigit() and 1 <= int(quality) <= 6:
        head.append("★" * int(quality))
    if info.get("type"):
        head.append(info["type"])
    if info.get("version"):
        head.append(f"v{info['version']}实装")
    if head:
        lines.append("｜".join(head))
    if info.get("atk90") or info.get("substat90"):
        stat = []
        if info.get("atk90"):
            stat.append(f"90级攻击 {info['atk90']}")
        if info.get("substat90"):
            stat.append(info["substat90"])
        lines.append("｜".join(stat))
    if info.get("obtain"):
        lines.append(f"获取途径：{info['obtain']}")
    if info.get("skill_name"):
        skill = info["skill_name"]
        desc = info.get("skill_desc", "")
        if len(desc) > 130:
            desc = desc[:130] + "…"
        lines.append(f"武器技能「{skill}」：{desc}" if desc else f"武器技能「{skill}」")
    mats = format_material_totals(info.get("ascension_totals") or {})
    if mats:
        lines.append(f"⚒ 突破材料（0→90）：{mats}")
    else:
        lines.append("⚒ 突破材料：WIKI 暂未收录")
    if info.get("url"):
        lines.append(f"🔗 {info['url']}")
    return "\n".join(lines)


def build_card(info: dict, extra: list[str] | None = None) -> str:
    """角色资料卡片文本；extra 为插入在链接前的附加段（养成材料/武器推荐）"""
    lines = [f"📖 鸣潮WIKI · {info.get('name', '')}"]

    head = []
    quality = str(info.get("quality", "")).strip()
    if quality.isdigit() and 1 <= int(quality) <= 6:
        head.append("★" * int(quality))
    if info.get("element"):
        head.append(info["element"])
    if info.get("weapon"):
        head.append(info["weapon"])
    if head:
        lines.append("｜".join(head))

    if info.get("first_voice"):
        lines.append(f"初奏：「{info['first_voice']}」")

    region_bits = []
    if info.get("birth"):
        region_bits.append(f"出生：{info['birth']}")
    if info.get("faction"):
        region_bits.append(f"势力：{info['faction']}")
    if info.get("birthday"):
        region_bits.append(f"生日：{info['birthday']}")
    if region_bits:
        lines.append("｜".join(region_bits))

    if info.get("ability"):
        lines.append(f"共鸣能力：{info['ability']}")
    if info.get("styles"):
        lines.append(f"战斗风格：{info['styles']}")
    if info.get("dish"):
        lines.append(f"特殊料理：{info['dish']}")
    if info.get("impl_date"):
        lines.append(f"实装日期：{info['impl_date']}")
    if info.get("intro"):
        lines.append(f"简介：{info['intro']}")
    if extra:
        lines.extend(x for x in extra if x)
    if info.get("url"):
        lines.append(f"🔗 {info['url']}")
    return "\n".join(lines)


def _pinyin(text: str) -> str:
    """无声调拼音，用于谐音/错别字匹配；未安装 pypinyin 返回空"""
    try:
        from pypinyin import lazy_pinyin
        return "".join(lazy_pinyin(text)).lower()
    except Exception:  # noqa: BLE001
        return ""


def match_name(keyword: str, names: list[str]) -> tuple[str | None, list[str]]:
    """在角色列表里匹配关键词，返回 (唯一命中, 候选列表)

    顺序：别名 → 精确 → 包含 → 拼音 → 形近字容错。
    多个包含命中（如「漂泊者」对应四种属性）时返回候选让用户挑。
    """
    key = (keyword or "").strip()
    if not key or not names:
        return None, []
    low = key.lower()

    # 1. 别名（先相等后包含，包含时取最长别名）
    alias = ALIASES.get(key) or ALIASES.get(low)
    if alias and alias in names:
        return alias, []
    best: tuple[str, str] | None = None
    for src, target in ALIASES.items():
        if src.lower() in low and (best is None or len(src) > len(best[0])):
            best = (src, target)
    if best and best[1] in names:
        return best[1], []

    # 2. 精确
    for n in names:
        if n.lower() == low:
            return n, []

    # 3. 包含（「今汐的资料」也能命中；单字名只走精确，避免误伤）
    hits = [
        n for n in names
        if (len(n) >= 2 and n.lower() in low) or (len(low) >= 2 and low in n.lower())
    ]
    if len(hits) == 1:
        return hits[0], []
    if len(hits) > 1:
        return None, hits

    # 4. 拼音（谐音/错别字，如「达尼亚」→「达妮娅」）
    if len(key) >= 2:
        key_py = _pinyin(key)
        if key_py:
            py_hits = [n for n in names if _pinyin(n) == key_py]
            if len(py_hits) == 1:
                return py_hits[0], []
            if py_hits:
                return None, py_hits

    # 5. 形近字容错（3 字及以上，避免误匹配）
    if len(key) >= 3:
        best_name, best_score = None, 0.0
        for n in names:
            if len(n) < 2:
                continue
            score = difflib.SequenceMatcher(None, key, n).ratio()
            if score > best_score:
                best_name, best_score = n, score
        if best_score >= 0.6:
            return best_name, []
    return None, []


# ============ WIKI 客户端 ============

WEAPON_TYPES = ("长刃", "迅刀", "佩枪", "臂铠", "音感仪")


def match_weapon(keyword: str, index: dict) -> str | None:
    """在武器索引里匹配：精确 → 包含（唯一命中才算）→ 形近字容错

    裸的武器类型词（如「迅刀」）不参与包含匹配，交给全文搜索。
    """
    key = (keyword or "").strip()
    if not key or not index or key in WEAPON_TYPES:
        return None
    low = key.lower()
    for n in index:
        if n.lower() == low:
            return n
    hits = [
        n for n in index
        if (len(n) >= 2 and n.lower() in low) or (len(low) >= 2 and low in n.lower())
    ]
    if len(hits) == 1:
        return hits[0]
    if not hits and len(key) >= 2:
        key_py = _pinyin(key)
        if key_py:
            py_hits = [n for n in index if _pinyin(n) == key_py]
            if len(py_hits) == 1:
                return py_hits[0]
    if not hits and len(key) >= 3:
        best_name, best_score = None, 0.0
        for n in index:
            if len(n) < 2:
                continue
            score = difflib.SequenceMatcher(None, key, n).ratio()
            if score > best_score:
                best_name, best_score = n, score
        if best_score >= 0.6:
            return best_name
    return None


class WuwaWikiClient:
    """B站鸣潮 WIKI 的 MediaWiki API 客户端（带本地缓存）"""

    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.image_dir = self.cache_dir / "images"
        self.image_dir.mkdir(parents=True, exist_ok=True)

    async def _get(self, params: dict) -> dict | None:
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20), headers=HEADERS
            ) as session:
                async with session.get(API_BASE, params=params) as resp:
                    if resp.status != 200:
                        logger.warning("[aemeath_wiki] API HTTP %s", resp.status)
                        return None
                    return await resp.json(content_type=None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[aemeath_wiki] API 请求失败: %s", exc)
            return None

    def _read_cache(self, path: Path, ttl: float):
        if not path.exists():
            return None
        try:
            if time.time() - path.stat().st_mtime > ttl:
                return None
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    @staticmethod
    def _write_cache(path: Path, data) -> None:
        try:
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            logger.warning("[aemeath_wiki] 写缓存失败: %s", exc)

    # ---------- 角色列表 ----------

    async def characters(self, force: bool = False) -> list[str]:
        """Category:共鸣者 里所有「共鸣者/xxx」页的角色名（缓存 7 天）"""
        path = self.cache_dir / "characters.json"
        if not force:
            cached = self._read_cache(path, CHAR_TTL)
            if cached:
                return cached
        names: list[str] = []
        cont: dict = {}
        for _ in range(5):
            data = await self._get({
                "action": "query", "list": "categorymembers",
                "cmtitle": "Category:共鸣者", "cmlimit": "200",
                "format": "json", "formatversion": "2", **cont,
            })
            if not data:
                break
            for m in data.get("query", {}).get("categorymembers", []):
                title = m.get("title", "")
                if title.startswith("共鸣者/") and "/" in title[4:]:
                    continue  # 跳过 共鸣者/xxx/语音 等子页
                if title.startswith("共鸣者/"):
                    name = title[len("共鸣者/"):].strip()
                    if name and name not in names:
                        names.append(name)
            cont = data.get("continue", {})
            if not cont:
                break
        if names:
            self._write_cache(path, names)
            return names
        # 网络失败时回退旧缓存
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []

    # ---------- 页面与图片 ----------

    async def smw_ask(self, query: str) -> dict:
        """Semantic MediaWiki ask 查询，返回 {页面标题: {属性: 值}}"""
        data = await self._get({"action": "ask", "query": query,
                                "format": "json"})
        if not data:
            return {}
        results = {}
        for title, item in (data.get("query", {}).get("results") or {}).items():
            row = {}
            for prop, values in (item.get("printouts") or {}).items():
                if values:
                    row[prop] = values[0]
            results[title] = row
        return results

    async def weapon_index(self) -> dict[str, dict]:
        """全部武器 {名称: {type, quality}}（缓存 7 天），用于查询路由"""
        path = self.cache_dir / "weapon_index.json"
        cached = self._read_cache(path, CHAR_TTL)
        if cached:
            return cached
        rows = await self.smw_ask(
            "[[分类:武器]] |?名称 |?类型 |?品质 |limit=500"
        )
        index = {}
        for title, row in rows.items():
            if not title.startswith("武器/"):
                continue  # 分类里混着 模板:武器
            name = str(row.get("名称") or title[3:]).strip()
            if name:
                index[name] = {
                    "type": str(row.get("类型") or "").strip(),
                    "quality": str(row.get("品质") or "").strip(),
                }
        if index:
            self._write_cache(path, index)
            return index
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    async def weapons_by_type(self, wtype: str, quality: int = 5) -> list[dict]:
        """某武器类型 + 品质的武器表 [{name, substat, atk90, version}]（缓存 7 天）"""
        if not wtype:
            return []
        safe = re.sub(r"[\\/:*?\"<>|]", "_", wtype)
        path = self.cache_dir / f"weapons_{safe}_{quality}.json"
        cached = self._read_cache(path, CHAR_TTL)
        if cached:
            return cached
        rows = await self.smw_ask(
            f"[[分类:武器]] [[类型::{wtype}]] [[品质::{quality}]]"
            " |?名称 |?副词条 |?90级攻击 |?实装版本 |limit=200"
        )
        weapons = []
        for title, row in rows.items():
            if not title.startswith("武器/"):
                continue
            weapons.append({
                "name": str(row.get("名称") or title[3:]).strip(),
                "substat": str(row.get("副词条") or "").strip(),
                "atk90": str(row.get("90级攻击") or "").strip(),
                "version": str(row.get("实装版本") or "").strip(),
            })
        if weapons:
            self._write_cache(path, weapons)
        return weapons

    async def wikitext(self, title: str) -> str:
        data = await self._get({
            "action": "parse", "page": title, "prop": "wikitext",
            "format": "json", "formatversion": "2",
        })
        return (data or {}).get("parse", {}).get("wikitext", "")

    async def image_url(self, filenames: list[str]) -> tuple[str, str] | None:
        """按候选顺序查文件直链，返回 (url, 文件名)；全缺失返回 None"""
        if not filenames:
            return None
        data = await self._get({
            "action": "query",
            "titles": "|".join(f"File:{n}" for n in filenames),
            "prop": "imageinfo", "iiprop": "url|size",
            "format": "json", "formatversion": "2",
        })
        if not data:
            return None
        by_title = {}
        for p in data.get("query", {}).get("pages", []):
            infos = p.get("imageinfo") or []
            title = p.get("title", "")
            # 返回标题是本地化前缀（文件:）且下划线被规范化成空格，统一后比对
            if not p.get("missing") and infos and ":" in title:
                by_title[title.split(":", 1)[1].replace(" ", "_")] = infos[0]
        for fn in filenames:
            info = by_title.get(fn.replace(" ", "_"))
            if info and info.get("url") and info.get("size", 0) <= MAX_IMAGE_BYTES:
                return info["url"], fn
        return None

    async def download(self, url: str, path: Path) -> bool:
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60), headers=HEADERS
            ) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        logger.warning("[aemeath_wiki] 下载图片 HTTP %s", resp.status)
                        return False
                    payload = await resp.read()
            if len(payload) > MAX_IMAGE_BYTES:
                logger.warning("[aemeath_wiki] 图片过大（%d 字节），跳过", len(payload))
                return False
            path.write_bytes(payload)
            self._prune_images()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("[aemeath_wiki] 下载图片失败: %s", exc)
            return False

    def _prune_images(self) -> None:
        try:
            files = sorted(self.image_dir.glob("*"), key=lambda p: p.stat().st_mtime)
            for old in files[:-IMAGE_LIMIT]:
                old.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("[aemeath_wiki] 清理图片缓存失败: %s", exc)

    # ---------- 搜索 ----------

    async def search(self, keyword: str, limit: int = 3) -> list[dict]:
        """全文搜索，返回 [{title, snippet}]"""
        data = await self._get({
            "action": "query", "list": "search",
            "srsearch": keyword, "srlimit": limit,
            "format": "json", "formatversion": "2",
        })
        if not data:
            return []
        return [
            {"title": r.get("title", ""), "snippet": clean_snippet(r.get("snippet", ""))}
            for r in data.get("query", {}).get("search", [])
            if r.get("title")
        ]


# ============ 插件 ============

class AemeathWikiPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        cache = self.config.get("cache_dir") or str(
            Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_aemeath_wiki"
        )
        self.client = WuwaWikiClient(Path(cache))
        self._last: dict[str, float] = {}
        logger.info("[aemeath_wiki] 鸣潮 WIKI 插件已加载，缓存目录: %s", cache)

    # ---------- 资料组装 ----------

    @staticmethod
    def _info_path(name: str) -> str:
        safe = re.sub(r"[\\/:*?\"<>|]", "_", name)
        return f"info_{safe}.json"

    async def character_info(self, name: str) -> dict | None:
        """取角色基本资料 + 养成材料合计（缓存 3 天）；页面缺失或解析失败返回 None"""
        path = self.client.cache_dir / self._info_path(name)
        cached = self.client._read_cache(path, INFO_TTL)
        if cached and cached.get("v") == INFO_VERSION:
            return cached

        wt = await self.client.wikitext(f"共鸣者/{name}")
        if not wt:
            return None
        fields = parse_template_fields(wt, "共鸣者/基本资料")
        if not fields.get("名称") and not fields.get("属性"):
            logger.warning("[aemeath_wiki] 解析 %s 的基本资料失败", name)
            return None
        base_fields = parse_template_fields(wt, "共鸣者/基础属性")
        skill_fields = parse_template_fields(wt, "共鸣者/技能")

        # 突破材料（wiki 的「突破消耗材料总数」字段，新角色常缺）
        ascension = sum_material_entries(base_fields.get("突破消耗材料总数", ""))
        # 技能材料（五条主技能轨 + 固有/加成；wiki 仅个别角色维护）
        main_tracks = [skill_fields.get(f, "") for f in SKILL_COST_FIELDS[:5]]
        skill: dict[str, int] = {}
        if any(t.strip() for t in main_tracks):
            skill = sum_material_entries(
                *(skill_fields.get(f, "") for f in SKILL_COST_FIELDS)
            )

        info = {
            "v": INFO_VERSION,
            "kind": "character",
            "name": fields.get("名称") or name,
            "en": fields.get("英文名", ""),
            "element": fields.get("属性", ""),
            "weapon": fields.get("武器", ""),
            "quality": fields.get("品质", ""),
            "styles": format_styles(fields.get("战斗风格", "")),
            "birthday": fields.get("生日", ""),
            "birth": fields.get("出生", ""),
            "faction": fields.get("势力", ""),
            "ability": fields.get("共鸣能力", ""),
            "dish": fields.get("特殊料理", ""),
            "first_voice": fields.get("初奏", "").replace("\n", " "),
            "intro": fields.get("简介", ""),
            "impl_date": format_date(fields.get("实装日期", "")),
            "ascension_totals": ascension,
            "skill_totals": skill,
            "url": page_url(f"共鸣者/{name}"),
        }
        self.client._write_cache(path, info)
        return info

    async def weapon_info(self, name: str) -> dict | None:
        """取武器资料 + 突破材料合计（缓存 3 天）；页面缺失或解析失败返回 None"""
        path = self.client.cache_dir / self._info_path(f"w_{name}")
        cached = self.client._read_cache(path, INFO_TTL)
        if cached and cached.get("v") == INFO_VERSION:
            return cached

        wt = await self.client.wikitext(f"武器/{name}")
        if not wt:
            return None
        f = parse_template_fields(wt, "武器")
        if not f.get("名称") and not f.get("类型"):
            logger.warning("[aemeath_wiki] 解析武器 %s 的资料失败", name)
            return None

        info = {
            "v": INFO_VERSION,
            "kind": "weapon",
            "name": f.get("名称") or name,
            "type": f.get("类型", ""),
            "quality": f.get("品质", ""),
            "version": f.get("实装版本", ""),
            "obtain": f.get("获取途径", ""),
            "skill_name": f.get("武器技能", ""),
            "skill_desc": f.get("技能描述", "").replace("\n", " "),
            "url": page_url(f"武器/{name}"),
        }

        # 90 级面板（公式来自 wiki 模板:武器）
        try:
            atk0 = float(f.get("初始攻击", "") or 0)
        except ValueError:
            atk0 = 0.0
        if atk0:
            info["atk90"] = str(int(round(atk0 * WEAPON_LV90_ATK_MULT, 6)))
        sub_name = f.get("副词条", "").strip()
        try:
            sub0 = float(f.get("词条初始数值", "") or 0)
        except ValueError:
            sub0 = 0.0
        if sub_name and sub0:
            if (f.get("%") or "").strip():
                a = int(round(sub0 * 1000, 6)) / 10
                b = int(round(sub0 * WEAPON_LV90_SUB_PCT, 6)) / 10
                info["substat90"] = f"{sub_name} {a:g}%→{b:g}%"
            else:
                b = int(round(sub0 * WEAPON_LV90_SUB_FLAT, 6))
                info["substat90"] = f"{sub_name} {sub0:g}→{b:g}"

        # 突破材料：优先手动总览，否则汇总六档
        overview = f.get("突破材料总览", "")
        if overview.strip():
            info["ascension_totals"] = sum_material_entries(overview)
        else:
            info["ascension_totals"] = sum_material_entries(
                *(f.get(k, "") for k in WEAPON_COST_FIELDS)
            )
        self.client._write_cache(path, info)
        return info

    async def _fetch_image(self, cands: list[str]) -> Path | None:
        """按候选文件名顺序：本地缓存 → 查直链 → 下载；失败返回 None"""
        for fn in cands:
            p = self.client.image_dir / fn.replace("/", "_")
            if p.exists() and p.stat().st_size > 0:
                return p
        got = await self.client.image_url(cands)
        if not got:
            return None
        url, fn = got
        path = self.client.image_dir / fn.replace("/", "_")
        ok = await self.client.download(url, path)
        return path if ok else None

    async def character_image(self, info: dict) -> Path | None:
        """按 image_mode 配置取立绘/头像；失败返回 None"""
        mode = (self.config.get("image_mode") or "立绘").strip()
        if mode == "无":
            return None
        name = info.get("name", "")
        en = (info.get("en") or "").strip()
        if not name:
            return None

        if mode == "头像":
            cands = [f"角色_{name}_头像.png"]
            if en:
                cands.append(f"Mc wiki role portrait {en} x500.png")
        else:
            cands = [f"角色_{name}_立绘.png"]
            if en:
                cands.append(f"Mc wiki role {en} x1500.png")
            cands.append(f"角色_{name}_头像.png")  # 立绘缺失时兜底头像
        return await self._fetch_image(cands)

    async def weapon_image(self, info: dict) -> Path | None:
        """武器图（image_mode=无 时不发）；命名规则来自 wiki 模板:武器"""
        if (self.config.get("image_mode") or "立绘").strip() == "无":
            return None
        name = info.get("name", "")
        if not name:
            return None
        return await self._fetch_image([
            f"Mc wiki weapon {name}.png",
            f"武器_{name}.png",
        ])

    async def lookup(self, query: str) -> tuple[str, Path | None]:
        """查询入口：返回 (回复文本, 图片路径|None)；角色 → 武器 → 全文搜索"""
        query = (query or "").strip()
        if not query:
            return "用法：/wiki <角色名|武器名>，比如 /wiki 爱弥斯、/wiki 裁春", None

        names = await self.client.characters()
        if names:
            name, cands = match_name(query, names)
            if cands:
                shown = "、".join(cands[:6])
                return f"「{query}」有好几个相近的条目：{shown}……想看哪一个呀？", None
            if name:
                info = await self.character_info(name)
                if info:
                    img = await self.character_image(info)
                    extra = build_material_section(info)
                    try:
                        rec = await self.client.weapons_by_type(info.get("weapon", ""))
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("[aemeath_wiki] 武器推荐查询失败: %s", exc)
                        rec = []
                    extra += build_weapon_rec_section(info, rec)
                    return build_card(info, extra), img
                return (
                    f"📖 鸣潮WIKI · {name}\n资料解析失败了，直接看页面吧：\n"
                    f"🔗 {page_url(f'共鸣者/{name}')}"
                ), None

        # 武器路由
        index = await self.client.weapon_index()
        wname = match_weapon(query, index) if index else None
        if wname:
            info = await self.weapon_info(wname)
            if info:
                img = await self.weapon_image(info)
                return build_weapon_card(info), img

        # 都没命中 → 全文搜索兜底（声骸/剧情/版本页都能查）
        results = await self.client.search(query, limit=1)
        if results:
            r = results[0]
            return f"📖 鸣潮WIKI · {r['title']}\n{r['snippet']}\n🔗 {page_url(r['title'])}", None
        return f"唔…WIKI 里没找到「{query}」，换个名字试试？", None

    # ---------- 触发 ----------

    def _match_prefix(self, text: str) -> str | None:
        prefixes = sorted(self.config.get("prefixes", []), key=len, reverse=True)
        for prefix in prefixes:
            if prefix and text.startswith(prefix):
                rest = text[len(prefix):].lstrip(" 　:：,，!！~～")
                if rest:
                    return rest.strip()
        return None

    @staticmethod
    def _args(event: AstrMessageEvent) -> str:
        parts = (event.message_str or "").strip().split()
        return " ".join(parts[1:]).strip() if parts else ""

    async def _reply(self, event: AstrMessageEvent, query: str):
        text, img = await self.lookup(query)
        chain = []
        if img:
            chain.append(Comp.Image.fromFileSystem(str(img)))
        chain.append(Comp.Plain(text))
        yield event.chain_result(chain)

    @filter.command("wiki")
    async def wiki_cmd(self, event: AstrMessageEvent):
        """查鸣潮 WIKI：/wiki <角色名|关键词>"""
        if not self.config.get("enabled", True):
            return
        query = self._args(event)
        event._aemeath_voice_sent = True  # 资料卡片不转语音
        async for item in self._reply(event, query):
            yield item

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def wiki_prefix(self, event: AstrMessageEvent):
        if not self.config.get("enabled", True):
            return
        text = (event.message_str or "").strip()
        keyword = self._match_prefix(text)
        if not keyword:
            return

        event.stop_event()
        event._aemeath_voice_sent = True
        now = time.time()
        key = event.unified_msg_origin
        if now - self._last.get(key, 0.0) < int(self.config.get("cooldown", 5)):
            return
        self._last[key] = now

        async for item in self._reply(event, keyword):
            yield item

    # ---------- LLM 工具 ----------

    @filter.llm_tool(name="wuwa_character_info")
    async def wiki_tool(self, event: AstrMessageEvent, keyword: str):
        """当用户想查询《鸣潮》角色资料（属性/武器/势力/简介/养成材料/武器推荐）或武器资料（面板/突破材料）及其他游戏条目时调用：查 B站鸣潮WIKI 返回资料卡片文本。keyword 为角色名、武器名或搜索关键词。"""
        if not self.config.get("enabled", True):
            return "鸣潮 WIKI 查询当前未启用。"
        text, _ = await self.lookup(keyword)
        return text
