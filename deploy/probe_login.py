"""查询 NapCat 当前登录账号（OneBot v11 get_login_info）

用法（服务器上）：
    sudo /opt/qqbot/.venv/bin/python /opt/qqbot/deploy/probe_login.py
"""
import asyncio
import json
import sys

import aiohttp


async def main(url: str) -> int:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(url, timeout=8) as ws:
                await ws.send_str(json.dumps({"action": "get_login_info", "echo": "probe"}))
                while True:
                    msg = await asyncio.wait_for(ws.receive(), timeout=8)
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    data = json.loads(msg.data)
                    if data.get("echo") != "probe":
                        continue
                    if data.get("status") == "ok":
                        info = data.get("data") or {}
                        print(f"已登录 QQ: {info.get('user_id')}  昵称: {info.get('nickname')}")
                    else:
                        print(f"未登录或查询失败: {data.get('msg') or data.get('retcode')}")
                    return 0
    except Exception as exc:  # noqa: BLE001
        print(f"连接 NapCat 失败: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:3001")))
