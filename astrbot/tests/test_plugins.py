"""
本机验收：不依赖 AstrBot/Docker，用桩模块加载插件并跑核心逻辑。

覆盖 README 验收清单里可离线验证的项：
  1. access: /allow /deny /status、非白名单群拦截
  2. voice: 前缀匹配、显式触发（stop_event + 发送语音）、概率语音替换
  3. emoji: [emoji] 标记替换为图片、残留标记清理
  4. summary: 群消息落盘、/summary 触发生成并发送

真实语音合成（网络）见 astrbot/tests/test_tts_live.py。
"""
import asyncio
import importlib.util
import json
import shutil
import sys
import types
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


comps = types.ModuleType("astrbot.api.message_components")
comps.Plain, comps.Image, comps.Record = Plain, Image, Record

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

    def get_group_id(self):
        return self._group_id

    def get_sender_name(self):
        return self._sender

    def plain_result(self, text):
        return Plain(text)

    def chain_result(self, chain):
        return chain

    def stop_event(self):
        self.stopped = True

    def get_result(self):
        return self._result

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

    # ---------- emoji ----------
    emoji = load_plugin("astrbot_plugin_aemeath_emoji")
    cache = TMP / "emoji_cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "菲比_开心.jpg").write_bytes(b"fake")
    ep = emoji.AemeathEmojiPlugin(Context(), AstrBotConfig({"enabled": True, "cache_dir": str(cache), "max_per_message": 3}))
    check("emoji: 缓存计数", ep.source.count() == 1)

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
