"""爱弥斯 · 指令总览

`/帮助`（别名 help / 菜单）发送全部功能与命令，按功能分组，方便群里直接查。
"""
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

HELP_TEXT = """📖 指令一览

【聊天】
· 直接说话 / @我 提问
· /水群 on|off|prob —— 群聊插话开关

【鸣潮 Wiki】
· /wiki 角色名 —— 立绘 + 资料 + 养成材料 + 武器推荐
· /wiki 武器名 —— 武器面板 + 突破材料
· /材料 角色名 —— 库街区突破材料合计
· /攻略 角色名 —— 武器 / 声骸 / 队伍推荐
· /配队 角色名 —— 配队引擎
· /kuro 关键词 —— 库街区全站搜索

【吃史仙人】
· 转发 / 分享卡片 / 截图 —— 自动读内容评一句
· 搬过去 / 转过去 / 喂一下 —— 搬到搬史群
· 发我 —— 搬到我私聊
· 搬到123456789 / 搬给123456789 —— 搬到指定群
· 发给123456789 —— 搬到指定人
· 「这条也搬」/「这个也搬」/「再搬一次」—— 搬最近一条

【点歌】
· 演奏 xxx / 来一首 xxx

【日记与总结】（管理员）
· /日记 [日期] —— 查看日记
· /写日记 —— 立即写一篇
· 每天 21:00 群聊总结、23:30 日记

【AstrBot 内置】
· /new 新会话 · /reset 重置上下文 · /name 改名
· /sid 会话 ID · /provider 模型 · /stats 统计 · /stop 停止

发 /帮助 随时查看本列表"""


class AemeathHelpPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        logger.info("[aemeath_help] 指令总览插件已加载")

    @filter.command("帮助", alias={"菜单"})
    async def help_cmd(self, event: AstrMessageEvent):
        if not self.config.get("enabled", True):
            return
        yield event.plain_result(HELP_TEXT)
        event.stop_event()
