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
import os
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
    def __init__(self, path=None, file=None, url=None):
        self.path = path or file or url
        self.file = file
        self.url = url

    @classmethod
    def fromFileSystem(cls, path):
        return cls(path, file=path)

    @classmethod
    def fromURL(cls, url):
        return cls(url, url=url)

    def __repr__(self):
        return f"Image({self.path})"


class Node:
    def __init__(self, content=None, **kw):
        self.content = list(content or [])
        self.name = kw.get("name", "")
        self.uin = kw.get("uin", "0")

    def __repr__(self):
        return f"Node({self.name}, {self.content})"


class Nodes:
    def __init__(self, nodes=None, **kw):
        self.nodes = list(nodes or [])

    def __repr__(self):
        return f"Nodes({self.nodes})"


class Record:
    def __init__(self, file=None, url=None):
        self.file = file
        self.url = url

    def __repr__(self):
        return f"Record({self.file})"


class Video:
    def __init__(self, file=None, **kwargs):
        self.file = file

    @classmethod
    def fromURL(cls, url):
        return cls(file=url)

    def __repr__(self):
        return f"Video({self.file})"


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
comps.Video = Video
comps.Node, comps.Nodes = Node, Nodes

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
    def on_llm_request(*a, **k):
        return lambda f: f

    @staticmethod
    def on_astrbot_loaded(*a, **k):
        return lambda f: f

    @staticmethod
    def llm_tool(*a, **k):
        return lambda f: f


class MessageChain:
    def __init__(self, chain=None, **kwargs):
        self.chain = list(chain or [])

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
        return "<OLD_BOT_QQ>"

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


def _chain_text(chain):
    """把消息链里的文字抽出来（含合并转发节点里的，递归）"""
    out = []

    def walk(comps):
        for seg in comps or []:
            if isinstance(seg, Nodes):
                for node in seg.nodes:
                    walk(node.content)
            elif isinstance(seg, Node):
                walk(seg.content)
            else:
                out.append(getattr(seg, "text", ""))

    walk(chain)
    return "".join(out)


async def main():
    # ---------- access ----------
    access = load_plugin("astrbot_plugin_aemeath_access")
    ctx = Context()
    cfg = AstrBotConfig({"enforce": True})
    ap = access.AemeathAccessPlugin(ctx, cfg)

    ev = AstrMessageEvent("/allow 900000001")
    await anext_or_drain(ap.allow(ev))
    check("access: /allow 新增", (await ap._groups()) == [900000001])

    ev = AstrMessageEvent("/allow list")
    out = await drain(ap.allow(ev))
    check("access: /allow list", "900000001" in out[0].text)

    ev = AstrMessageEvent("/allow 900000001")
    out = await drain(ap.allow(ev))
    check("access: 重复添加提示", "已经在白名单" in out[0].text)

    ev = AstrMessageEvent("/deny 900000001")
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

    # 配置字段直接控制白名单（控制台可编辑，文件改动无需重启）
    cfg2 = AstrBotConfig({"enforce": True, "whitelistGroups": "900000001, 900000003"})
    ap2 = access.AemeathAccessPlugin(Context(), cfg2)
    check("access: 配置里的白名单生效", (await ap2._groups()) == [900000001, 900000003])

    ev = AstrMessageEvent("hi", group_id="900000002")
    await ap2.gate_group(ev)
    check("access: 配置名单外拦截", ev.stopped is True)

    await ap2._save([900000001, 900000002])
    check("access: /allow 写回配置字段", cfg2.get("whitelistGroups") == "900000001,900000002")
    ev = AstrMessageEvent("hi", group_id="900000002")
    await ap2.gate_group(ev)
    check("access: 保存后立即放行", ev.stopped is False)

    # 控制台改配置文件：mtime 变了就重读，无需重启
    cfg_file = TMP / "access_cfg.json"
    cfg_file.write_text(json.dumps({"whitelistGroups": "111222"}), encoding="utf-8")

    class _FileCfg(AstrBotConfig):
        def __init__(self, data, path):
            super().__init__(data)
            self.config_path = str(path)

    ap3 = access.AemeathAccessPlugin(Context(), _FileCfg({"enforce": True}, cfg_file))
    check("access: 读取配置文件里的白名单", (await ap3._groups()) == [111222])
    cfg_file.write_text(json.dumps({"whitelistGroups": "333444"}), encoding="utf-8")
    os.utime(cfg_file, (time.time() + 5, time.time() + 5))
    check("access: 文件变更免重启生效", (await ap3._groups()) == [333444])

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

    # ---------- diary ----------
    diary = load_plugin("astrbot_plugin_aemeath_diary")
    dcfg = AstrBotConfig({
        "record_enabled": True, "hour": 23, "minute": 30, "min_messages": 2,
        "max_per_group": 40, "max_messages": 120,
        "send_to_admin": True, "admin_qq": "10001", "keep_days": 0, "persona": "",
    })
    dctx = Context()
    dp = diary.AemeathDiaryPlugin(dctx, dcfg)

    for txt in ("今天好热", "群主又抽卡歪了", "晚上吃什么"):
        await dp.record(AstrMessageEvent(txt, group_id="777"))
    for txt in ("深渊又打不过了", "新版本什么时候来"):
        await dp.record(AstrMessageEvent(txt, group_id="888"))

    today = date.today().isoformat()
    droot = TMP / "plugin_data" / "astrbot_plugin_aemeath_diary"
    dfiles = sorted((droot / "daily" / today).glob("*.jsonl"))
    check("diary: 多群消息落盘", len(dfiles) == 2)

    class _DiaryResp:
        completion_text = "今天两个群里聊了抽卡、深渊和晚饭，我插了几句嘴 [emoji] 很开心。"

    class _DiaryProvider:
        async def text_chat(self, **kwargs):
            return _DiaryResp()

    dctx.provider = _DiaryProvider()
    await dp.run_diary()
    diary_md = droot / "diary" / f"{today}.md"
    check("diary: 合并生成单篇", diary_md.exists() and "两个群" in diary_md.read_text(encoding="utf-8"))
    check("diary: 不再按群分文件", not list((droot / "diary").glob(f"{today}/*.md")))
    check("diary: 合集备份", (droot / "diary" / "合集.md").exists())
    check("diary: 只发管理员私聊",
          len(dctx.sent) == 1 and dctx.sent[0][0].endswith(":FriendMessage:10001")
          and "日记" in dctx.sent[0][1].chain[0].text)
    check("diary: 剔除表情标记", "[emoji]" not in dctx.sent[0][1].chain[0].text)

    ev = AstrMessageEvent("/日记", group_id="777")
    out = await drain(dp.diary_cmd(ev))
    flat = [seg for x in out for seg in (x if isinstance(x, list) else [x])]
    txt = "".join(seg.text for seg in flat if isinstance(seg, Plain))
    check("diary: /日记 查看当天", "两个群" in txt)

    ev = AstrMessageEvent("/日记 2020-01-01", group_id="777")
    out = await drain(dp.diary_cmd(ev))
    flat = [seg for x in out for seg in (x if isinstance(x, list) else [x])]
    check("diary: 无日记时的提示", any("还没有日记" in getattr(s, "text", "") for s in flat))

    # ---------- help ----------
    help_mod = load_plugin("astrbot_plugin_aemeath_help")
    hctx = Context()
    hp = help_mod.AemeathHelpPlugin(hctx, AstrBotConfig({"enabled": True}))
    ev = AstrMessageEvent("/帮助", group_id="801")
    out = await drain(hp.help_cmd(ev))
    flat = [seg for x in out for seg in (x if isinstance(x, list) else [x])]
    help_text = "".join(getattr(s, "text", "") for s in flat)
    check("help: /帮助 返回指令总览",
          "/wiki" in help_text and "/材料" in help_text and "吃史仙人" in help_text
          and "/日记" in help_text and ev.stopped)

    # ---------- watchdog ----------
    wd = load_plugin("astrbot_plugin_aemeath_watchdog")

    wd_tmp = Path("_tmp_wd_test")
    shutil.rmtree(wd_tmp, ignore_errors=True)
    wd_tmp.mkdir(parents=True, exist_ok=True)
    wd.get_astrbot_data_path = lambda: str(wd_tmp)
    wctx = Context()
    wp = wd.AemeathWatchdogPlugin(
        wctx, AstrBotConfig({"enabled": True, "interval": 60, "admin_qq": "10001",
                             "platform": "aiocqhttp"}))
    check("watchdog: 无状态不通知", (await wp.notify_recovered()) is False and not wctx.sent)

    sp = wp._state_path()
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps({"pending": {"since": 1, "until": 2, "seconds": 300,
                                          "reason": "需要扫码"}}, ensure_ascii=False),
                  encoding="utf-8")
    ok = await wp.notify_recovered()
    wd_text = _chain_text(wctx.sent[0][1].chain) if wctx.sent else ""
    check("watchdog: 恢复通知",
          ok and "掉线了约 5 分钟" in wd_text and "需要扫码" in wd_text)
    check("watchdog: 通知后清空 pending", wp.read_state().get("pending") is None)
    shutil.rmtree(wd_tmp, ignore_errors=True)

    # ---------- chishi ----------
    chishi = load_plugin("astrbot_plugin_aemeath_chishi")

    lvl, body = chishi.level_comment("LV: 3\n这操作逆天")
    check("chishi: 分级与评语解析", lvl == 3 and body == "这操作逆天")
    lvl, body = chishi.level_comment("就是一句反应")
    check("chishi: 无分级时回退", lvl == 0 and body == "就是一句反应")
    lvl, body = chishi.level_comment("标签：抽象\n等级：3\n猪：这算升职还是发配")
    check("chishi: 中文元信息不入评语", lvl == 3 and body == "猪：这算升职还是发配")
    check("chishi: 中文标签解析", chishi.parse_tag("标签：抽象") == ["抽象"])

    check("chishi: 搬运指令解析",
          chishi.match_move("搬过去") == ("feed", "")
          and chishi.match_move("发我") == ("self", "")
          and chishi.match_move("发给12345678") == ("private", "12345678")
          and chishi.match_move("搬到900000011") == ("group", "900000011")
          and chishi.match_move("群900000011") == ("group", "900000011"))
    check("chishi: 长句不误判", chishi.match_move("这个内容我发给别人看过了，你也看看") is None)

    class _RawObj:
        def __init__(self, segs):
            self.raw_message = {"message": segs}

    fwd_segs = [{"type": "forward", "data": {"id": "f1", "content": [
        {"type": "node", "data": {"nickname": "张三", "content": [
            {"type": "text", "data": {"text": "我裂开了"}}]}}]}}]
    ev = AstrMessageEvent("", group_id="901")
    ev.message_obj = _RawObj(fwd_segs)
    parts = chishi.parse_segments(ev)
    check("chishi: 转发文本解析", parts["kind"] == "forward" and any("我裂开了" in t for t in parts["texts"]))

    card_segs = [{"type": "json", "data": {"data": json.dumps(
        {"meta": {"news": {"title": "离谱新闻", "desc": "摘要内容",
                           "jumpUrl": "https://www.bilibili.com/video/BV1xx411c7mD"}}},
         ensure_ascii=False)}}]
    ev = AstrMessageEvent("", group_id="901")
    ev.message_obj = _RawObj(card_segs)
    parts = chishi.parse_segments(ev)
    check("chishi: 卡片解析", parts["kind"] == "card" and "离谱新闻" in parts["card_texts"][0])
    check("chishi: 卡片链接提取",
          parts["urls"] == ["https://www.bilibili.com/video/BV1xx411c7mD"])

    img_segs = [{"type": "image", "data": {"url": "https://example.com/a.png"}}]
    ev = AstrMessageEvent("", group_id="901")
    ev.message_obj = _RawObj(img_segs)
    parts = chishi.parse_segments(ev)
    check("chishi: 图片解析", parts["kind"] == "image" and parts["image_urls"] == ["https://example.com/a.png"])

    ccfg = AstrBotConfig({
        "enabled": True, "watchGroups": "", "commentGroups": "",
        "feedGroups": "900000011", "feedPrivateTarget": "10001",
        "commentEnabled": True, "feedProbability": 0, "ownerQQ": "",
        "summaryChars": 40, "feedMinLevel": 3, "feedMaxPerHour": 20,
        "maxChars": 60000, "maxImages": 6, "dedupeMinutes": 5, "persona": "",
    })
    cctx = Context()

    class _ShiResp:
        completion_text = "LV: 2\n这操作离谱了"

    class _ShiProvider:
        async def text_chat(self, **kwargs):
            return _ShiResp()

    cctx.provider = _ShiProvider()
    cp = chishi.AemeathChishiPlugin(cctx, ccfg)

    ev = AstrMessageEvent("", group_id="901")
    ev.message_obj = _RawObj(fwd_segs)
    out = await drain(cp.on_message(ev))
    flat = [seg for x in out for seg in (x if isinstance(x, list) else [x])]
    check("chishi: 转发触发评价", any("离谱" in getattr(s, "text", "") for s in flat) and ev.stopped)

    ev = AstrMessageEvent("搬过去", group_id="901")
    ev.message_obj = _RawObj([])
    out = await drain(cp.on_message(ev))
    flat = [seg for x in out for seg in (x if isinstance(x, list) else [x])]
    texts = "".join(getattr(s, "text", "") for s in flat)
    check("chishi: 搬过去用最近一份内容", "搬过去了" in texts and len(cctx.sent) == 1)
    sent_chain = cctx.sent[0][1].chain if cctx.sent else []
    sent_text = _chain_text(sent_chain)
    check("chishi: 搬运的是转发原文", "我裂开了" in sent_text)
    check("chishi: 搬运用合并转发卡片", any(isinstance(seg, Nodes) for seg in sent_chain))

    # 自动搬史黑名单：群 / 用户 命中时不自动搬
    check("chishi: 名单匹配",
          chishi.in_list("123,456 789", "456") is True
          and chishi.in_list("123,456 789", "999") is False
          and chishi.in_list("", "456") is False)
    check("chishi: 主题标签解析",
          chishi.parse_tag("TAG: 美少女,抽象\nLV: 3\n好") == ["美少女", "抽象"]
          and chishi.parse_tag("LV: 3\n好") == [])
    check("chishi: 口语搬运触发",
          chishi.match_move("这条也搬") == ("feed", "")
          and chishi.match_move("这个也搬") == ("feed", "")
          and chishi.match_move("再搬一次") == ("feed", "")
          and chishi.match_move("这条别搬") is None)
    check("chishi: 正常聊天不误判搬运",
          chishi.match_move("我要把这个喂给我家鱼") is None
          and chishi.match_move("这个转发挺好笑的") is None)

    # 「这条也搬」实际搬运（新实例，避免与上面的去重冲突）
    cctx_n = Context()
    cctx_n.provider = _ShiProvider()
    cp_n = chishi.AemeathChishiPlugin(cctx_n, ccfg)
    ev = AstrMessageEvent("", group_id="901")
    ev.message_obj = _RawObj(fwd_segs)
    await drain(cp_n.on_message(ev))
    ev = AstrMessageEvent("这条也搬", group_id="901")
    ev.message_obj = _RawObj([])
    out = await drain(cp_n.on_message(ev))
    flat = [seg for x in out for seg in (x if isinstance(x, list) else [x])]
    texts = "".join(getattr(s, "text", "") for s in flat)
    check("chishi: 这条也搬走流程", "搬过去了" in texts and len(cctx_n.sent) == 1)

    # 手动搬送到所有 feedGroups（排除来源群）
    ccfg_multi = AstrBotConfig({
        "enabled": True, "watchGroups": "", "commentGroups": "",
        "feedGroups": "900000011,900000012,900000013", "feedPrivateTarget": "10001",
        "commentEnabled": True, "feedProbability": 0, "ownerQQ": "",
        "summaryChars": 40, "feedMinLevel": 3, "feedMaxPerHour": 20,
        "maxChars": 60000, "maxImages": 6, "dedupeMinutes": 5, "persona": "",
    })
    cctx_multi = Context()
    cctx_multi.provider = _ShiProvider()
    cp_multi = chishi.AemeathChishiPlugin(cctx_multi, ccfg_multi)
    ev = AstrMessageEvent("", group_id="901")
    ev.message_obj = _RawObj(fwd_segs)
    await drain(cp_multi.on_message(ev))
    ev = AstrMessageEvent("搬过去", group_id="901")
    ev.message_obj = _RawObj([])
    out = await drain(cp_multi.on_message(ev))
    flat = [seg for x in out for seg in (x if isinstance(x, list) else [x])]
    texts = "".join(getattr(s, "text", "") for s in flat)
    check("chishi: 手动搬送到所有群",
          len(cctx_multi.sent) == 3 and "3 个群" in texts)

    # 数字消息 id + 协议端支持时，优先用「原生转发」保真（不重建卡片）
    class _NativeBot:
        def __init__(self):
            self.calls = []

        async def call_action(self, action, **kw):
            self.calls.append((action, kw))
            return {}

    cctx_nat = Context()
    cctx_nat.provider = _ShiProvider()
    cp_nat = chishi.AemeathChishiPlugin(cctx_nat, ccfg)
    ev = AstrMessageEvent("", group_id="901")
    ev.bot = _NativeBot()
    ev.message_obj = _RawObj(fwd_segs)
    ev.message_obj.message_id = "1234567"
    await drain(cp_nat.on_message(ev))
    ev = AstrMessageEvent("搬过去", group_id="901")
    ev.bot = ev.bot if hasattr(ev, "bot") else _NativeBot()
    ev.message_obj = _RawObj([])
    ev.message_obj.message_id = "7654321"
    out = await drain(cp_nat.on_message(ev))
    native_called = any(a == "forward_group_single_msg" for a, _ in ev.bot.calls)
    check("chishi: 数字 id 优先原生转发", native_called and len(cctx_nat.sent) == 0)

    def _auto_cfg(**kw):
        base = {
            "enabled": True, "watchGroups": "", "commentGroups": "",
            "feedGroups": "900000011", "feedPrivateTarget": "10001",
            "commentEnabled": True, "feedProbability": 1.0, "ownerQQ": "",
            "feedMinLevel": 1, "feedMaxPerHour": 20, "maxChars": 60000,
            "maxImages": 6, "dedupeMinutes": 5, "persona": "", "feedBlockList": "",
            "feedForceTags": "", "feedForceKeywords": "",
            "feedSkipTags": "", "feedSkipKeywords": "", "biliAutoFeed": False,
            "abstractOnlyGroups": "",
        }
        base.update(kw)
        return AstrBotConfig(base)

    cctx2 = Context()
    cctx2.provider = _ShiProvider()
    cp2 = chishi.AemeathChishiPlugin(cctx2, _auto_cfg(feedBlockList="901"))
    ev = AstrMessageEvent("", group_id="901")
    ev.message_obj = _RawObj(fwd_segs)
    await drain(cp2.on_message(ev))
    check("chishi: 黑名单群不自动搬史", len(cctx2.sent) == 0)

    cctx3 = Context()
    cctx3.provider = _ShiProvider()
    cp3 = chishi.AemeathChishiPlugin(cctx3, _auto_cfg(feedBlockList="10001"))
    ev = AstrMessageEvent("", group_id="902")
    ev.message_obj = _RawObj(fwd_segs)
    await drain(cp3.on_message(ev))
    check("chishi: 黑名单用户不自动搬史", len(cctx3.sent) == 0)

    cctx4 = Context()
    cctx4.provider = _ShiProvider()
    cp4 = chishi.AemeathChishiPlugin(cctx4, _auto_cfg())
    ev = AstrMessageEvent("", group_id="903")
    ev.message_obj = _RawObj(fwd_segs)
    await drain(cp4.on_message(ev))
    check("chishi: 未命中黑名单会自动搬", len(cctx4.sent) == 1)

    # 强制搬运①：TAG 命中 → 不看等级/概率/黑名单，直接搬
    class _TagProvider:
        async def text_chat(self, **kwargs):
            class R:
                completion_text = "TAG: 美少女\nLV: 1\n这图绝了"
            return R()

    cctx11 = Context()
    cctx11.provider = _TagProvider()
    cp11 = chishi.AemeathChishiPlugin(
        cctx11, _auto_cfg(feedProbability=0.0, feedMinLevel=5, feedBlockList="911",
                          feedForceTags="美少女"))
    ev = AstrMessageEvent("", group_id="911")
    ev.message_obj = _RawObj(fwd_segs)
    await drain(cp11.on_message(ev))
    check("chishi: TAG命中强制搬（无视概率/等级/黑名单）", len(cctx11.sent) == 1)

    # 强制搬运②：文本关键词命中 → 同样强制
    kw_segs = [{"type": "forward", "data": {"id": "fk", "content": [
        {"type": "node", "data": {"nickname": "甲", "content": [
            {"type": "text", "data": {"text": "美少女真好看"}}]}}]}}]
    cctx12 = Context()
    cctx12.provider = _ShiProvider()
    cp12 = chishi.AemeathChishiPlugin(
        cctx12, _auto_cfg(feedProbability=0.0, feedMinLevel=5, feedBlockList="912",
                          feedForceTags="", feedForceKeywords="美少女"))
    ev = AstrMessageEvent("", group_id="912")
    ev.message_obj = _RawObj(kw_segs)
    await drain(cp12.on_message(ev))
    check("chishi: 关键词命中强制搬", len(cctx12.sent) == 1)

    # 对照：普通内容概率 0 → 不搬
    cctx13 = Context()
    cctx13.provider = _ShiProvider()
    cp13 = chishi.AemeathChishiPlugin(cctx13, _auto_cfg(feedProbability=0.0))
    ev = AstrMessageEvent("", group_id="913")
    ev.message_obj = _RawObj(fwd_segs)
    await drain(cp13.on_message(ev))
    check("chishi: 普通内容概率0不搬", len(cctx13.sent) == 0)

    # 抽象内容也强制搬
    class _AbsProvider:
        async def text_chat(self, **kwargs):
            class R:
                completion_text = "TAG: 抽象\nLV: 1\n什么鬼东西"
            return R()

    cctx14 = Context()
    cctx14.provider = _AbsProvider()
    cp14 = chishi.AemeathChishiPlugin(
        cctx14, _auto_cfg(feedProbability=0.0, feedMinLevel=5, feedBlockList="914",
                          feedForceTags="美少女,抽象"))
    ev = AstrMessageEvent("", group_id="914")
    ev.message_obj = _RawObj(fwd_segs)
    await drain(cp14.on_message(ev))
    check("chishi: 抽象标签强制搬", len(cctx14.sent) == 1)

    # R18 硬拦截：优先级最高，命中强制标签也不搬
    class _R18Provider:
        async def text_chat(self, **kwargs):
            class R:
                completion_text = "TAG: 美少女,R18\nLV: 5\n这不太好吧"
            return R()

    cctx15 = Context()
    cctx15.provider = _R18Provider()
    cp15 = chishi.AemeathChishiPlugin(
        cctx15, _auto_cfg(feedProbability=1.0, feedForceTags="美少女,抽象", feedSkipTags="R18"))
    ev = AstrMessageEvent("", group_id="915")
    ev.message_obj = _RawObj(fwd_segs)
    out = await drain(cp15.on_message(ev))
    flat = [seg for x in out for seg in (x if isinstance(x, list) else [x])]
    check("chishi: R18不搬（评语照发）",
          len(cctx15.sent) == 0 and any("不太好" in getattr(s, "text", "") for s in flat))

    # 擦边内容：会被搬运（与 R18 互斥）
    class _EdgeProvider:
        async def text_chat(self, **kwargs):
            class R:
                completion_text = "TAG: 擦边\nLV: 2\n有点东西"
            return R()

    cctx16 = Context()
    cctx16.provider = _EdgeProvider()
    cp16 = chishi.AemeathChishiPlugin(
        cctx16, _auto_cfg(feedProbability=0.0, feedMinLevel=5, feedBlockList="916",
                          feedForceTags="美少女,抽象,擦边"))
    ev = AstrMessageEvent("", group_id="916")
    ev.message_obj = _RawObj(fwd_segs)
    await drain(cp16.on_message(ev))
    check("chishi: 擦边标签强制搬", len(cctx16.sent) == 1)

    # 万一模型同时给了擦边和 R18，R18 优先 → 不搬
    class _BothProvider:
        async def text_chat(self, **kwargs):
            class R:
                completion_text = "TAG: 擦边,R18\nLV: 5\n这过线了"
            return R()

    cctx17 = Context()
    cctx17.provider = _BothProvider()
    cp17 = chishi.AemeathChishiPlugin(
        cctx17, _auto_cfg(feedProbability=1.0, feedForceTags="美少女,抽象,擦边",
                          feedSkipTags="R18"))
    ev = AstrMessageEvent("", group_id="917")
    ev.message_obj = _RawObj(fwd_segs)
    await drain(cp17.on_message(ev))
    check("chishi: R18优先于擦边", len(cctx17.sent) == 0)

    # 跨群内容去重：同一份内容在 A、B 两个群各发一次，只搬一次
    class _ForceProvider:
        async def text_chat(self, **kwargs):
            class R:
                completion_text = "TAG: 美少女\nLV: 2\n这东西"
            return R()

    cctx_dup = Context()
    cctx_dup.provider = _ForceProvider()
    cp_dup = chishi.AemeathChishiPlugin(
        cctx_dup, _auto_cfg(feedProbability=0.0, feedForceTags="美少女"))
    for g in ("921", "922"):
        ev = AstrMessageEvent("", group_id=g)
        ev.message_obj = _RawObj(fwd_segs)
        await drain(cp_dup.on_message(ev))
    check("chishi: 跨群相同内容只搬一次", len(cctx_dup.sent) == 1)

    # 多人引用同一条转发：指纹用被引用消息 id，只搬一次
    check("chishi: 引用指纹一致",
          cp_dup._fingerprint({"text": "甲", "fp_seed": "quote:100"})
          == cp_dup._fingerprint({"text": "乙", "fp_seed": "quote:100"}))

    class _QuoteBot:
        async def call_action(self, action, **kw):
            if action == "get_msg":
                return {"message": [{"type": "forward", "data": {"id": "f9"}}]}
            if action == "get_forward_msg":
                return {"messages": [{"sender": {"nickname": "李某"}, "message": [
                    {"type": "text", "data": {"text": "同一条转发"}}]}]}
            return {}

    cctx_q = Context()

    class _AbsQuoteProvider:
        async def text_chat(self, **kwargs):
            class R:
                completion_text = "TAG: 抽象\nLV: 2\n离谱"
            return R()

    cctx_q.provider = _AbsQuoteProvider()
    cp_q = chishi.AemeathChishiPlugin(
        cctx_q, _auto_cfg(feedProbability=0.0, feedForceTags="抽象"))
    for reply_text in ("一般", "别搞"):
        ev = AstrMessageEvent(reply_text, group_id="941")
        ev.bot = _QuoteBot()
        ev.message_obj = _RawObj([{"type": "reply", "data": {"id": "m100"}},
                                  {"type": "text", "data": {"text": reply_text}}])
        await drain(cp_q.on_message(ev))
    check("chishi: 多人引用同一转发只搬一次", len(cctx_q.sent) == 1)

    # 原转发 + 引用它的回复 → 共用「转发段 id」种子，只搬一次
    cctx_fq = Context()
    cctx_fq.provider = _AbsQuoteProvider()
    cp_fq = chishi.AemeathChishiPlugin(
        cctx_fq, _auto_cfg(feedProbability=0.0, feedForceTags="抽象"))
    # 1) 原转发（内联 content，与 _QuoteBot 返回的转发 id 相同）
    ev = AstrMessageEvent("", group_id="942")
    ev.bot = _QuoteBot()
    ev.message_obj = _RawObj([{"type": "forward", "data": {"id": "f9", "content": [
        {"type": "node", "data": {"nickname": "李某", "content": [
            {"type": "text", "data": {"text": "同一条转发"}}]}}]}}])
    await drain(cp_fq.on_message(ev))
    # 2) 引用同一条转发回复
    ev = AstrMessageEvent("我要把这个喂给我家鱼", group_id="942")
    ev.bot = _QuoteBot()
    ev.message_obj = _RawObj([{"type": "reply", "data": {"id": "m200"}},
                              {"type": "text", "data": {"text": "我要把这个喂给我家鱼"}}])
    await drain(cp_fq.on_message(ev))
    check("chishi: 原转发与引用共指纹", len(cctx_fq.sent) == 1)

    # 不同内容照常各搬一次
    diff_segs = [{"type": "forward", "data": {"id": "fd", "content": [
        {"type": "node", "data": {"nickname": "乙", "content": [
            {"type": "text", "data": {"text": "另一条完全不同的史"}}]}}]}}]
    cctx_diff = Context()
    cctx_diff.provider = _ForceProvider()
    cp_diff = chishi.AemeathChishiPlugin(
        cctx_diff, _auto_cfg(feedProbability=0.0, feedForceTags="美少女"))
    for g, segs in (("924", fwd_segs), ("925", diff_segs)):
        ev = AstrMessageEvent("", group_id=g)
        ev.message_obj = _RawObj(segs)
        await drain(cp_diff.on_message(ev))
    check("chishi: 不同内容照常搬", len(cctx_diff.sent) == 2)

    # 卡片搬运附带原始链接
    cctx_card = Context()
    cctx_card.provider = _ShiProvider()
    cp_card = chishi.AemeathChishiPlugin(cctx_card, _auto_cfg(feedProbability=0.0))
    ev = AstrMessageEvent("", group_id="901")
    ev.message_obj = _RawObj(card_segs)
    await drain(cp_card.on_message(ev))
    ev = AstrMessageEvent("搬过去", group_id="901")
    ev.message_obj = _RawObj([])
    await drain(cp_card.on_message(ev))
    sent_card = _chain_text(cctx_card.sent[0][1].chain) if cctx_card.sent else ""
    check("chishi: 搬史附带卡片链接", "bilibili.com/video/BV1xx411c7mD" in sent_card)

    # B站内容只搬「抽象」标签
    class _ProviderTag:
        def __init__(self, text):
            self.text = text

        async def text_chat(self, **kwargs):
            class R:
                completion_text = self.text
            return R()

    bili_segs = [{"type": "json", "data": {"data": json.dumps(
        {"meta": {"news": {"title": "B站视频标题", "desc": "简介",
                           "jumpUrl": "https://www.bilibili.com/video/BV1yy411c7mE"}}},
         ensure_ascii=False)}}]

    # ① 美少女标签的B站卡片 → 不搬
    cctx_b1 = Context()
    cctx_b1.provider = _ProviderTag("TAG: 美少女\nLV: 5\n好看")
    cp_b1 = chishi.AemeathChishiPlugin(
        cctx_b1, _auto_cfg(feedProbability=1.0, feedForceTags="美少女,抽象,擦边"))
    ev = AstrMessageEvent("", group_id="931")
    ev.message_obj = _RawObj(bili_segs)
    await drain(cp_b1.on_message(ev))
    check("chishi: B站美少女不搬", len(cctx_b1.sent) == 0)

    # ② 抽象标签的B站卡片：默认也不自动搬（只手动）
    cctx_b2 = Context()
    cctx_b2.provider = _ProviderTag("TAG: 抽象\nLV: 1\n什么抽象东西")
    cp_b2 = chishi.AemeathChishiPlugin(
        cctx_b2, _auto_cfg(feedProbability=0.0, feedForceTags="美少女,抽象,擦边"))
    ev = AstrMessageEvent("", group_id="932")
    ev.message_obj = _RawObj(bili_segs)
    await drain(cp_b2.on_message(ev))
    check("chishi: B站抽象默认不自动搬", len(cctx_b2.sent) == 0)

    # ③ 其他标签 + LV5 + 概率1 → 也不搬
    cctx_b3 = Context()
    cctx_b3.provider = _ProviderTag("TAG: 其他\nLV: 5\n还行")
    cp_b3 = chishi.AemeathChishiPlugin(cctx_b3, _auto_cfg(feedProbability=1.0))
    ev = AstrMessageEvent("", group_id="933")
    ev.message_obj = _RawObj(bili_segs)
    await drain(cp_b3.on_message(ev))
    check("chishi: B站其他标签不搬", len(cctx_b3.sent) == 0)

    # ④ 手动搬 B站卡片仍然可以（不受自动搬限制）
    cctx_b4 = Context()
    cctx_b4.provider = _ProviderTag("TAG: 抽象\nLV: 3\n抽象")
    cp_b4 = chishi.AemeathChishiPlugin(cctx_b4, _auto_cfg(feedProbability=0.0))
    ev = AstrMessageEvent("", group_id="934")
    ev.message_obj = _RawObj(bili_segs)
    await drain(cp_b4.on_message(ev))
    ev = AstrMessageEvent("搬过去", group_id="934")
    ev.message_obj = _RawObj([])
    await drain(cp_b4.on_message(ev))
    check("chishi: B站卡片可手动搬", len(cctx_b4.sent) == 1)

    # ⑤ 打开 biliAutoFeed 后恢复自动搬
    cctx_b5 = Context()
    cctx_b5.provider = _ProviderTag("TAG: 抽象\nLV: 1\n抽象")
    cp_b5 = chishi.AemeathChishiPlugin(
        cctx_b5, _auto_cfg(feedProbability=0.0, feedForceTags="美少女,抽象,擦边", biliAutoFeed=True))
    ev = AstrMessageEvent("", group_id="935")
    ev.message_obj = _RawObj(bili_segs)
    await drain(cp_b5.on_message(ev))
    check("chishi: 开启 biliAutoFeed 后自动搬", len(cctx_b5.sent) == 1)

    # 指定群只搬「抽象」：900000009 这类群的来源内容，非抽象标签不搬
    cctx_g1 = Context()
    cctx_g1.provider = _ProviderTag("TAG: 美少女\nLV: 5\n好看")
    cp_g1 = chishi.AemeathChishiPlugin(
        cctx_g1, _auto_cfg(feedProbability=1.0, feedForceTags="美少女,抽象,擦边",
                           abstractOnlyGroups="900000009"))
    ev = AstrMessageEvent("", group_id="900000009")
    ev.message_obj = _RawObj(fwd_segs)
    await drain(cp_g1.on_message(ev))
    check("chishi: 抽象限定群美少女不搬", len(cctx_g1.sent) == 0)

    cctx_g2 = Context()
    cctx_g2.provider = _ProviderTag("TAG: 抽象\nLV: 1\n离谱")
    cp_g2 = chishi.AemeathChishiPlugin(
        cctx_g2, _auto_cfg(feedProbability=0.0, feedForceTags="美少女,抽象,擦边",
                           abstractOnlyGroups="900000009"))
    ev = AstrMessageEvent("", group_id="900000009")
    ev.message_obj = _RawObj(fwd_segs)
    await drain(cp_g2.on_message(ev))
    check("chishi: 抽象限定群抽象搬", len(cctx_g2.sent) == 1)

    # @机器人 + 引用转发 + 搬过去
    class _FakeBot:
        async def call_action(self, action, **kw):
            if action == "get_msg":
                return {"message": [{"type": "forward", "data": {"id": "f9"}}]}
            if action == "get_forward_msg":
                return {"messages": [{"sender": {"nickname": "李某"}, "message": [
                    {"type": "text", "data": {"text": "这条转发很典"}}]}]}
            return {}

    cctx5 = Context()
    cctx5.provider = _ShiProvider()
    cp5 = chishi.AemeathChishiPlugin(cctx5, _auto_cfg(feedProbability=0))
    ev = AstrMessageEvent("@小爱 搬到900000011", group_id="905")
    ev.is_at_or_wake_command = True
    ev.bot = _FakeBot()
    ev.message_obj = _RawObj([{"type": "reply", "data": {"id": "m1"}},
                              {"type": "text", "data": {"text": "搬到900000011"}}])
    out = await drain(cp5.on_message(ev))
    flat = [seg for x in out for seg in (x if isinstance(x, list) else [x])]
    moved = _chain_text(cctx5.sent[0][1].chain) if cctx5.sent else ""
    check("chishi: @机器人搬引用转发",
          any("搬过去了" in getattr(s, "text", "") for s in flat)
          and "这条转发很典" in moved
          and "搬到900000011" not in moved)

    # 转发里的图片要随节点一起搬（不是 [图片] 占位）
    img_fwd = [{"type": "forward", "data": {"id": "f7", "content": [
        {"type": "node", "data": {"nickname": "车神", "content": [
            {"type": "text", "data": {"text": "看看这个"}},
            {"type": "image", "data": {"url": "https://example.com/a.png"}}]}}]}}]
    cctx6 = Context()
    cctx6.provider = _ShiProvider()
    cp6 = chishi.AemeathChishiPlugin(cctx6, _auto_cfg(feedProbability=0))
    ev = AstrMessageEvent("", group_id="906")
    ev.message_obj = _RawObj(img_fwd)
    await drain(cp6.on_message(ev))
    ev = AstrMessageEvent("搬过去", group_id="906")
    ev.message_obj = _RawObj([])
    await drain(cp6.on_message(ev))
    chain6 = cctx6.sent[0][1].chain if cctx6.sent else []
    node6 = chain6[0] if chain6 and isinstance(chain6[0], Nodes) else None
    has_img = bool(node6 and any(isinstance(c, Image) for n in node6.nodes for c in n.content))
    check("chishi: 转发图片随节点搬运", has_img)

    # 转发里的视频也要随节点带过去
    vid_fwd = [{"type": "forward", "data": {"id": "fv", "content": [
        {"type": "node", "data": {"nickname": "凝目", "content": [
            {"type": "text", "data": {"text": "战歌来了"}},
            {"type": "video", "data": {"url": "https://example.com/a.mp4"}}]}}]}}]
    cctx_v = Context()
    cctx_v.provider = _ShiProvider()
    cp_v = chishi.AemeathChishiPlugin(cctx_v, _auto_cfg(feedProbability=0))
    ev = AstrMessageEvent("", group_id="951")
    ev.message_obj = _RawObj(vid_fwd)
    await drain(cp_v.on_message(ev))
    ev = AstrMessageEvent("搬过去", group_id="951")
    ev.message_obj = _RawObj([])
    await drain(cp_v.on_message(ev))
    chain_v = cctx_v.sent[0][1].chain if cctx_v.sent else []
    top_v = chain_v[0] if chain_v and isinstance(chain_v[0], Nodes) else None
    has_vid = bool(top_v and any(isinstance(c, Video) for n in top_v.nodes for c in n.content))
    check("chishi: 转发视频随节点搬运", has_vid)

    # 纯图消息重传经常 400：有数字 mid 时直接走原生转发
    class _ImgBot:
        def __init__(self):
            self.calls = []

        async def call_action(self, action, **kw):
            self.calls.append((action, kw))
            return {}

    cctx_img = Context()
    cctx_img.provider = _ShiProvider()
    cp_img = chishi.AemeathChishiPlugin(cctx_img, _auto_cfg(feedProbability=0.0))
    ev = AstrMessageEvent("", group_id="952")
    ev.bot = _ImgBot()
    ok_img = await cp_img.move(ev, "group", "900000011", {
        "kind": "image", "text": "", "nodes": [], "urls": [],
        "images": ["https://gchat.qpic.cn/x.jpg"], "mid": "987654321",
    })
    check("chishi: 纯图走原生转发",
          ok_img and any(a == "forward_group_single_msg" for a, _ in ev.bot.calls)
          and len(cctx_img.sent) == 0)

    # 图片和指令在同一条消息里时，不能原生转发（否则把「这条也搬」也搬过去）
    ev2 = AstrMessageEvent("", group_id="953")
    ev2.bot = _ImgBot()
    ev2.message_obj = _RawObj([{"type": "image", "data": {"file": "abc.jpg", "url": "https://gchat.qpic.cn/y.jpg"}}])
    ev2.message_obj.message_id = "5550001"
    ok_cmd = await cp_img.move(ev2, "group", "900000011", {
        "kind": "image", "text": "", "nodes": [], "urls": [],
        "images": ["https://gchat.qpic.cn/y.jpg"], "mid": "5550001",
        "image_files": ["abc.jpg"],
    })
    img_sent = [c for m in cctx_img.sent for c in getattr(m[1], "chain", []) if isinstance(c, Image)]
    check("chishi: 指令消息不原生转发、图片按文件名直传",
          ok_cmd and not ev2.bot.calls and len(img_sent) == 1
          and getattr(img_sent[0], "file", "") == "abc.jpg")

    # 引用别人消息时，直接把被引用的原消息交给协议端转发（图片链接会过期）
    ev3 = AstrMessageEvent("", group_id="955")
    ev3.bot = _ImgBot()
    ok_q = await cp_img.move(ev3, "group", "900000011", {
        "kind": "forward", "text": "x",
        "nodes": [{"parts": [("text", "x")], "name": "K", "uin": "1"}],
        "images": [], "urls": [], "mid": "q1", "quote_mid": "8888",
    })
    quoted = [kw for act, kw in ev3.bot.calls if act == "forward_group_single_msg"]
    check("chishi: 引用时原生转发原消息", ok_q and quoted and quoted[0].get("message_id") == 8888)

    # 二级转发（套娃）：整段只有内层转发时嵌套成卡片
    class _NestedBot:
        async def call_action(self, action, **kw):
            if action == "get_forward_msg":
                if kw.get("id") == "f2":
                    return {"messages": [{"sender": {"nickname": "甲", "user_id": 1},
                                          "message": [{"type": "forward", "data": {"id": "sub2"}}]}]}
                if kw.get("id") == "sub2":
                    return {"messages": [{"sender": {"nickname": "乙", "user_id": 2},
                                          "message": [{"type": "text", "data": {"text": "二级内容"}}]}]}
            return {}

    cctx7 = Context()
    cctx7.provider = _ShiProvider()
    cp7 = chishi.AemeathChishiPlugin(cctx7, _auto_cfg(feedProbability=0))
    ev = AstrMessageEvent("", group_id="907")
    ev.bot = _NestedBot()
    ev.message_obj = _RawObj([{"type": "forward", "data": {"id": "f2"}}])
    await drain(cp7.on_message(ev))
    ev = AstrMessageEvent("搬过去", group_id="907")
    ev.message_obj = _RawObj([])
    await drain(cp7.on_message(ev))
    chain7 = cctx7.sent[0][1].chain if cctx7.sent else []
    top7 = chain7[0] if chain7 and isinstance(chain7[0], Nodes) else None
    nested = top7.nodes[0].content[0] if top7 and top7.nodes else None
    check("chishi: 二级转发嵌套搬运",
          isinstance(nested, Node) and "二级内容" in _chain_text([nested]))

    # 二级转发内联 content（NapCat 对二级 id 返回 1200，只能吃内联）
    class _InlineBot:
        async def call_action(self, action, **kw):
            if action == "get_forward_msg" and kw.get("id") == "f4":
                return {"messages": [{"sender": {"nickname": "甲", "user_id": 1},
                                      "message": [{"type": "forward", "data": {
                                          "id": "sub9",
                                          "content": [{"type": "node", "data": {
                                              "nickname": "乙", "user_id": 2,
                                              "content": [{"type": "text",
                                                           "data": {"text": "内联二级"}}]}}]}}]}]}
            raise RuntimeError("内层 id 不应该被拉取")

    cctx8 = Context()
    cctx8.provider = _ShiProvider()
    cp8 = chishi.AemeathChishiPlugin(cctx8, _auto_cfg(feedProbability=0))
    ev = AstrMessageEvent("", group_id="908")
    ev.bot = _InlineBot()
    ev.message_obj = _RawObj([{"type": "forward", "data": {"id": "f4"}}])
    await drain(cp8.on_message(ev))
    ev = AstrMessageEvent("搬过去", group_id="908")
    ev.message_obj = _RawObj([])
    await drain(cp8.on_message(ev))
    chain8 = cctx8.sent[0][1].chain if cctx8.sent else []
    top8 = chain8[0] if chain8 and isinstance(chain8[0], Nodes) else None
    nested8 = top8.nodes[0].content[0] if top8 and top8.nodes else None
    check("chishi: 二级转发内联搬运",
          isinstance(nested8, Node) and "内联二级" in _chain_text([nested8]))

    check("chishi: 搬给群号识别", chishi.match_move("搬给900000001") == ("group", "900000001"))

    # 内层 content 是普通消息段数组（不是 node 数组）
    class _SegArrayBot:
        async def call_action(self, action, **kw):
            if action == "get_forward_msg" and kw.get("id") == "f5":
                return {"messages": [{"sender": {"nickname": "甲", "user_id": 1},
                                      "message": [{"type": "forward", "data": {
                                          "id": "sub10",
                                          "content": [{"type": "text",
                                                       "data": {"text": "段数组二级"}}]}}]}]}
            raise RuntimeError("内层 id 不应该被拉取")

    cctx9 = Context()
    cctx9.provider = _ShiProvider()
    cp9 = chishi.AemeathChishiPlugin(cctx9, _auto_cfg(feedProbability=0))
    ev = AstrMessageEvent("", group_id="909")
    ev.bot = _SegArrayBot()
    ev.message_obj = _RawObj([{"type": "forward", "data": {"id": "f5"}}])
    await drain(cp9.on_message(ev))
    ev = AstrMessageEvent("搬过去", group_id="909")
    ev.message_obj = _RawObj([])
    await drain(cp9.on_message(ev))
    chain9 = cctx9.sent[0][1].chain if cctx9.sent else []
    top9 = chain9[0] if chain9 and isinstance(chain9[0], Nodes) else None
    nested9 = top9.nodes[0].content[0] if top9 and top9.nodes else None
    check("chishi: 二级转发段数组搬运",
          isinstance(nested9, Node) and "段数组二级" in _chain_text([nested9]))

    # 内层 content 是消息对象数组（sender + message，实测 NapCat 即此形态）
    class _MsgObjBot:
        async def call_action(self, action, **kw):
            if action == "get_forward_msg" and kw.get("id") == "f6":
                return {"messages": [{"sender": {"nickname": "甲", "user_id": 1},
                                      "message": [{"type": "forward", "data": {
                                          "id": "sub11",
                                          "content": [{"sender": {"nickname": "丙", "user_id": 3},
                                                       "message": [{"type": "text",
                                                                    "data": {"text": "消息对象二级"}}]}]}}]}]}
            raise RuntimeError("内层 id 不应该被拉取")

    cctx10 = Context()
    cctx10.provider = _ShiProvider()
    cp10 = chishi.AemeathChishiPlugin(cctx10, _auto_cfg(feedProbability=0))
    ev = AstrMessageEvent("", group_id="910")
    ev.bot = _MsgObjBot()
    ev.message_obj = _RawObj([{"type": "forward", "data": {"id": "f6"}}])
    await drain(cp10.on_message(ev))
    ev = AstrMessageEvent("搬过去", group_id="910")
    ev.message_obj = _RawObj([])
    await drain(cp10.on_message(ev))
    chain10 = cctx10.sent[0][1].chain if cctx10.sent else []
    top10 = chain10[0] if chain10 and isinstance(chain10[0], Nodes) else None
    nested10 = top10.nodes[0].content[0] if top10 and top10.nodes else None
    check("chishi: 二级转发消息对象搬运",
          isinstance(nested10, Node) and "消息对象二级" in _chain_text([nested10]))

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
        "verify_reply": False,
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
        "verify_reply": False,
    }))
    ev = AstrMessageEvent("嗯嗯", group_id="222")
    out = await drain(gp2.maybe_join(ev))
    check("group_chat: [skip] 不发言", not out)

    # 冷却
    gcfg3 = AstrBotConfig({
        "enabled": True, "probability": 1.0, "cooldown": 60,
        "context_size": 5, "min_len": 2, "max_chars": 80, "persona": "",
        "verify_reply": False,
    })
    gctx3 = Context()
    gctx3.provider = _ChatProvider()
    gp3 = group_chat.AemeathGroupChatPlugin(gctx3, gcfg3)
    await drain(gp3.maybe_join(AstrMessageEvent("第一句", group_id="333")))
    out = await drain(gp3.maybe_join(AstrMessageEvent("第二句", group_id="333")))
    check("group_chat: 冷却内不插话", not out)

    # 引用校验：回复与引用消息对得上才发
    class _JudgeProvider:
        def __init__(self, verdict):
            self.verdict = verdict
            self.calls = []

        async def text_chat(self, **kw):
            self.calls.append(kw)

            class R:
                completion_text = "今天好热啊，受不了"

            r = R()
            if "判卷人" in str(kw.get("system_prompt", "")):
                r.completion_text = self.verdict
            return r

    gctx5 = Context()
    judge_yes = _JudgeProvider("YES")
    gctx5.provider = judge_yes
    gp5 = group_chat.AemeathGroupChatPlugin(gctx5, AstrBotConfig({
        "enabled": True, "probability": 1.0, "cooldown": 0,
        "context_size": 5, "min_len": 2, "max_chars": 80, "persona": "",
        "verify_reply": True, "verify_timeout": 5,
    }))
    ev = AstrMessageEvent("今天好热啊", group_id="555")
    ev.message_obj = _Obj("mid-555")
    out = await drain(gp5.maybe_join(ev))
    check("group_chat: 引用校验通过才插话", bool(out) and ev.stopped)
    check("group_chat: 校验请求带被引用消息",
          len(judge_yes.calls) == 2 and "今天好热啊" in judge_yes.calls[1]["prompt"])

    gctx6 = Context()
    gctx6.provider = _JudgeProvider("NO")
    gp6 = group_chat.AemeathGroupChatPlugin(gctx6, AstrBotConfig({
        "enabled": True, "probability": 1.0, "cooldown": 0,
        "context_size": 5, "min_len": 2, "max_chars": 80, "persona": "",
        "verify_reply": True, "verify_timeout": 5,
    }))
    ev = AstrMessageEvent("今天好热啊", group_id="666")
    out = await drain(gp6.maybe_join(ev))
    check("group_chat: 引用对不上不插话", not out and not ev.stopped)

    # ---------- poke ----------
    poke = load_plugin("astrbot_plugin_aemeath_poke")
    pp = poke.AemeathPokePlugin(Context(), AstrBotConfig({"enabled": True, "cooldown": 0, "replies": []}))
    ev = AstrMessageEvent("")
    ev._messages = [Poke(id="<OLD_BOT_QQ>")]  # stub 的 self_id
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
        "verify_reply": False,
    })
    gp4 = group_chat.AemeathGroupChatPlugin(gctx4, gcfg4)
    await drain(gp4.maybe_join(AstrMessageEvent("旧话题甲", group_id="444")))
    gp4._history["444"] = deque(
        [(time.time() - 7200, ln) for _, ln in gp4._history["444"]], maxlen=15
    )
    await drain(gp4.maybe_join(AstrMessageEvent("新话题乙", group_id="444")))
    last_prompt = cap.prompts[-1]
    check("group_chat: 冷群后清空旧上下文", "旧话题甲" not in last_prompt and "新话题乙" in last_prompt)

    # ---------- memory 长期记忆 ----------
    mem_dir = TMP / "memory"
    mem_dir.mkdir(parents=True, exist_ok=True)
    os.environ["AEMEATH_MEMORY_DIR"] = str(mem_dir)
    memory = load_plugin("astrbot_plugin_aemeath_memory")

    check("memory: 脱敏手机号/密钥",
          "13000000000" not in memory.redact("我电话 13000000000")
          and "sk-" not in memory.redact("key sk-abcdefghijklmnopqrst"))

    cfg_mem = {
        "recentDays": 7, "forgetAfterDays": 60, "maxRecords": 500, "injectMaxChars": 120,
        "autoExtract": True, "extractEvery": 3, "extractCooldown": 0,
    }
    check("memory: 近事保持清楚", memory.clarity_for(2, 40, False, cfg_mem) in ("vivid", "clear"))
    check("memory: 久远低分遗忘", memory.clarity_for(200, 30, False, cfg_mem) == "forgotten")
    check("memory: 主人锚点永不模糊", memory.clarity_for(999, 90, True, cfg_mem) == "vivid")

    parsed = memory.parse_extracted(
        '好的：```json\n[{"text":"阿伟在准备考研","importance":72,"who":"阿伟"}]\n```')
    check("memory: 抽取结果容错解析", len(parsed) == 1 and parsed[0]["importance"] == 72)

    mctx = Context()
    mctx.provider = _ShiProvider()
    mp = memory.AemeathMemoryPlugin(mctx, AstrBotConfig(dict(cfg_mem)))
    mp._records = []
    mp._add("阿伟在准备考研，压力很大", "group:555", 72, False, "auto", "阿伟")
    mp._add("主人喜欢喝冰美式", "global", 80, True, "manual")
    dup = mp._add("阿伟在准备考研，压力很大", "group:555", 72)
    check("memory: 重复内容不重复记", dup is None and len(mp._records) == 2)

    block = mp.render_block("group:555")
    check("memory: 注入包含本会话与全局记忆",
          "考研" in block and "冰美式" in block and "<memory>" in block)
    check("memory: 别的会话看不到群记忆", mp.render_block("group:999").find("考研") == -1)
    check("memory: 注入有字数上限", len(mp.render_block("group:555")) <= 400)

    class _MemResp:
        def __init__(self, text):
            self.completion_text = text

    class _MemProvider:
        async def text_chat(self, **kw):
            return _MemResp('[{"text":"群友小刘养了只猫叫团子","importance":55}]')

    mctx2 = Context()
    mctx2.provider = _MemProvider()
    mp2 = memory.AemeathMemoryPlugin(mctx2, AstrBotConfig(dict(cfg_mem)))
    mp2._records = []
    await mp2._extract("group:666", "小刘: 我家猫叫团子")
    check("memory: 自动抽取写入记忆",
          len(mp2._records) == 1 and "团子" in mp2._records[0]["text"])

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
