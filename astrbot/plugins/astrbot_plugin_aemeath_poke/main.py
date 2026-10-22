"""
爱弥斯 · 戳一戳回应插件

QQ 里戳机器人（群/私聊）时，回一句俏皮话。
适配器会把 poke 通知转成带 Poke 组件的消息事件，这里检测目标是否为机器人自身。
"""
import random
import time

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

DEFAULT_REPLIES = [
    "诶？戳我干嘛呀～",
    "收到收到！小爱在线～",
    "别戳啦别戳啦，痒痒的！",
    "戳我有事嘛？说呀说呀～",
    "哼，再戳要收费了哦（不是）",
    "在呢在呢，不用戳也在！",
]


class AemeathPokePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._last: dict[str, float] = {}
        logger.info("[aemeath_poke] 戳一戳回应插件已加载")

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_poke(self, event: AstrMessageEvent):
        if not self.config.get("enabled", True):
            return

        poke = None
        for comp in event.get_messages():
            if isinstance(comp, Comp.Poke):
                poke = comp
                break
        if poke is None:
            return

        target = str(getattr(poke, "id", None) or getattr(poke, "qq", None) or "")
        if target != str(event.get_self_id()):
            return  # 戳的不是机器人
        sender = str(event.get_sender_id())
        if not sender or sender == str(event.get_self_id()):
            return

        now = time.time()
        cooldown = int(self.config.get("cooldown", 10))
        if now - self._last.get(sender, 0.0) < cooldown:
            return
        self._last[sender] = now

        replies = self.config.get("replies") or DEFAULT_REPLIES
        line = random.choice(replies) if replies else random.choice(DEFAULT_REPLIES)
        logger.info("[aemeath_poke] %s 戳了机器人，回应: %s", sender, line)
        yield event.plain_result(line)
        event.stop_event()
