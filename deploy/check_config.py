"""打印运行时配置（用于确认环境变量生效）"""
import sys

sys.path.insert(0, "/opt/qqbot")

import config  # noqa: E402

print("插件/功能开关")
print("  GROUP_CHAT_ENABLED   =", config.GROUP_CHAT_ENABLED)
print("  GROUP_CHAT_PROBABILITY =", config.GROUP_CHAT_PROBABILITY)
print("  GROUP_CHAT_COOLDOWN  =", config.GROUP_CHAT_COOLDOWN)
print("  GROUP_CHAT_CONTEXT   =", config.GROUP_CHAT_CONTEXT)
print("  GROUP_WHITELIST      =", sorted(config.GROUP_WHITELIST))
print("  ADMIN_ID             =", config.ADMIN_ID)
print("  WUWA 角色            =", __import__("os").getenv("WUWA_CHARACTER", "任意"))
