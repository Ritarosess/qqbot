"""
音频素材分析与剪辑：为爱弥斯声音复刻准备样本

用法:
    python prep_voice_sample.py analyze                       # 分析时长/采样率/静音分布
    python prep_voice_sample.py cut 12.5 75.0                 # 裁剪指定秒数区段 → sample.wav
    python prep_voice_sample.py cut 12.5 75.0 200 240         # 多段拼接成一个样本
"""
import subprocess
import sys
import os

try:
    import imageio_ffmpeg
    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except ImportError:
    FFMPEG = "ffmpeg"

SRC = os.path.join("声音素材", "【鸣潮】爱弥斯剧情语音纯享 干声素材.m4a")
OUT = "aemeath_sample.wav"


def run(args):
    return subprocess.run([FFMPEG, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")


def analyze():
    # 1) 基本信息
    p = run(["-i", SRC, "-hide_banner"])
    info = p.stderr
    for line in info.splitlines():
        if "Duration" in line or "Stream #" in line:
            print(line.strip())

    # 2) 静音检测 → 反推连续说话段，输出最长的若干段
    p = run(["-i", SRC, "-af", "silencedetect=noise=-35dB:d=1.2", "-f", "null", "-"])
    import re
    silences = []
    cur = None
    for line in p.stderr.splitlines():
        m = re.search(r"silence_start:\s*([\d.]+)", line)
        if m:
            cur = float(m.group(1))
            continue
        m = re.search(r"silence_end:\s*([\d.]+)", line)
        if m and cur is not None:
            silences.append((cur, float(m.group(1))))
            cur = None

    total = 28 * 60 + 58.5
    # 连续语音段 = 相邻静音之间的部分
    speech = []
    pos = 0.0
    for s, e in silences:
        if s - pos >= 6:
            speech.append((pos, s))
        pos = e
    if total - pos >= 6:
        speech.append((pos, total))

    speech.sort(key=lambda x: x[1] - x[0], reverse=True)
    print(f"\n共检出 {len(speech)} 段 ≥6秒 的连续说话，最长 20 段:")
    for s, e in speech[:20]:
        print(f"  {s/60:5.1f}分{s%60:04.1f}秒 - {e/60:5.1f}分{e%60:04.1f}秒  (连续 {e-s:.1f}s)")


def cut(segments, out=None):
    """按 (start,end) 列表裁剪/拼接，输出 16bit 单声道 24kHz wav"""
    global OUT
    if out:
        OUT = out
    inputs = []
    filters = []
    for i, (ss, t) in enumerate(segments):
        inputs += ["-ss", str(ss), "-t", str(t - ss), "-i", SRC]
        filters.append(f"[{i}:a]aformat=sample_fmts=s16:sample_rates=24000:channel_layouts=mono[a{i}]")
    if len(segments) == 1:
        fc = "[0:a]aformat=sample_fmts=s16:sample_rates=24000:channel_layouts=mono[out]"
    else:
        concat = "".join(f"[a{i}]" for i in range(len(segments)))
        fc = ";".join(filters) + f";{concat}concat=n={len(segments)}:v=0:a=1[out]"
    p = run(["-y", *inputs, "-filter_complex", fc, "-map", "[out]", OUT])
    if p.returncode == 0:
        size = os.path.getsize(OUT)
        print(f"已生成 {OUT} ({size/1024/1024:.1f} MB)")
        # 复核时长
        p2 = run(["-i", OUT, "-hide_banner"])
        for line in p2.stderr.splitlines():
            if "Duration" in line:
                print(line.strip())
                break
    else:
        print("剪辑失败:", p.stderr[-500:])


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)
    cmd = sys.argv[1]
    if cmd == "analyze":
        analyze()
    elif cmd == "cut":
        argv = sys.argv[2:]
        out = None
        if argv and argv[-1].endswith(".wav"):
            out = argv[-1]
            argv = argv[:-1]
        nums = [float(x) for x in argv]
        if len(nums) < 2 or len(nums) % 2 != 0:
            print("需要成对的 开始 结束 秒数")
            sys.exit(1)
        cut(list(zip(nums[0::2], nums[1::2])), out)
