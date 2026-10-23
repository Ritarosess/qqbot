"""
本机验收：不依赖 AstrBot/Docker，用桩模块加载插件并跑核心逻辑。

覆盖 README 验收清单里可离线验证的项：
  1. access: /allow /deny /status、非白名单群拦截
  2. voice: 前缀匹配、显式触发（stop_event + 发送语音）、概率语音替换
  3. emoji: [emoji] 标记替换为图片、残留标记清理
  4. summary: 群消息落盘、/summary 触发生成并发送
  5. wiki: 模板解析、角色名匹配、卡片排版、搜索兜底、LLM 工具、缓存

真实语音合成（网络）见 astrbot/tests/test_tts_live.py。
"""
import asyncio
import importlib.util
import json
import shutil
import sys
import time
import types
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TMP = ROOT / "_tmp"
if TMP.exists():
    shutil.rmtree(TMP)
TMP.mkdir(parents=True)

# ============ 桩：astrbot.api ============

class AstrBotConfig(dict):
    pass


class _Logger:
    def info(self, *a, **k): print("[info]", *a)
    def warning(self, *a, **k): print("[warn]", *a)
    def error(self, *a, **k): print("[error]", *a)
    def debug(self, *a, **k): pass


api = types.ModuleType("astrbot.api")
api.AstrBotConfig = AstrBotConfig
api.logger = _Logger()

# ---- message components ----

class Plain:
    def __init__(self, text):
        self.text = text

    def __repr__(self):
        return f"Plain({self.text!r})"


class Image:
    def __init__(self, path):
        self.path = path

    @classmethod
    def fromFileSystem(cls, path):
        return cls(path)

    def __repr__(self):
        return f"Image({self.path})"


class Record:
    def __init__(self, file=None, url=None):
        self.file = file
        self.url = url

    def __repr__(self):
        return f"Record({self.file})"


class Reply:
    def __init__(self, id=None, **kwargs):
        self.id = id

    def __repr__(self):
        return f"Reply({self.id})"


class Poke:
    def __init__(self, id=None, **kwargs):
        self.id = id

    def __repr__(self):
        return f"Poke({self.id})"


comps = types.ModuleType("astrbot.api.message_components")
comps.Plain, comps.Image, comps.Record, comps.Reply, comps.Poke = Plain, Image, Record, Reply, Poke

# ---- event ----

class PermissionType:
    ADMIN = "admin"


class EventMessageType:
    ALL = "all"
    GROUP_MESSAGE = "group"
    PRIVATE_MESSAGE = "private"


class _Filter:
    PermissionType = PermissionType
    EventMessageType = EventMessageType

    @staticmethod
    def command(*a, **k):
        return lambda f: f

    @staticmethod
    def permission_type(*a, **k):
        return lambda f: f

    @staticmethod
    def event_message_type(*a, **k):
        return lambda f: f

    @staticmethod
    def on_decorating_result(*a, **k):
        return lambda f: f

    @staticmethod
    def on_astrbot_loaded(*a, **k):
        return lambda f: f

    @staticmethod
    def llm_tool(*a, **k):
        return lambda f: f


class MessageChain:
    def message(self, text):
        self.chain = [Plain(text)]
        return self


class AstrMessageEvent:
    def __init__(self, message_str="", group_id="", umo="umo::test", sender="Tester"):
        self.message_str = message_str
        self.unified_msg_origin = umo
        self._group_id = group_id
        self._sender = sender
        self.stopped = False
        self.sent = []
        self._result = None
        self.is_at_or_wake_command = False

    def get_group_id(self):
        return self._group_id

    def get_sender_name(self):
        return self._sender

    def get_sender_id(self):
        return "10001"

    def get_self_id(self):
        return "242826716"

    def plain_result(self, text):
        return Plain(text)

    def chain_result(self, chain):
        return chain

    def stop_event(self):
        self.stopped = True

    def get_result(self):
        return self._result

    def get_messages(self):
        return getattr(self, "_messages", [])

    def set_result(self, result):
        self._result = result


event_mod = types.ModuleType("astrbot.api.event")
event_mod.filter = _Filter()
event_mod.AstrMessageEvent = AstrMessageEvent
event_mod.MessageChain = MessageChain
event_mod.EventMessageType = EventMessageType


class _Result:
    def __init__(self, chain):
        self.chain = chain


# ---- star ----

class Context:
    def __init__(self):
        self.sent = []
        self.provider = None

    def get_using_provider(self):
        return self.provider

    async def send_message(self, umo, chain):
        self.sent.append((umo, chain))


_STORAGE = {}


class Star:
    def __init__(self, context):
        self.context = context
        self.name = "stub_plugin"

    async def get_kv_data(self, key, default=None):
        return _STORAGE.get((id(self.context), key), default)

    async def put_kv_data(self, key, value):
        _STORAGE[(id(self.context), key)] = value

    async def delete_kv_data(self, key):
        _STORAGE.pop((id(self.context), key), None)


star_mod = types.ModuleType("astrbot.api.star")
star_mod.Context = Context
star_mod.Star = Star

# ---- data path ----

core_mod = types.ModuleType("astrbot.core")
utils_mod = types.ModuleType("astrbot.core.utils")
path_mod = types.ModuleType("astrbot.core.utils.astrbot_path")
path_mod.get_astrbot_data_path = lambda: str(TMP)

pkg = types.ModuleType("astrbot")
pkg.api = api
pkg.core = core_mod
core_mod.utils = utils_mod
utils_mod.astrbot_path = path_mod

sys.modules.update({
    "astrbot": pkg,
    "astrbot.api": api,
    "astrbot.api.event": event_mod,
    "astrbot.api.star": star_mod,
    "astrbot.api.message_components": comps,
    "astrbot.core": core_mod,
    "astrbot.core.utils": utils_mod,
    "astrbot.core.utils.astrbot_path": path_mod,
})

# 没装 aiohttp 的环境用空模块顶替（测试里网络函数全部 monkeypatch，不会真调用）
if importlib.util.find_spec("aiohttp") is None:
    _aiohttp = types.ModuleType("aiohttp")
    _aiohttp.ClientTimeout = lambda **kw: None
    _aiohttp.ClientSession = object
    sys.modules["aiohttp"] = _aiohttp

PLUGINS = ROOT.parent / "plugins"


def load_plugin(name):
    main = PLUGINS / name / "main.py"
    spec = importlib.util.spec_from_file_location(f"{name}.main", main)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"{name}.main"] = mod
    spec.loader.exec_module(mod)
    return mod


PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f"  {detail}" if detail and not cond else ""))


async def main():
    # ---------- access ----------
    access = load_plugin("astrbot_plugin_aemeath_access")
    ctx = Context()
    cfg = AstrBotConfig({"enforce": True})
    ap = access.AemeathAccessPlugin(ctx, cfg)

    ev = AstrMessageEvent("/allow 753063398")
    await anext_or_drain(ap.allow(ev))
    check("access: /allow 新增", (await ap._groups()) == [753063398])

    ev = AstrMessageEvent("/allow list")
    out = await drain(ap.allow(ev))
    check("access: /allow list", "753063398" in out[0].text)

    ev = AstrMessageEvent("/allow 753063398")
    out = await drain(ap.allow(ev))
    check("access: 重复添加提示", "已经在白名单" in out[0].text)

    ev = AstrMessageEvent("/deny 753063398")
    await drain(ap.deny(ev))
    check("access: /deny 移除", (await ap._groups()) == [])

    # 拦截：白名单为空时不拦截
    ev = AstrMessageEvent("hi", group_id="111")
    await ap.gate_group(ev)
    check("access: 空名单不拦截", ev.stopped is False)

    await ap._save([111])
    ev = AstrMessageEvent("hi", group_id="222")
    await ap.gate_group(ev)
    check("access: 非白名单群拦截", ev.stopped is True)

    ev = AstrMessageEvent("hi", group_id="111")
    await ap.gate_group(ev)
    check("access: 白名单群放行", ev.stopped is False)

    # ---------- voice ----------
    voice = load_plugin("astrbot_plugin_aemeath_voice")
    vcfg = AstrBotConfig({
        "enabled": True, "api_key": "sk-test", "voice": "v",
        "model": "cosyvoice-v3.5-flash", "prefixes": ["语音", "说", "朗读一下"],
        "probability": 1.0, "max_chars": 120, "cooldown": 0, "format": "wav",
    })
    vp = voice.AemeathVoicePlugin(Context(), vcfg)
    check("voice: 多字前缀直接接内容", vp._match_prefix("语音你好呀") == "你好呀")
    check("voice: 单字前缀需分隔符", vp._match_prefix("说真的") is None)
    check("voice: 单字前缀带空格", vp._match_prefix("说 你好") == "你好")
    check("voice: 非触发文本", vp._match_prefix("今天吃什么") is None)

    fake_wav = TMP / "fake.wav"
    fake_wav.write_bytes(b"RIFF")
    vp._synth_file = lambda text: asyncio.sleep(0, result=fake_wav)

    ev = AstrMessageEvent("语音 你好呀", group_id="111")
    out = await drain(vp.voice_trigger(ev))
    flat = [seg for x in out for seg in (x if isinstance(x, list) else [x])]
    check("voice: 触发后停止事件", ev.stopped is True)
    check("voice: 发送 Record", any(isinstance(seg, Record) for seg in flat))

    # 概率语音：构造最终结果链
    from astrbot.api.message_components import Plain  # noqa: E402
    ev = AstrMessageEvent("qq")
    ev.set_result(_Result([Plain("收到收到～")]))
    await vp.maybe_voice_reply(ev)
    check("voice: 概率替换为 Record", isinstance(ev.get_result().chain[0], Record))

    # 超长回退
    ev = AstrMessageEvent("qq")
    ev.set_result(_Result([Plain("字" * 200)]))
    await vp.maybe_voice_reply(ev)
    check("voice: 超长回退文字", isinstance(ev.get_result().chain[0], Plain))

    # 表情包回复不转语音（交给表情插件发图）
    ev = AstrMessageEvent("qq")
    ev.set_result(_Result([Plain("给你～[emoji]")]))
    await vp.maybe_voice_reply(ev)
    check("voice: 表情包回复不转语音", isinstance(ev.get_result().chain[0], Plain))

    # 插件进度提示不转语音
    ev = AstrMessageEvent("qq")
    ev.set_result(_Result([Plain("今天的状态尚未生成，正在生成…")]))
    await vp.maybe_voice_reply(ev)
    check("voice: 状态提示不转语音", isinstance(ev.get_result().chain[0], Plain))

    # 含链接的回复不转语音（点歌分享等）
    ev = AstrMessageEvent("qq")
    ev.set_result(_Result([Plain("🎵 纸飞机 - 林忆莲\nhttps://music.163.com/song?id=256343")]))
    await vp.maybe_voice_reply(ev)
    check("voice: 链接回复不转语音", isinstance(ev.get_result().chain[0], Plain))

    # ---------- emoji ----------
    emoji = load_plugin("astrbot_plugin_aemeath_emoji")
    cache = TMP / "emoji_cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "菲比_开心.jpg").write_bytes(b"fake")
    ep = emoji.AemeathEmojiPlugin(Context(), AstrBotConfig({
        "enabled": True, "cache_dir": str(cache), "max_per_message": 3,
        "remote_enabled": False,
    }))
    check("emoji: 缓存计数", ep.local.count() == 1)

    ev = AstrMessageEvent("qq")
    ev.set_result(_Result([Plain("收到收到～[emoji] 交给我吧[表情]")]))
    await ep.decorate(ev)
    chain = ev.get_result().chain
    check("emoji: 标记替换为图片", any(isinstance(x, Image) for x in chain))
    check("emoji: 残留标记清理", all("[表情]" not in getattr(x, "text", "") for x in chain))

    ev = AstrMessageEvent("qq")
    ev.set_result(_Result([Plain("[emoji:菲比]")]))
    await ep.decorate(ev)
    check("emoji: 指定角色命中缓存", any(isinstance(x, Image) for x in ev.get_result().chain))

    # ---------- summary ----------
    summary = load_plugin("astrbot_plugin_aemeath_summary")
    from datetime import date
    scfg = AstrBotConfig({"record_enabled": True, "hour": 21, "minute": 0, "min_messages": 2, "persona": ""})
    sctx = Context()
    sp = summary.AemeathSummaryPlugin(sctx, scfg)

    for i in range(3):
        await sp.record(AstrMessageEvent(f"消息{i}", group_id="111"))

    files = list((TMP / "plugin_data" / "astrbot_plugin_aemeath_summary" / "daily" / date.today().isoformat()).glob("*.jsonl"))
    check("summary: 消息落盘", len(files) == 1 and len(files[0].read_text(encoding="utf-8").strip().splitlines()) == 3)

    class _Resp:
        completion_text = "今天大家聊得很开心呀 [emoji]"

    class _Provider:
        async def text_chat(self, **kwargs):
            return _Resp()

    sctx.provider = _Provider()
    await sp.run_summary()
    check("summary: 生成并发送", len(sctx.sent) == 1)
    if sctx.sent:
        text = sctx.sent[0][1].chain[0].text
        check("summary: 剔除表情标记", "[emoji]" not in text and "群聊总结" in text)

    # ---------- card ----------
    card = load_plugin("astrbot_plugin_aemeath_card")

    class _RawMsg:
        def __init__(self, segments):
            self.message = segments
            self.raw_message = ""

    class _MsgObj:
        def __init__(self, segments):
            self.raw_message = _RawMsg(segments)
            self.message_str = ""
            self.message = []

    cp = card.AemeathCardPlugin(Context(), AstrBotConfig({"enabled": True, "max_chars": 300}))

    card_json = json.dumps({
        "app": "com.tencent.structmsg",
        "meta": {"news": {"title": "【逆天中配】神人不行 第三集", "jumpUrl": "https://b23.tv/WNx5jZq"}},
    })
    ev = AstrMessageEvent("")
    ev.message_obj = _MsgObj([{"type": "json", "data": {"data": card_json}}])
    await cp.parse_card(ev)
    check("card: 解析标题与链接", "神人不行" in ev.message_str and "b23.tv" in ev.message_str)

    ev = AstrMessageEvent("")
    ev.message_obj = _MsgObj([{"type": "xml", "data": {"data": "<msg><item><title>测试视频</title><url>https://b23.tv/abc</url></item></msg>"}}])
    await cp.parse_card(ev)
    check("card: 解析 XML 卡片", "测试视频" in ev.message_str)

    ev = AstrMessageEvent("你好")
    ev.message_obj = _MsgObj([{"type": "json", "data": {"data": card_json}}])
    await cp.parse_card(ev)
    check("card: 有文字时跳过", ev.message_str == "你好")

    # ---------- group chat ----------
    group_chat = load_plugin("astrbot_plugin_aemeath_group_chat")

    class _ChatResp:
        completion_text = "是呀是呀，今天确实挺热的～"

    class _ChatProvider:
        async def text_chat(self, **kwargs):
            return _ChatResp()

    gcfg = AstrBotConfig({
        "enabled": True, "probability": 1.0, "cooldown": 0,
        "context_size": 5, "min_len": 2, "max_chars": 80, "persona": "",
    })
    gctx = Context()
    gctx.provider = _ChatProvider()
    gp = group_chat.AemeathGroupChatPlugin(gctx, gcfg)

    ev = AstrMessageEvent("今天好热啊", group_id="111")

    class _Raw:
        def __init__(self, mid):
            self.message_id = mid

    class _Obj:
        def __init__(self, mid):
            self.raw_message = _Raw(mid)

    ev.message_obj = _Obj("mid-123")
    out = await drain(gp.maybe_join(ev))
    flat = [seg for x in out for seg in (x if isinstance(x, list) else [x])]
    texts = [seg.text for seg in flat if isinstance(seg, Plain)]
    check("group_chat: 概率 1.0 会插话", texts and "热" in texts[0])
    check("group_chat: 插话后停止事件", ev.stopped is True)
    check("group_chat: 插话引用原消息", bool(flat) and isinstance(flat[0], Reply) and flat[0].id == "mid-123")

    ev = AstrMessageEvent("@机器人 今天好热啊", group_id="111")
    ev.is_at_or_wake_command = True
    out = await drain(gp.maybe_join(ev))
    check("group_chat: 被 @ 时不插话", not out)

    ev = AstrMessageEvent("爱弥斯 在吗", group_id="111")
    out = await drain(gp.maybe_join(ev))
    check("group_chat: 旧触发词开头不插话", not out)

    ev = AstrMessageEvent("[图片]", group_id="111")
    out = await drain(gp.maybe_join(ev))
    check("group_chat: 纯图片不插话", not out)

    class _SkipProvider:
        async def text_chat(self, **kwargs):
            class R:
                completion_text = "[skip]"
            return R()

    gctx2 = Context()
    gctx2.provider = _SkipProvider()
    gp2 = group_chat.AemeathGroupChatPlugin(gctx2, AstrBotConfig({
        "enabled": True, "probability": 1.0, "cooldown": 0,
        "context_size": 5, "min_len": 2, "max_chars": 80, "persona": "",
    }))
    ev = AstrMessageEvent("嗯嗯", group_id="222")
    out = await drain(gp2.maybe_join(ev))
    check("group_chat: [skip] 不发言", not out)

    # 冷却
    gcfg3 = AstrBotConfig({
        "enabled": True, "probability": 1.0, "cooldown": 60,
        "context_size": 5, "min_len": 2, "max_chars": 80, "persona": "",
    })
    gctx3 = Context()
    gctx3.provider = _ChatProvider()
    gp3 = group_chat.AemeathGroupChatPlugin(gctx3, gcfg3)
    await drain(gp3.maybe_join(AstrMessageEvent("第一句", group_id="333")))
    out = await drain(gp3.maybe_join(AstrMessageEvent("第二句", group_id="333")))
    check("group_chat: 冷却内不插话", not out)

    # ---------- poke ----------
    poke = load_plugin("astrbot_plugin_aemeath_poke")
    pp = poke.AemeathPokePlugin(Context(), AstrBotConfig({"enabled": True, "cooldown": 0, "replies": []}))
    ev = AstrMessageEvent("")
    ev._messages = [Poke(id="242826716")]  # stub 的 self_id
    out = await drain(pp.on_poke(ev))
    check("poke: 戳机器人会回应", any(isinstance(x, Plain) for x in out))
    ev = AstrMessageEvent("")
    ev._messages = [Poke(id="999")]
    out = await drain(pp.on_poke(ev))
    check("poke: 戳别人不回应", not out)

    # ---------- music ----------
    music = load_plugin("astrbot_plugin_aemeath_music")
    mp = music.AemeathMusicPlugin(Context(), AstrBotConfig({
        "enabled": True, "prefixes": ["演奏", "来一首"], "limit": 5, "cooldown": 0,
    }))

    async def fake_search(kw, limit=5):
        return [{"id": 123, "name": "纸飞机", "artist": "爱弥斯"}]

    mp.search = fake_search
    ev = AstrMessageEvent("演奏 纸飞机")
    out = await drain(mp.on_request(ev))
    txt = "".join(x.text for x in out if isinstance(x, Plain))
    check("music: 触发并分享链接", "music.163.com/song?id=123" in txt and "纸飞机" in txt)
    ev = AstrMessageEvent("今天演奏会取消了吗")
    out = await drain(mp.on_request(ev))
    check("music: 非触发不分享", not out)

    # ---------- wiki ----------
    wiki = load_plugin("astrbot_plugin_aemeath_wiki")

    fixture = (
        "{{共鸣者/基本资料\n"
        "|实装日期=20260205\n"
        "|名称=爱弥斯\n"
        "|初奏=<!--抽卡语音-->你，看见我了？\n"
        "|属性=热熔\n"
        "|武器=迅刀\n"
        "|品质=5\n"
        "|战斗风格=2,7,33\n"
        "|出生=拉海洛\n"
        "|势力=星炬学院\n"
        "|简介=星炬学院的{{引用|隧者}}适格者。<br/>电子幽灵。\n"
        "}}\n"
        "{{共鸣者/基础属性\n|生命=882\n}}\n"
    )
    fields = wiki.parse_template_fields(fixture, "共鸣者/基本资料")
    check("wiki: 模板字段解析", fields.get("名称") == "爱弥斯" and fields.get("属性") == "热熔")
    check("wiki: HTML 注释清理", fields.get("初奏") == "你，看见我了？")
    check("wiki: 引用模板与换行清理", fields.get("简介") == "星炬学院的隧者适格者。\n电子幽灵。")
    check("wiki: 战斗风格 ID 映射", wiki.format_styles("2,7,33") == "主力输出、共鸣解放伤害、震谐响应")
    check("wiki: 战斗风格名称透传", wiki.format_styles("主力输出,重击伤害") == "主力输出、重击伤害")
    check("wiki: 实装日期格式化", wiki.format_date("20260205") == "2026-02-05")

    # 材料解析：数量记法 / 汇总 / 万格式化
    check("wiki: 数量记法", wiki.parse_qty("1k5") == 1500 and wiki.parse_qty("1w6") == 16000
          and wiki.parse_qty("10w") == 100000 and wiki.parse_qty("2,252") == 2252
          and wiki.parse_qty("60") == 60)
    check("wiki: 材料串解析", wiki.parse_material_entries("龙吐珠*60,哀歌声核*46;贝币*1k5")
          == [("龙吐珠", 60), ("哀歌声核", 46), ("贝币", 1500)])
    totals = wiki.sum_material_entries("贝壳*2,贝币*1k5;", "贝壳*3,贝币*2k")
    check("wiki: 材料汇总", totals == {"贝壳": 5, "贝币": 3500})
    check("wiki: 万格式化", wiki.format_wan(330000) == "33万" and wiki.format_wan(1730000) == "173万"
          and wiki.format_wan(16000) == "1.6万" and wiki.format_wan(60) == "60")
    fmt = wiki.format_material_totals({"龙吐珠": 60, "贝币": 330000})
    check("wiki: 合计排版（贝币最后）", fmt == "龙吐珠×60、贝币×33万")

    names = ["爱弥斯", "今汐", "卡卡罗", "心", "秧秧", "秧秧·玄翎",
             "漂泊者·导电", "漂泊者·气动", "漂泊者·湮灭", "漂泊者·衍射"]
    check("wiki: 别名匹配", wiki.match_name("小爱", names)[0] == "爱弥斯")
    check("wiki: 包含匹配", wiki.match_name("今汐的资料", names)[0] == "今汐")
    check("wiki: 单字名精确命中", wiki.match_name("心", names)[0] == "心")
    check("wiki: 单字名不误配", wiki.match_name("心跳", names)[0] is None)
    multi = wiki.match_name("漂泊者", names)
    check("wiki: 多条目返回候选", multi[0] is None and len(multi[1]) == 4)
    check("wiki: 形近字容错", wiki.match_name("卡卡萝", names)[0] == "卡卡罗")

    card = wiki.build_card({
        "name": "爱弥斯", "quality": "5", "element": "热熔", "weapon": "迅刀",
        "styles": "主力输出", "intro": "电子幽灵", "url": "https://example.com",
    })
    check("wiki: 卡片排版", "★★★★★" in card and "热熔｜迅刀" in card and "https://example.com" in card)

    sec = "\n".join(wiki.build_material_section({
        "ascension_totals": {"龙吐珠": 60, "哀歌声核": 46},
        "skill_totals": {"海蚀嵌合体239": 61, "贝币": 1730000},
    }))
    check("wiki: 材料段完整", "龙吐珠×60" in sec and "贝币×173万" in sec and "技能满级" in sec)
    sec = "\n".join(wiki.build_material_section({"ascension_totals": {}, "skill_totals": {}}))
    check("wiki: 材料缺失如实标注", sec.count("暂未收录") == 2 and "未计入" in sec)
    sec = "\n".join(wiki.build_material_section({"name": "爱弥斯", "ascension_totals": {}, "skill_totals": {}}))
    check("wiki: 缺失时指向库街区", "/材料 爱弥斯" in sec)

    rec = wiki.build_weapon_rec_section(
        {"weapon": "迅刀"},
        [{"name": "千古洑流", "substat": "暴击", "version": "1.0"},
         {"name": "天之苍苍", "substat": "暴击", "version": "3.5"},
         {"name": "裁春", "substat": "暴击", "version": "1.4"}],
    )
    check("wiki: 武器推荐按版本降序",
          bool(rec) and rec[0].index("天之苍苍") < rec[0].index("裁春") < rec[0].index("千古洑流"))

    weapon_fixture = (
        "{{武器\n"
        "|实装版本=1.4\n"
        "|名称=裁春\n"
        "|类型=迅刀\n"
        "|品质=5\n"
        "|获取途径=限时武器活动唤取\n"
        "|武器技能=终始之外\n"
        "|技能描述=攻击提升{{颜色|技能|12%/15%/18%/21%/24%}}。<br>造成普攻伤害时提升伤害。\n"
        "|初始攻击=47\n"
        "|副词条=暴击\n"
        "|词条初始数值=0.054\n"
        "|%=1\n"
        "|突破材料总览=\n"
        "|20级突破材料=低频啸叫声核*6,贝币*10000\n"
        "|40级突破材料=中频啸叫声核*6,惰性金属液滴*6,贝币*20000\n"
        "|50级突破材料=高频啸叫声核*4,活性金属液滴*8,贝币*40000\n"
        "|60级突破材料=高频啸叫声核*6,极化金属液滴*6,贝币*60000\n"
        "|70级突破材料=全频啸叫声核*4,异构金属液滴*8,贝币*80000\n"
        "|80级突破材料=全频啸叫声核*8,异构金属液滴*12,贝币*120000\n"
        "}}\n"
    )
    widx = {"裁春": {"type": "迅刀", "quality": "5"}, "远行者迅刀·旅迹": {"type": "迅刀", "quality": "3"}}
    check("wiki: 武器精确匹配", wiki.match_weapon("裁春", widx) == "裁春")
    check("wiki: 武器包含匹配", wiki.match_weapon("裁春的资料", widx) == "裁春")
    check("wiki: 武器类型词不误配", wiki.match_weapon("迅刀", widx) is None)
    _orig_pinyin = wiki._pinyin
    wiki._pinyin = lambda s: {"裁椿": "caichun", "裁春": "caichun"}.get(s, "")
    check("wiki: 武器拼音容错（裁椿→裁春）", wiki.match_weapon("裁椿", widx) == "裁春")
    wiki._pinyin = _orig_pinyin

    wp = wiki.AemeathWikiPlugin(Context(), AstrBotConfig({
        "enabled": True, "prefixes": ["鸣潮wiki", "查角色"], "image_mode": "立绘",
        "cooldown": 0, "cache_dir": str(TMP / "wiki_cache"),
    }))

    async def fake_characters(force=False):
        return names

    async def fake_wikitext(title):
        return fixture if title == "共鸣者/爱弥斯" else ""

    async def no_image(info):
        return None

    wp.client.characters = fake_characters
    wp.client.wikitext = fake_wikitext
    wp.character_image = no_image

    ev = AstrMessageEvent("/wiki 爱弥斯")
    out = await drain(wp.wiki_cmd(ev))
    flat = [seg for x in out for seg in (x if isinstance(x, list) else [x])]
    txt = "".join(seg.text for seg in flat if isinstance(seg, Plain))
    check("wiki: /wiki 发送角色卡片", "爱弥斯" in txt and "热熔" in txt and "wiki.biligame.com" in txt)

    ev = AstrMessageEvent("鸣潮wiki 小爱")
    out = await drain(wp.wiki_prefix(ev))
    flat = [seg for x in out for seg in (x if isinstance(x, list) else [x])]
    txt = "".join(seg.text for seg in flat if isinstance(seg, Plain))
    check("wiki: 前缀触发命中别名", ev.stopped is True and "爱弥斯" in txt)
    check("wiki: 卡片不转语音", getattr(ev, "_aemeath_voice_sent", False) is True)

    fake_png = TMP / "fake_lihui.png"
    fake_png.write_bytes(b"\x89PNG\r\n\x1a\n")

    async def with_image(info):
        return fake_png

    wp.character_image = with_image
    ev = AstrMessageEvent("/wiki 爱弥斯")
    out = await drain(wp.wiki_cmd(ev))
    flat = [seg for x in out for seg in (x if isinstance(x, list) else [x])]
    check("wiki: 立绘图片段发送", any(isinstance(seg, Image) for seg in flat))
    wp.character_image = no_image

    # 角色卡附加段：养成材料（fixture 无数据→标注）+ 同类型五星武器推荐
    async def fake_weapons_by_type(wtype, quality=5):
        if wtype != "迅刀":
            return []
        return [{"name": "千古洑流", "substat": "暴击", "version": "1.0"},
                {"name": "天之苍苍", "substat": "暴击", "version": "3.5"}]

    wp.client.weapons_by_type = fake_weapons_by_type
    text, _ = await wp.lookup("爱弥斯")
    check("wiki: 角色卡带材料段", "养成材料" in text and "WIKI 暂未收录该角色突破材料" in text)
    check("wiki: 角色卡带武器推荐", "五星迅刀" in text and "天之苍苍(暴击)" in text)

    # 技能材料集成：wiki 有数据的角色（今汐风格 fixture）
    jinxi_fixture = (
        "{{共鸣者/基本资料\n|名称=今汐\n|属性=衍射\n|武器=长刃\n|品质=5\n}}\n"
        "{{共鸣者/基础属性\n|生命=866\n|突破消耗材料总数=龙吐珠*60,哀歌声核*46\n}}\n"
        "{{共鸣者/技能\n|角色名称=今汐\n"
        "|常态攻击升级消耗=海蚀嵌合体210*2,低频啸叫声核*2,贝币*1k5;\n"
        "海蚀嵌合体210*3,低频啸叫声核*3,贝币*2k\n"
        "|共鸣技能升级消耗=海蚀嵌合体210*2,贝币*1k5\n"
        "}}\n"
    )

    async def fake_wikitext2(title):
        if title == "共鸣者/爱弥斯":
            return fixture
        if title == "共鸣者/今汐":
            return jinxi_fixture
        if title == "武器/裁春":
            return weapon_fixture
        return ""

    wp.client.wikitext = fake_wikitext2
    text, _ = await wp.lookup("今汐")
    check("wiki: 技能材料合计", "海蚀嵌合体210×7" in text and "低频啸叫声核×5" in text and "贝币×5,000" in text)
    check("wiki: 突破材料合计", "龙吐珠×60" in text and "哀歌声核×46" in text)

    # 武器路由：/wiki 裁春 → 武器卡片（90级面板 + 六档材料汇总）
    async def fake_weapon_index():
        return widx

    wp.client.weapon_index = fake_weapon_index
    text, _ = await wp.lookup("裁春")
    check("wiki: 武器卡片", "裁春（武器）" in text and "90级攻击 587" in text
          and "暴击 5.4%→24.3%" in text and "限时武器活动唤取" in text)
    check("wiki: 武器材料六档汇总", "高频啸叫声核×10" in text and "异构金属液滴×20" in text
          and "贝币×33万" in text)
    check("wiki: 武器技能描述清理", "12%/15%/18%/21%/24%" in text and "{{颜色" not in text)

    text, img = await wp.lookup("漂泊者")
    check("wiki: 多条目询问用户", "漂泊者·衍射" in text and img is None)

    async def fake_search(kw, limit=3):
        return [{"title": "武器/四尺玉", "snippet": "测试摘要"}] if "四尺" in kw else []

    wp.client.search = fake_search
    text, _ = await wp.lookup("四尺玉")
    check("wiki: 搜索兜底摘要与链接", "武器/四尺玉" in text and "测试摘要" in text)
    text, _ = await wp.lookup("不存在的东西")
    check("wiki: 未找到提示", "没找到" in text)

    tool_text = await wp.wiki_tool(AstrMessageEvent(""), "爱弥斯")
    check("wiki: LLM 工具返回卡片", "爱弥斯" in tool_text and "热熔" in tool_text)

    async def empty_wikitext(title):
        return ""

    wp.client.wikitext = empty_wikitext
    text, _ = await wp.lookup("爱弥斯")
    check("wiki: 资料缓存命中不再请求网络", "爱弥斯" in text and "热熔" in text)

    # ---------- kurobbs（库街区） ----------
    kurobbs = load_plugin("astrbot_plugin_aemeath_kurobbs")

    kb_search = [
        {"name": "今汐", "entryId": 1249040336606580736, "previewEntryId": "",
         "category": "全部 / 游戏图鉴 / 共鸣者", "catId": 1105},
        {"name": "今汐", "entryId": 18963, "previewEntryId": "1331088925953253376",
         "category": "全部 / 攻略合集 / 角色攻略", "catId": 1384},
        {"name": "【WIKI】今汐角色攻略", "entryId": 1331088925953253376, "previewEntryId": "",
         "category": "全部 / 攻略合集 / 角色攻略 / 今汐", "catId": 1565},
        {"name": "萌新今汐攻略", "entryId": 21566, "previewEntryId": "",
         "category": "全部 / 攻略合集 / 角色攻略 / 今汐", "catId": 1565},
    ]
    codex = kurobbs.pick_entry(kb_search, "今汐", "codex")
    check("kurobbs: 图鉴词条选取", codex and codex["entryId"] == "1249040336606580736")
    guide = kurobbs.pick_entry(kb_search, "今汐", "guide")
    check("kurobbs: 攻略优先官方WIKI正文", guide and guide["entryId"] == "1331088925953253376")
    guide2 = kurobbs.pick_entry([kb_search[1]], "今汐", "guide")
    check("kurobbs: 占位卡片走 previewEntryId", guide2 and guide2["entryId"] == "1331088925953253376")

    asc_md = (
        "### 角色突破材料\n\n#### 一阶突破\n"
        "| [低频啸叫声核](https://wiki.kurobbs.com/mc/item/111)x4 | [贝币](https://wiki.kurobbs.com/mc/item/222)x5000 |\n\n"
        "#### 二阶突破\n"
        "| [](https://wiki.kurobbs.com/mc/item/333)[中频啸叫声核](https://wiki.kurobbs.com/mc/item/444)x4 | [龙吐珠](https://wiki.kurobbs.com/mc/item/555)x4 |\n"
        "| [哀歌声核](https://wiki.kurobbs.com/mc/item/666)x3 | [贝币](https://wiki.kurobbs.com/mc/item/222)x10000 |\n"
    )
    totals = kurobbs.parse_material_totals(asc_md)
    check("kurobbs: 突破材料汇总", totals == {"低频啸叫声核": 4, "中频啸叫声核": 4,
                                          "龙吐珠": 4, "哀歌声核": 3, "贝币": 15000})
    check("kurobbs: 合计排版", kurobbs.format_totals(totals).endswith("贝币×1.5万"))

    weapon_md = (
        "## 武器推荐\n\n#### 主流武器\n"
        "| **武器推荐 ** | [](https://u/1) [](https://u/2) "
        "[时和岁稔](https://u/1)> [苍鳞千嶂](https://u/2)> [浩境粼光](https://u/3)> [纹秋](https://u/4) 无特别说明，武器默认1阶 |\n"
        "| [](https://u/1) | [**承天之佑**](https://u/1) 谐振(**1/2/3/4/5**)阶 全属性伤害加成提升 |\n"
    )
    check("kurobbs: 武器排名提取",
          kurobbs.extract_weapon_rank(weapon_md) == "时和岁稔 > 苍鳞千嶂 > 浩境粼光 > 纹秋")

    echo_md = (
        "#### 主流声骸\n"
        "| [](https://u/9) [**浮星祛暗**](https://u/9) | [](https://u/8) |\n"
        "| --- | --- |\n"
        "| **COST分配** | **毕业：43311** |\n"
        "| **COST主词条** | **COST4：**暴击 **/** 暴击伤害 **COST3：**双衍射伤害加成 **COST1：**攻击 |\n"
    )
    echo = kurobbs.extract_echo(echo_md)
    check("kurobbs: 声骸提取", echo["set"] == "浮星祛暗" and echo["cost"] == "43311"
          and "C4：暴击/暴击伤害" in echo["main"])

    teams_md = (
        "| **队伍组成** | **[今汐](https://u/1)+[折枝](https://u/2)+[守岸人](https://u/3)/[维里奈](https://u/4)"
        " ----- [今汐](https://u/1)+[渊武](https://u/5)+[守岸人](https://u/3)/[维里奈](https://u/4)"
        " ----- [今汐](https://u/1)+[莫特斐](https://u/6)+[鉴心](https://u/7)** |\n"
    )
    teams = kurobbs.extract_teams(teams_md)
    check("kurobbs: 主流队伍提取", teams[0] == "今汐+折枝+守岸人/维里奈" and len(teams) == 3)

    team_cli = (
        "[probe] 检查 折枝 画像...\n"
        "# 今汐 最佳组队 Top2\n> 角色池来源：手动指定（3 名）\n\n"
        "🥇 **今汐 + 折枝 + 维里奈**  [🔵引擎推断]\n   🎯 增益主C清单：\n\n"
        "🥈 **今汐 + 折枝 + 守岸人**  [🟡混合] 📚https://wiki.kurobbs.com/mc/item/123\n"
    )
    parsed = kurobbs.parse_team_output(team_cli)
    check("kurobbs: 配队输出解析", len(parsed) == 2 and parsed[0]["team"] == "今汐 + 折枝 + 维里奈"
          and parsed[1]["url"] == "https://wiki.kurobbs.com/mc/item/123")

    kp = kurobbs.AemeathKurobbsPlugin(Context(), AstrBotConfig({
        "enabled": True, "timeout": 5, "team_timeout": 5, "top_teams": 3,
        "cooldown": 0, "cache_hours": 24, "cache_dir": str(TMP / "kb_cache"),
    }))

    kb_calls = []

    async def fake_run(args, timeout=None):
        kb_calls.append(list(args))
        if args[0] == "search":
            return json.dumps(kb_search, ensure_ascii=False)
        if args[0] == "detail":
            return {"角色突破材料": asc_md, "武器推荐": weapon_md,
                    "声骸套装推荐": echo_md, "编队&队伍轴推荐": teams_md}.get(args[3], "")
        if args[0] == "team":
            return team_cli
        return ""

    kp._run = fake_run

    text = await kp.query_materials("今汐")
    check("kurobbs: 材料卡片", "低频啸叫声核×4" in text and "贝币×1.5万" in text
          and "wiki.kurobbs.com/mc/item/1249040336606580736" in text)

    text = await kp.query_materials("小爱")
    check("kurobbs: 别名解析", "爱弥斯" in text and kb_calls[-1][1] == "爱弥斯")

    text = await kp.query_guide("今汐")
    check("kurobbs: 攻略卡片", "时和岁稔 > 苍鳞千嶂" in text and "浮星祛暗" in text
          and "毕业43311" in text and "今汐+折枝+守岸人/维里奈" in text and "仅供当期参考" in text)

    text = await kp.query_team("今汐", "折枝,维里奈")
    check("kurobbs: 配队卡片", "🥇" in text and "今汐 + 折枝 + 维里奈" in text)
    check("kurobbs: 配队用指定池", any(a[0] == "team" and "--pool" in a for a in kb_calls))
    await kp.query_team("今汐")
    check("kurobbs: 无池子用攻略补池", "--guide-pool" in kb_calls[-1])

    text = await kp.query_search("今汐")
    check("kurobbs: 搜索列表", "游戏图鉴 / 共鸣者" in text and "wiki.kurobbs.com/mc/item/" in text)

    ev = AstrMessageEvent("/材料 今汐")
    out = await drain(kp.cmd_materials(ev))
    flat = [seg for x in out for seg in (x if isinstance(x, list) else [x])]
    txt = "".join(seg.text for seg in flat if isinstance(seg, Plain))
    check("kurobbs: /材料 指令", "低频啸叫声核×4" in txt)

    tool_text = await kp.kurobbs_tool(AstrMessageEvent(""), "今汐", "突破材料")
    check("kurobbs: LLM 工具", "低频啸叫声核×4" in tool_text)

    # ---------- group_chat 冷群间隔 ----------
    class _CapProvider:
        def __init__(self):
            self.prompts = []

        async def text_chat(self, **kw):
            self.prompts.append(kw.get("prompt", ""))
            return _ChatResp()

    cap = _CapProvider()
    gctx4 = Context()
    gctx4.provider = cap
    gcfg4 = AstrBotConfig({
        "enabled": True, "probability": 1.0, "cooldown": 0, "context_size": 15,
        "min_len": 2, "max_chars": 0, "persona": "", "gap_minutes": 30,
    })
    gp4 = group_chat.AemeathGroupChatPlugin(gctx4, gcfg4)
    await drain(gp4.maybe_join(AstrMessageEvent("旧话题甲", group_id="444")))
    gp4._history["444"] = deque(
        [(time.time() - 7200, ln) for _, ln in gp4._history["444"]], maxlen=15
    )
    await drain(gp4.maybe_join(AstrMessageEvent("新话题乙", group_id="444")))
    last_prompt = cap.prompts[-1]
    check("group_chat: 冷群后清空旧上下文", "旧话题甲" not in last_prompt and "新话题乙" in last_prompt)

    print(f"\n===== 通过 {len(PASS)} / {len(PASS) + len(FAIL)} =====")
    if FAIL:
        print("失败项:", FAIL)
        sys.exit(1)


async def drain(gen):
    out = []
    async for item in gen:
        out.append(item)
    return out


async def anext_or_drain(gen):
    await drain(gen)


if __name__ == "__main__":
    asyncio.run(main())
