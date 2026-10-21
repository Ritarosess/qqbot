"""
群聊每日总结定时任务
每天 8:00 为前一天有消息的群生成 AI 总结
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Callable, Awaitable

logger = logging.getLogger("scheduler")


class DailySummaryScheduler:
    def __init__(self, summary_callback: Callable[[int, str], Awaitable[None]]):
        """
        summary_callback: 异步回调函数，接收 (group_id, summary_text) 并发送
        """
        self._callback = summary_callback
        self._task: asyncio.Task | None = None
        self._running = False

    def start(self):
        """启动调度器"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run())
        logger.info("每日总结调度器已启动，目标时间: 08:00")

    def stop(self):
        """停止调度器"""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info("每日总结调度器已停止")

    async def _run(self):
        while self._running:
            now = datetime.now()
            # 计算今天8点
            target = now.replace(hour=8, minute=0, second=0, microsecond=0)
            if now >= target:
                # 已经过了今天8点，等明天
                target += timedelta(days=1)

            wait_seconds = (target - now).total_seconds()
            logger.info(f"下次总结时间: {target.strftime('%Y-%m-%d %H:%M')}，等待 {int(wait_seconds)} 秒")

            await asyncio.sleep(wait_seconds)
            if not self._running:
                break

            await self._execute_summary()

    async def _execute_summary(self):
        """执行总结任务（由外部注入实际逻辑）"""
        # 这里由 bot.py 注入具体实现
        pass

    async def trigger_now(self, group_id: int, messages: list[str]):
        """立即触发一次总结（用于测试或手动触发）"""
        if not messages:
            return None
        # 由 bot.py 调用 AI 生成
        return None
