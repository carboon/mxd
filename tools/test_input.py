#!/usr/bin/env python3
"""端到端验证虚拟键盘通道: 游戏已由外部启动(--headless --ipc MXD_TRUTH=1),
本脚本以20Hz持续写 left=True, 2秒后读 /tmp/mxd_truth.json 对比玩家x是否移动。"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from game.ipc import RemoteInputClient, cleanup_stale  # noqa: E402

TRUTH = "/tmp/mxd_truth.json"


def truth():
    try:
        with open(TRUTH) as f:
            return json.load(f)
    except Exception:
        return None


def main():
    t0 = truth()
    print("初始 truth:", t0)
    c = RemoteInputClient()
    # 持续2.0s写left (模拟bot 20Hz持续按键)
    end = time.time() + 2.0
    n = 0
    while time.time() < end:
        c.send(left=True)
        n += 1
        time.sleep(0.05)
    c.send()  # release
    time.sleep(0.4)
    t1 = truth()
    print(f"写了{n}次left, 结束 truth:", t1)
    if t0 and t1:
        dx = t1["x"] - t0["x"]
        print(f"Δx = {dx:.1f}  ({'移动正常' if dx < -30 else '移动偏慢' if dx < -10 else '没动!'})")


if __name__ == "__main__":
    main()
