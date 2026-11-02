# -*- coding: utf-8 -*-
"""B站视频处理 worker（独立进程）

为什么要单独跑：
- 下载 80MB 视频 + ffmpeg 抽帧会吃内存，放主进程里会拖累机器人
- 独立进程可以设内存上限、可以超时直接 kill，挂了不影响 AstrBot

用法（父进程调用）：
    python worker.py '<json 参数>'

参数：
    {"vid": "BV1xx", "cookie": "SESSDATA=...", "maxHeight": 480,
     "maxSizeMB": 40, "frames": 12, "cols": 4, "workDir": "/tmp/xxx",
     "wantDialog": true}

输出（stdout 一行 JSON）：
    成功：{"ok": true, "info": {...}, "dialog": "...", "boards": ["/path/b0.jpg"]}
    失败：{"ok": false, "error": "..."}
"""
import json
import os
import resource
import sys
import traceback

MEM_LIMIT = 900 * 1024 * 1024  # 900MB，超过直接 MemoryError，不拖垮宿主机


def _limit_memory() -> None:
    try:
        resource.setrlimit(resource.RLIMIT_AS, (MEM_LIMIT, MEM_LIMIT))
    except Exception:  # noqa: BLE001
        pass


def run(req: dict) -> dict:
    import bili  # 同目录

    work = req.get("workDir") or "/tmp/aemeath_bili_worker"
    os.makedirs(work, exist_ok=True)
    client = bili.Bili(cookie=str(req.get("cookie") or ""))

    vid = client.resolve_input(str(req.get("vid") or ""))
    info = client.view(vid)
    max_dur = int(req.get("maxDuration", 1800))
    if info["duration"] > max_dur:
        return {"ok": False, "error": f"too_long",
                "duration": info["duration"], "info": info}

    dash = client.playurl_dash(info["bvid"], info["cid"])
    stream = bili.pick_stream(dash.get("video") or [], int(req.get("maxHeight", 480)))
    if not stream:
        return {"ok": False, "error": "no_stream"}

    dialog = ""
    if req.get("wantDialog", True):
        dialog = client.subtitles(info["bvid"], info["cid"])

    # 有字幕就按原密度；没字幕多抽几帧、放大每格，靠画面上的文案读内容
    if dialog:
        frames_n = int(req.get("frames", 12))
        cell_w = 480
    else:
        frames_n = int(req.get("textFrames", 24))
        cell_w = int(req.get("textCellWidth", 640))

    vpath = os.path.join(work, "v.m4s")
    client.download(stream["baseUrl"], vpath, int(req.get("maxSizeMB", 40)) * 1024 * 1024)
    try:
        times = bili.frame_times(info["duration"], frames_n)
        files = bili.extract_frames(vpath, times, work, cell_w)
        labels = [bili.fmt_duration(int(t)) for t in times][: len(files)]
        cols = int(req.get("cols", 4))
        per_board = int(req.get("perBoard", 12))
        boards = []
        for i in range(0, len(files), max(1, per_board)):
            chunk = files[i:i + per_board]
            out = os.path.join(work, f"board{i // max(1, per_board)}.jpg")
            bili.make_storyboard(chunk, out, cols, labels[i:i + len(chunk)])
            boards.append(out)
    finally:
        if os.path.exists(vpath):
            try:
                os.remove(vpath)
            except OSError:
                pass

    comments = client.comments(info["aid"], int(req.get("topComments", 5)))

    return {
        "ok": True,
        "info": {
            "bvid": info["bvid"],
            "title": info["title"],
            "owner": info["owner"],
            "duration": info["duration"],
            "desc": info["desc"][:400],
            "stat": {"view": (info.get("stat") or {}).get("view")},
        },
        "dialog": dialog[:4000],
        "comments": comments,
        "boards": boards,
        "frames": len(files),
    }


def main() -> None:
    _limit_memory()
    try:
        req = json.loads(sys.argv[1] if len(sys.argv) > 1 else "{}")
        result = run(req)
    except MemoryError:
        result = {"ok": False, "error": "memory_limit"}
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        result = {"ok": False, "error": str(exc)[:200]}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
