#!/usr/bin/env python3
"""离线验证 bot 分层导航图: 层聚类/边/全对全路线(不启动游戏)。"""
import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fake = types.ModuleType("game.ipc")
class FakeRemote:
    def send(self, **k): pass
fake.RemoteInputClient = FakeRemote
sys.modules["game.ipc"] = fake

from bot.autoplay import Bot

b = object.__new__(Bot)
b.map = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data/map.json")))
b.ropes = b.map["ropes"]
plats = {}
for fh in b.map["footholds"]:
    if abs(fh["y1"] - fh["y2"]) > 6:
        continue
    y = round((fh["y1"] + fh["y2"]) / 2)
    plats.setdefault(y, []).append((min(fh["x1"], fh["x2"]), max(fh["x1"], fh["x2"])))
b.platforms = {y: (min(a for a, _ in v), max(b2 for _, b2 in v)) for y, v in plats.items()}
b.platform_ys = sorted(b.platforms.keys())
b._build_nav_graph()

print("层:", [(i, b.layer_rep[i]) for i in range(len(b.layer_rep))])
for i in sorted(b.edges):
    for e in b.edges[i]:
        if e["kind"] == "rope_up":
            extra = "top_y=%s" % e["top_y"]
        elif e["kind"] == "rope_down":
            extra = "land_y=%s" % e["land_y"]
        else:
            extra = ""
        print("  层%d(y=%.0f) → 层%d(y=%.0f)  %-9s x=%.0f  %s" % (
            i, b.layer_rep[i], e["to"], b.layer_rep[e["to"]], e["kind"], e["x"], extra))
print()
fail = 0
for src in range(len(b.layer_rep)):
    for dst in range(len(b.layer_rep)):
        if src == dst:
            continue
        r = b.plan_route(src, dst)
        if r is None:
            if dst == 0:
                print("  %d→%d: 无路(预期内: 顶层石台不在巡逻集, 规则6)" % (src, dst))
            else:
                print("  %d→%d: 无路!" % (src, dst))
                fail += 1
        else:
            print("  %d→%d: %s" % (src, dst, " → ".join(h["kind"] for h in r)))
print("失败数:", fail)
