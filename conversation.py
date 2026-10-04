"""
会话上下文管理：为每个群维护最近 N 条对话历史 + 每日消息记录
"""
from collections import deque
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class Conversation:
    history: deque = field(default_factory=lambda: deque(maxlen=20))
    last_reply_time: float = 0.0


@dataclass
class DailyRecord:
    """每日消息记录"""
    date: str = ""  # YYYY-MM-DD
    messages: list[dict] = field(default_factory=list)  # [{sender, nickname, content, time}]


class ConversationManager:
    def __init__(self, max_history: int):
        self._max_history = max_history
        self._sessions: dict[int, Conversation] = {}
        self._daily_records: dict[int, DailyRecord] = {}  # group_id -> 当日记录

    def _get(self, group_id: int) -> Conversation:
        if group_id not in self._sessions:
            self._sessions[group_id] = Conversation(
                history=deque(maxlen=self._max_history)
            )
        return self._sessions[group_id]

    def _get_daily(self, group_id: int) -> DailyRecord:
        today = datetime.now().strftime("%Y-%m-%d")
        if group_id not in self._daily_records:
            self._daily_records[group_id] = DailyRecord(date=today)
        record = self._daily_records[group_id]
        # 如果日期变了，清空旧记录
        if record.date != today:
            record.date = today
            record.messages.clear()
        return record

    def add_user(self, group_id: int, content: str):
        self._get(group_id).history.append({"role": "user", "content": content})

    def add_assistant(self, group_id: int, content: str):
        self._get(group_id).history.append({"role": "assistant", "content": content})

    def record_message(self, group_id: int, sender_id: int, nickname: str, content: str):
        """记录一条群消息到当日记录（用于每日总结）"""
        record = self._get_daily(group_id)
        record.messages.append({
            "sender_id": sender_id,
            "nickname": nickname,
            "content": content,
            "time": datetime.now().strftime("%H:%M"),
        })

    def get_daily_messages(self, group_id: int) -> list[dict]:
        """获取某群当日的所有消息记录"""
        return self._get_daily(group_id).messages

    def get_yesterday_messages(self, group_id: int) -> list[dict]:
        """获取昨日消息（用于早上总结，因为8点总结的是前一天的内容）"""
        # 简化处理：返回当日记录（因为会在日期切换时清空）
        # 实际使用场景是：8点触发时，记录里还是昨天的数据（还没人发新消息）
        return self._get_daily(group_id).messages

    def clear_daily(self, group_id: int):
        """清空当日记录"""
        today = datetime.now().strftime("%Y-%m-%d")
        self._daily_records[group_id] = DailyRecord(date=today)

    def get_active_groups(self) -> list[int]:
        """获取当日有消息的群列表"""
        today = datetime.now().strftime("%Y-%m-%d")
        return [
            gid for gid, record in self._daily_records.items()
            if record.date == today and len(record.messages) > 0
        ]

    def get_messages(self, group_id: int, system_prompt: str) -> list[dict]:
        conv = self._get(group_id)
        return [{"role": "system", "content": system_prompt}] + list(conv.history)

    def clear(self, group_id: int):
        if group_id in self._sessions:
            self._sessions[group_id].history.clear()

    def can_reply(self, group_id: int, cooldown: int) -> bool:
        conv = self._get(group_id)
        now = time.time()
        if now - conv.last_reply_time < cooldown:
            return False
        conv.last_reply_time = now
        return True
