#!/usr/bin/env python3
"""确定性导航验证（子进程版）：游戏+监控+bot 三进程，世界坐标真值取证。

场景A(攀爬): 怪全移到上层(y≤200), 角色在底层305 → bot须走向藤蔓x=637,
             跳跃抓绳攀爬到~124层再攻击。证据: 世界y 305→<200, bot.log NAV攀爬。
场景B(下跳): 怪全移到地面, 角色出生中层(300,121) → bot须下+跳下跳/走下台阶。
             证据: 世界y 121→≥280, bot.log NAV下层。
场景C(脱围): 3只怪紧贴角色出生点(MXD_MOBS_AROUND=1) → bot须利用无敌时间
             跳跃/下跳/爬藤脱离包围。证据: bot.log SURROUNDED, 位移>100px或换层。
"""
import csv
import json
import os
import subprocess
import sys
import time

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, R)
VISION = "/tmp/mxd_vision.json"


def kill_orphans():
    """清理残留的测试进程(孤儿游戏会覆盖真值文件)。"""
    for pat in ["game/main.py", "vision/monitor.py", "bot/autoplay.py"]:
        subprocess.run(["pkill", "-f", pat], capture_output=True)
    time.sleep(0.8)


def run_scenario(name, dur, env_extra):
    kill_orphans()
    from game import ipc as ipc_mod
    ipc_mod.cleanup_stale()
    for f in [os.path.join(R, "logs/kill_log.csv"), os.path.join(R, "logs/bot.log"),
              os.path.join(R, "logs/session_exp.json"), VISION, "/tmp/mxd_truth.json"]:
        if os.path.exists(f):
            os.remove(f)
    env = dict(os.environ)
    env["SDL_VIDEODRIVER"] = "dummy"
    env["MXD_IPC"] = "1"
    env["MXD_TRUTH"] = "1"
    env.update(env_extra)
    procs = []
    try:
        procs.append(subprocess.Popen([sys.executable, "game/main.py", "--headless", "--ipc"],
                                      cwd=R, env=env,
                                      stdout=open(f"/tmp/game_{name}.log", "w"),
                                      stderr=subprocess.STDOUT))
        time.sleep(2)
        procs.append(subprocess.Popen([sys.executable, "vision/monitor.py"], cwd=R,
                                      stdout=open(f"/tmp/mon_{name}.log", "w"), stderr=subprocess.STDOUT))
        time.sleep(2)
        procs.append(subprocess.Popen([sys.executable, "bot/autoplay.py", "--duration", str(dur)],
                                      cwd=R, stdout=open(f"/tmp/bot_{name}.log", "w"), stderr=subprocess.STDOUT))
        print(f"[{name}] 三进程启动, 采样 {dur}s ...")
        track = []
        t0 = time.time()
        on_rope_seen = False
        while time.time() - t0 < dur + 4:
            time.sleep(0.25)
            try:
                with open("/tmp/mxd_truth.json") as f:
                    tr = json.load(f)
                track.append((round(time.time() - t0, 1), int(tr["x"]), int(tr["y"])))
                if tr.get("onRope"):
                    on_rope_seen = True
            except Exception:
                pass
        return track, on_rope_seen
    finally:
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        time.sleep(1)
        for p in procs:
            try:
                p.kill()
            except Exception:
                pass
        ipc_mod.cleanup_stale()


def botlog():
    p = os.path.join(R, "logs/bot.log")
    return open(p).read() if os.path.exists(p) else ""


def main():
    kill_orphans()
    print("=" * 28, "场景A: 攀爬藤蔓上高层", "=" * 28)
    track_a, rope_a = run_scenario("climb", 60, {"MXD_MOBS_UP": "1"})
    log_a = botlog()
    ys = [y for _, _, y in track_a]
    print("角色世界y轨迹:", ys)
    climbed = min(ys) < 200 and max(ys) > 280 if ys else False
    kills_a = 0
    kp = os.path.join(R, "logs/kill_log.csv")
    if os.path.exists(kp):
        with open(kp) as f:
            kills_a = max(0, len(list(csv.reader(f))) - 1)
    nav_a = ("藤蔓" in log_a and "攀爬" in log_a)
    print(f"场景A: 层级跨越={climbed} (y min={min(ys) if ys else '-'} max={max(ys) if ys else '-'}) "
          f"攀爬导航日志={nav_a} 抓绳标志={rope_a} 击杀={kills_a}")

    print("=" * 28, "场景B: 中层下跳到地面", "=" * 28)
    track_b, rope_b = run_scenario("down", 45, {"MXD_PLAYER_AT": "300,121", "MXD_MOBS_GROUND": "1"})
    log_b = botlog()
    ys = [y for _, _, y in track_b]
    print("角色世界y轨迹:", ys)
    descended = max(ys) >= 280 and min(ys) < 180 if ys else False
    nav_b = ("下跳" in log_b or "下层" in log_b)
    print(f"场景B: 层级跨越={descended} (y min={min(ys) if ys else '-'} max={max(ys) if ys else '-'}) "
          f"下跳导航日志={nav_b}")

    print("=" * 28, "场景C: 被围殴→无敌期脱离", "=" * 28)
    track_c, _ = run_scenario("escape", 40, {"MXD_MOBS_AROUND": "1"})
    log_c = botlog()
    if track_c:
        x_start, y_start = track_c[0][1], track_c[0][2]
        moved = max(abs(x - x_start) for x, _, _ in track_c) > 100
        left_plat = min(y for _, _, y in track_c) < y_start - 40
    else:
        moved = left_plat = False
    esc_c = "SURROUNDED" in log_c
    haste_c = "轻功" in log_c
    print(f"场景C: 脱围日志={esc_c} 离开原地>100px={moved} 换层={left_plat} 轻功补放={haste_c}")
    print("角色世界x轨迹采样:", [x for _, x, _ in track_c[::8]])

    print("=" * 28, "结论", "=" * 28)
    ok_a = climbed and nav_a
    ok_b = descended and nav_b
    ok_c = esc_c and (moved or left_plat)
    print(f"攀爬验证: {'PASS' if ok_a else 'FAIL'}")
    print(f"下跳验证: {'PASS' if ok_b else 'FAIL'}")
    print(f"脱围验证: {'PASS' if ok_c else 'FAIL'}")
    print("bot.log 导航日志(场景A):")
    for line in log_a.splitlines():
        if "NAV" in line:
            print("  ", line)
    print("bot.log 导航日志(场景B):")
    for line in log_b.splitlines():
        if "NAV" in line:
            print("  ", line)
    print("bot.log 脱围/轻功日志(场景C):")
    for line in log_c.splitlines():
        if "SURROUNDED" in line or "轻功" in line or "挥拳" in line:
            print("  ", line)
    if not (ok_a and ok_b and ok_c):
        sys.exit(1)
    print("NAVIGATION EVIDENCE OK")


if __name__ == "__main__":
    main()
