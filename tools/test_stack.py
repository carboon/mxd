#!/usr/bin/env python3
"""三系统联调测试: 游戏进程 + 视觉监控进程 + bot进程 并行运行。
验证: 视觉流稳定输出、bot发起攻击、击杀发生、经验累计。
"""
import csv
import json
import os
import subprocess
import sys
import time

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, R)
DURATION = int(sys.argv[1]) if len(sys.argv) > 1 else 75

ipc_env = dict(os.environ)
ipc_env["SDL_VIDEODRIVER"] = "dummy"
ipc_env["MXD_IPC"] = "1"

# 清理
import game.ipc as ipc  # noqa: E402
sys.path.insert(0, R)
ipc.cleanup_stale()
for f in [os.path.join(R, "logs/kill_log.csv"), os.path.join(R, "logs/session_exp.json"),
          os.path.join(R, "logs/bot.log"), "/tmp/mxd_vision.json"]:
    if os.path.exists(f):
        os.remove(f)

procs = []
try:
    game = subprocess.Popen([sys.executable, "game/main.py", "--headless", "--ipc"], cwd=R, env=ipc_env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    procs.append(("game", game))
    time.sleep(2)
    mon = subprocess.Popen([sys.executable, "vision/monitor.py"], cwd=R,
                           stdout=open("/tmp/monitor_out.log", "w"), stderr=subprocess.STDOUT)
    procs.append(("monitor", mon))
    time.sleep(2)
    bot = subprocess.Popen([sys.executable, "bot/autoplay.py", "--duration", str(DURATION)], cwd=R,
                           stdout=open("/tmp/bot_out.log", "w"), stderr=subprocess.STDOUT)
    procs.append(("bot", bot))
    print(f"三进程已启动, 运行{DURATION}s ...")
    t0 = time.time()
    samples = 0
    while time.time() - t0 < DURATION + 6:
        time.sleep(5)
        try:
            with open("/tmp/mxd_vision.json") as f:
                v = json.load(f)
        except Exception:
            v = {}
        samples += 1
        import json
        try:
            with open(os.path.join(R, "logs/session_exp.json")) as f:
                se = json.load(f)
        except Exception:
            se = {}
        print(f"[{time.time()-t0:5.0f}s] 视觉fps={v.get('fps','?')} 怪x{len(v.get('mobs', []))} "
              f"can_skill={v.get('can_skill')} kills={se.get('total_kills', 0)} exp={se.get('exp', 0)}")
        if bot.poll() is not None and time.time() - t0 > DURATION:
            break
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
    ipc.cleanup_stale()

# 结果汇总
print("\n===== 联调结果 =====")
kills = 0
if os.path.exists(os.path.join(R, "logs/kill_log.csv")):
    with open(os.path.join(R, "logs/kill_log.csv")) as f:
        rows = list(csv.reader(f))
    kills = len(rows) - 1
    print(f"击杀数: {kills}")
    for row in rows[1:8]:
        print("  ", row)
else:
    print("击杀数: 0 (无日志)")
if os.path.exists("/tmp/bot_out.log"):
    print("\nbot输出尾部:")
    print(open("/tmp/bot_out.log").read()[-600:])
if os.path.exists("/tmp/monitor_out.log"):
    print("monitor输出尾部:")
    print(open("/tmp/monitor_out.log").read()[-400:])
