"""测试呜哇小站远程表情接口（服务器上运行）"""
import asyncio
import os
import sys

sys.path.insert(0, "/opt/qqbot")

import wuwa_emoji  # noqa: E402


async def main():
    print("API:", os.getenv("WUWA_API_BASE", wuwa_emoji.DEFAULT_API_BASE))
    print("角色:", os.getenv("WUWA_CHARACTER", "爱弥斯"))
    print("Token:", "已配置" if os.getenv("WUWA_TOKEN") else "未配置（匿名）")
    for i in range(3):
        item = await wuwa_emoji.random_emoji()
        if not item:
            print(f"第 {i + 1} 次: 失败")
            continue
        size = os.path.getsize(item["path"]) if os.path.exists(item["path"]) else 0
        char = item["keywords"][-1] if item.get("keywords") else "?"
        print(f"第 {i + 1} 次: [{char}] {item['name']} {size}B -> {item['path']}")


if __name__ == "__main__":
    asyncio.run(main())
