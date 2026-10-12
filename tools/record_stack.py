#!/usr/bin/env python3
"""录制完整bot操作视频: 游戏进程 + 视觉监控 + bot 并行运行,
从游戏帧共享内存(即玩家看到的完整画面: 地图/怪物/UI/buff图标/伤害数字/MISS特效)
按30Hz抓帧, 管道喂 ffmpeg 编码为 H.264 MP4。

用法: python3 tools/record_stack.py [时长秒, 默认90]
输出: videos/bot_gameplay.mp4
"""
import csv
import os
import subprocess
import sys
import time

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, R)
DURATION = int(sys.argv[1]) if len(sys.argv) > 1 else 90
FPS = 15
OUT = os.path.join(R, "videos")
os.makedirs(OUT, exist_ok=True)
MP4 = os.path.join(OUT, "bot_gameplay.mp4")

ipc_env = dict(os.environ)
ipc_env["SDL_VIDEODRIVER"] = "dummy"
ipc_env["MXD_IPC"] = "1"

import game.ipc as ipc  # noqa: E402
ipc.cleanup_stale()
for f in [os.path.join(R, "logs/kill_log.csv"), os.path.join(R, "logs/session_exp.json"),
          os.path.join(R, "logs/bot.log"), "/tmp/mxd_vision.json", MP4]:
    if os.path.exists(f):
        os.remove(f)

# ffmpeg: rawvideo RGB24 → H.264 MP4 (QuickTime可直开)
ffmpeg = subprocess.Popen(
    ["ffmpeg", "-y", "-loglevel", "error",
     "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "1280x724", "-r", str(FPS), "-i", "-",
     "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast", "-crf", "23",
     "-movflags", "+faststart", MP4],
    stdin=subprocess.PIPE)

procs = []
try:
    game = subprocess.Popen([sys.executable, "game/main.py", "--headless", "--ipc"], cwd=R,
                            env=ipc_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    procs.append(("game", game))
    time.sleep(2)
    mon = subprocess.Popen([sys.executable, "vision/monitor.py"], cwd=R,
                           stdout=open("/tmp/monitor_out.log", "w"), stderr=subprocess.STDOUT)
    procs.append(("monitor", mon))
    time.sleep(2)
    bot = subprocess.Popen([sys.executable, "bot/autoplay.py", "--duration", str(DURATION)],
                           cwd=R, stdout=open("/tmp/bot_out.log", "w"), stderr=subprocess.STDOUT)
    procs.append(("bot", bot))
    print(f"三进程已启动, 录制{DURATION}s @ {FPS}fps ...")

    from game.ipc import FrameReader
    reader = None
    last_frame = None
    written = 0
    t0 = time.time()
    next_tick = t0
    while time.time() - t0 < DURATION + 2:
        if reader is None:
            try:
                reader = FrameReader()
            except Exception:
                time.sleep(0.2)
                continue
        seq, frame = reader.read()
        if frame is not None:
            last_frame = frame
        if last_frame is not None:
            try:
                ffmpeg.stdin.write(last_frame.tobytes())
                written += 1
            except BrokenPipeError:
                print("ffmpeg 管道中断, 停止录制")
                break
        next_tick += 1.0 / FPS
        delay = next_tick - time.time()
        if delay > 0:
            time.sleep(delay)
        else:
            next_tick = time.time()          # 落后了就重新对齐
        if int(time.time() - t0) % 15 == 0 and abs((time.time() - t0) % 15) < 0.4:
            print(f"  [{time.time()-t0:5.1f}s] 已写帧 {written}")
finally:
    for name, p in procs:
        try:
            p.terminate()
        except Exception:
            pass
    time.sleep(1)
    for name, p in procs:
        try:
            p.kill()
        except Exception:
            pass
    try:
        ffmpeg.stdin.close()
        ffmpeg.wait(timeout=15)
    except Exception:
        ffmpeg.kill()
    ipc.cleanup_stale()

# 汇总
print("\n===== 录制结果 =====")
if os.path.exists(MP4):
    mb = os.path.getsize(MP4) / 1e6
    print(f"视频: {MP4}  ({mb:.1f} MB, 写帧 {written})")
    subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1", MP4])
kills = 0
if os.path.exists(os.path.join(R, "logs/kill_log.csv")):
    with open(os.path.join(R, "logs/kill_log.csv")) as f:
        rows = list(csv.reader(f))
    kills = len(rows) - 1
    print(f"期间击杀: {kills}")
print("\nbot 关键操作日志:")
if os.path.exists(os.path.join(R, "logs/bot.log")):
    for line in open(os.path.join(R, "logs/bot.log")):
        if any(k in line for k in ("轻功", "SURROUNDED", "远距狙猴", "后撤", "跳标")):
            print("  " + line.rstrip())
