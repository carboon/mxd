#!/usr/bin/env python3
"""bot 决策层: 行为树(BT) + 黑板 + 效用评分。

感知: 只读 vision JSON(怪物列表/受击闪烁/小地图世界坐标) — 不接触游戏内部状态
动作: 只通过 IPC 发按键
协调: bot/bt.py 的 Selector/Sequence/Cond/Act/Cooldown, 每 tick 从根反应式重估

优先级(Selector 自上而下):
  1. 受击/被围脱离(规则3): 跳跃后撤到挥拳距离外, 结束后回到战斗分支
  2. 换站赶路(route 非空): 绳上行(仅地面↔第三层)/顺绳滑下/平台下跳/跳上(规则6)
  3. 战斗(规则1/2): 效用选目标(最近优先; 距离相当优先石头人)
     → 飞镖包络内(含扇形远端上下, 规则5)直接开火, 不再逼近
     → 近身<75px 先拉开(防被迫挥拳/相撞), 攻击帧同帧定朝向
  4. 换站巡逻(规则4/5): 本站可打怪清完 → 效用选下一站台 {第二层, 地面}
     站第二层时扇形可覆盖第一/三层, 地面只覆盖本层
  5. 待机
"""
import json
import os
import random
import sys
import threading
import time

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, R)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from game.ipc import RemoteInputClient
from game.config import STAR_CONE_MAX, STAR_CONE_NEAR, STAR_CONE_SLOPE
from bt import Act, Cond, FAILURE, RUNNING, Selector, Sequence, SUCCESS

VISION = "/tmp/mxd_vision.json"
JUMP_REACH = 115                    # 跳跃高~111px(轻功)
STAR_RANGE = 880                    # 飞镖飞行1.2s*760px/s, 留余量
SAFE_DIST = 75                      # 近身安全线: 挥拳判定55px + 身位余量
TIE_DIST = 60                       # 规则2 "距离相当" 判定带宽


def load_map():
    with open(f"{R}/data/map.json") as f:
        return json.load(f)


class Bot:
    def __init__(self, duration):
        self.remote = RemoteInputClient()
        self.map = load_map()
        self.ropes = self.map["ropes"]
        plats = {}
        for fh in self.map["footholds"]:
            if abs(fh["y1"] - fh["y2"]) > 6:
                continue
            y = round((fh["y1"] + fh["y2"]) / 2)
            plats.setdefault(y, []).append((min(fh["x1"], fh["x2"]), max(fh["x1"], fh["x2"])))
        self.platforms = {y: (min(a for a, _ in v), max(b for _, b in v)) for y, v in plats.items()}
        self.platform_ys = sorted(self.platforms.keys())
        self.duration = duration
        self.attack_count = 0
        self.next_attack_at = 0.0
        self._last_nav_log = 0.0
        self.target = None
        self.target_until = 0.0
        self._hp_hist = []
        self._mp_hist = []
        self._hit_streak = 0
        self.jump_star_at = random.randint(3, 5)
        self.face = 1                       # bot 对玩家朝向的信念(攻击帧同帧校正)
        # 轻功管理
        self.next_haste_at = 0.0
        self._haste_tried = False
        self._skill_until = 0.0
        # 受击脱离(规则3)
        self._dis_until = 0.0
        self._dis_d = 1
        self.next_dis_at = 0.0
        self._dismount_until = 0.0          # 绳底脱离保障期
        # ---- 分层导航 ----
        self._build_nav_graph()
        self.route = []
        self.transit_until = 0.0
        self.hop_until = 0.0
        self.hop_cd = 0.0
        self._drop_tries = 0
        self._stale_since = 0.0             # 本站无包络内目标的起始时刻
        self.station_since = time.time()    # 驻留轮换: 在站时长
        self.station_budget = random.uniform(12, 20)
        self._last_view_log = 0.0
        self._world_snap = None
        self._pw_prev = None
        self._pw_prev_t = 0.0
        self.log = open(f"{R}/logs/bot.log", "a")
        self.t0 = time.time()
        self._build_tree()

    def _nav_log(self, msg):
        now = time.time()
        if now - self._last_nav_log > 1.2:      # 同类导航日志限频
            self._last_nav_log = now
            self.logline(msg)

    def logline(self, msg):
        self.log.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        self.log.flush()

    # ---- 行为树 ------------------------------------------------------------
    def _build_tree(self):
        self.tree = Selector([
            # 1) 受击/被围 → 跳跃后撤(进行中优先; 触发带冷却)
            Sequence([Cond(lambda bb: bb["now"] < self._dis_until),
                      Act(self.disengage_leaf)]),
            Sequence([Cond(self.need_disengage), Act(self.start_disengage)]),
            # 2) 换站赶路
            Act(self.transit_leaf),
            # 2.5) 绳底脱离保障期(滑到底后0.7s反复↓+跳, 防吊在绳上)
            Sequence([Cond(lambda bb: bb["now"] < self._dismount_until),
                      Act(lambda bb: (self.send(down=True, jump=True), RUNNING)[1])]),
            # 2.6) 驻留轮换: 在站超时 → 换另一个优选站台(规则4: 两站间巡逻)
            Sequence([Cond(self.station_expired), Act(self.rotate_station)]),
            # 3) 战斗: 选目标(规则2) → 包络内直接开火(规则1)
            Sequence([Cond(self.pick_target), Act(self.combat_leaf)]),
            # 4) 本站清完 → 换站巡逻(规则4/5)
            Act(self.reposition_leaf),
            # 5) 待机
            Act(self.idle_leaf),
        ])

    # ---- 视觉输入/黑板 ------------------------------------------------------
    def read_vision(self):
        try:
            with open(VISION) as f:
                d = json.load(f)
            if time.time() - d.get("t", 0) > 1.0:
                return None
            return d
        except Exception:
            return None

    def _perceive(self, now, vis):
        p = vis.get("player")
        if not p:
            return None
        bb = {"now": now, "hit_streak": self._hit_streak}
        pw = vis.get("player_world")
        if pw and vis.get("cam") is not None:
            # 位置跳变合理性: 箭头偶发误检造成瞬移假读数, 拒收
            if self._pw_prev and now - self._pw_prev_t < 0.5:
                jump = abs(pw["x"] - self._pw_prev["x"]) + abs(pw["y"] - self._pw_prev["y"])
                if jump > 250:
                    pw = self._pw_prev
            self._pw_prev, self._pw_prev_t = dict(pw), now
            self._world_snap = (pw, vis["cam"], vis.get("mob_world", []), now)
        elif self._world_snap is not None:
            pw2, cam2, mb2, t2 = self._world_snap
            if now - t2 <= 2.5:
                pw, vis["cam"], vis["mob_world"] = pw2, cam2, mb2
            else:
                pw = None
        if pw and vis.get("mob_world") is not None and vis.get("cam") is not None:
            bb["px"], bb["py"] = pw["x"], pw["y"]
            bb["mobs"] = [dict(m, x=m["wx"]) for m in vis["mob_world"]
                          if -320 < m["wy"] < 340 and -360 < m["wx"] < 1170]
            bb["world_ok"] = True
        else:
            bb["px"], bb["py"] = p["x"], p["y"]
            bb["mobs"] = vis.get("mobs", [])
            bb["world_ok"] = False
        bb["cur"] = self.layer_of(bb["py"]) if bb["world_ok"] else None
        bb["near_mobs"] = [m for m in bb["mobs"]
                           if abs(m["y"] - bb["py"]) < 60 and abs(m["x"] - bb["px"]) < 90]
        return bb

    # ---- 几何/地图 ----------------------------------------------------------
    def layer_of(self, y):
        best, bd = None, 1e9
        for i, rep in enumerate(getattr(self, "layer_rep", [])):
            d = abs(y - rep)
            if d < bd:
                best, bd = i, d
        return best if bd <= 50 else None

    def on_rope(self, px, py):
        for r in self.ropes:
            top, bot = min(r["y1"], r["y2"]), max(r["y1"], r["y2"])
            if abs(px - r["x"]) <= 26 and top - 6 <= py <= bot + 6:
                return r
        return None

    def star_envelope(self, px, py, mx, my):
        """目标是否在飞镖包络内(镜像 combat.Star 的扇形判定, 规则1/5)。"""
        dx = abs(mx - px)
        if dx > STAR_RANGE:
            return False
        half = min(STAR_CONE_NEAR + STAR_CONE_SLOPE * dx, STAR_CONE_MAX)
        star_y = py - 38                       # 出手高度(站立)
        return (star_y + half >= my - 50) and (star_y - half <= my + 6)

    # ---- 导航图 --------------------------------------------------------------
    def _build_nav_graph(self):
        ys = sorted(self.platforms.keys())
        groups = []
        for y in ys:
            if groups and y - groups[-1][-1] <= 40:
                groups[-1].append(y)
            else:
                groups.append([y])
        self.layer_groups = groups
        self.layer_rep = [g[len(g) // 2] for g in groups]
        self.layer_span = {}
        self.layer_segs = {}
        for i, g in enumerate(groups):
            segs = []
            for y in g:
                a, b = self.platforms[y]
                segs.append((a, b))
            self.layer_segs[i] = segs
            self.layer_span[i] = (min(a for a, _ in segs), max(b for _, b in segs))
        # 语义层(用户计数, 自上而下): 第一层-71 / 第二层26 / 第三层123 / 地面305
        # (最顶-251的石台不在巡逻集合)
        def near(target):
            return min(range(len(self.layer_rep)), key=lambda i: abs(self.layer_rep[i] - target))
        self.L_FIRST, self.L_SECOND = near(-71), near(26)
        self.L_THIRD, self.L_GROUND = near(123), near(305)
        self.edges = {i: [] for i in range(len(groups))}
        # 1) 藤蔓上行/滑下 — 规则6: 攀爬仅限 地面↔第三层
        for r in self.ropes:
            top, bot = min(r["y1"], r["y2"]), max(r["y1"], r["y2"])
            if self.layer_of(top) != self.L_THIRD:
                continue
            rep_g = self.layer_rep[self.L_GROUND]
            if rep_g - JUMP_REACH <= bot <= rep_g + 12:
                self.edges[self.L_GROUND].append({"kind": "rope_up", "to": self.L_THIRD,
                                                  "x": float(r["x"]), "top_y": top})
            land = self._rope_landing(r["x"], bot)
            if land is not None and land[1] == self.L_GROUND:
                self.edges[self.L_THIRD].append({"kind": "rope_down", "to": self.L_GROUND,
                                                 "x": float(r["x"]), "land_y": land[0]})
        # 2) 跳上边 — 规则6: 第三层→第二层, 第二层→第一层 (层间距97 < 跳高111)
        for lo, hi in [(self.L_THIRD, self.L_SECOND), (self.L_SECOND, self.L_FIRST)]:
            dy = self.layer_rep[lo] - self.layer_rep[hi]
            if 40 <= dy <= 105:
                x = self._drop_x(lo, hi)
                if x is not None:
                    self.edges[lo].append({"kind": "jump_up", "to": hi, "x": x})
        # 3) 平台下跳边(下降): 相邻下一层, 落点=两层 foothold 交集
        order = sorted(range(len(self.layer_rep)), key=lambda i: self.layer_rep[i])
        for idx, i in enumerate(order):
            for j in order[idx + 1:]:
                if self.layer_rep[j] <= self.layer_rep[i]:
                    continue
                x = self._drop_x(i, j)
                if x is not None:
                    self.edges[i].append({"kind": "drop", "to": j, "x": x})
                break

    def _drop_x(self, i, j):
        """层i→层j 的落点: 两层 foothold 线段交集最大段的中点(避开藤蔓)。"""
        best = None
        for a1, a2 in self.layer_segs[i]:
            for b1, b2 in self.layer_segs[j]:
                lo, hi = max(a1, b1), min(a2, b2)
                if hi - lo <= 30:
                    continue
                if best is None or hi - lo > best[1]:
                    best = ((lo + hi) / 2, hi - lo)
        if best is None:
            return None
        return self._avoid_ropes(best[0], best[0] - 60, best[0] + 60)

    def _rope_landing(self, x, rope_bot):
        best = None
        for y, (a, b) in self.platforms.items():
            if a - 24 <= x <= b + 24 and rope_bot - 30 <= y <= rope_bot + 60:
                if best is None or y > best[0]:
                    best = (y, self.layer_of(y))
        return best

    def _avoid_ropes(self, x, lo, hi):
        for r in self.ropes:
            rx = float(r["x"])
            if abs(x - rx) < 32 and lo <= rx <= hi:
                x = rx - 40 if rx - 40 >= lo else rx + 40
        return x

    def plan_route(self, src, dst):
        """BFS 多跳路线: 返回 hop 列表, 失败返回 None。"""
        if src == dst:
            return []
        prev = {src: None}
        q = [src]
        while q:
            i = q.pop(0)
            for e in self.edges.get(i, []):
                if e["to"] not in prev:
                    prev[e["to"]] = (i, e)
                    q.append(e["to"])
        if dst not in prev:
            return None
        hops = []
        cur = dst
        while prev[cur] is not None:
            i, e = prev[cur]
            hops.append(dict(e))
            cur = i
        hops.reverse()
        return hops

    # ---- 导航执行 -------------------------------------------------------------
    def do_transit(self, px, py, cur):
        now = time.time()
        if now > self.transit_until:
            self.route = []
            self._nav_log("NAV 导航超时, 放弃本次换站")
            self.release_all()
            return
        hop = self.route[0]
        to_rep = self.layer_rep[hop["to"]]
        on = self.on_rope(px, py)
        done = False
        if hop["kind"] == "rope_up":
            done = py <= hop["top_y"] + 6
        elif hop["kind"] == "rope_down":
            done = cur == hop["to"] and py >= hop["land_y"] - 20
        elif hop["kind"] == "drop":
            done = cur == hop["to"] and py >= to_rep - 20
        elif hop["kind"] == "jump_up":
            done = cur == hop["to"]
        if done:
            self.route.pop(0)
            self._drop_tries = 0
            self._stale_since = 0.0
            if hop["kind"] == "rope_down" and not self.route:
                self._dismount_until = now + 0.7
            self.send(jump=True)
            if self.route:
                self.hop_until = now + 12.0
                self._nav_log(f"NAV hop完成 → 层{hop['to']}(y={to_rep:.0f}), 剩{len(self.route)}跳")
            else:
                self._nav_log(f"PATROL 到达层{hop['to']}(y={to_rep:.0f})")
            return
        if now > self.hop_until:
            self.route = []
            self._nav_log(f"NAV hop超时({hop['kind']}→层{hop['to']}), 放弃本次换站")
            self.release_all()
            return
        if hop["kind"] == "rope_up":
            if on is not None and abs(px - hop["x"]) <= 26:
                self.send(up=True)
            elif abs(px - hop["x"]) > 8:
                d = 1 if hop["x"] > px else -1
                self.send(left=(d < 0), right=(d > 0))
            else:
                jump = now >= self.hop_cd
                self.send(up=True, jump=jump)
                if jump:
                    self.hop_cd = now + 0.8
                    self._nav_log(f"NAV 抓绳x={hop['x']:.0f} 上爬 → 层{hop['to']}")
        elif hop["kind"] == "rope_down":
            if on is not None:
                self.send(down=True)
            elif abs(px - hop["x"]) > 10:
                d = 1 if hop["x"] > px else -1
                self.send(left=(d < 0), right=(d > 0))
            else:
                if now >= self.hop_cd:
                    self.hop_cd = now + 2.0
                    self._nav_log(f"NAV 站绳顶x={hop['x']:.0f} 顺绳滑下 → 层{hop['to']}")
                self.send(down=True)
        elif hop["kind"] == "drop":
            if abs(px - hop["x"]) > 14:
                d = 1 if hop["x"] > px else -1
                self.send(left=(d < 0), right=(d > 0))
            else:
                jump = now >= self.hop_cd
                self.send(down=True, jump=jump)
                if jump:
                    self.hop_cd = now + 0.85
                    self._drop_tries += 1
                    if self._drop_tries == 2:
                        hop["x"] -= 32
                    elif self._drop_tries == 3:
                        hop["x"] += 64
                    self._nav_log(f"NAV 平台下跳 → 层{hop['to']}(y={to_rep:.0f}) 第{self._drop_tries}次")
                    if self._drop_tries >= 4:
                        self.route = []
                        self._nav_log("NAV 下跳多次未生效, 放弃本次换站")
                        self.release_all()
        elif hop["kind"] == "jump_up":
            if abs(px - hop["x"]) > 10:
                d = 1 if hop["x"] > px else -1
                self.send(left=(d < 0), right=(d > 0))
            else:
                if now >= self.hop_cd:
                    self.hop_cd = now + 0.9
                    self._drop_tries += 1
                    self._nav_log(f"NAV 跳上 → 层{hop['to']}(y={to_rep:.0f}) 第{self._drop_tries}次")
                    self.send(jump=True)
                    if self._drop_tries >= 5:
                        self.route = []
                        self._nav_log("NAV 跳上多次未生效, 放弃本次换站")
                        self.release_all()
                else:
                    self.send()

    # ---- BT 叶子: 换站赶路 -------------------------------------------------------
    def transit_leaf(self, bb):
        if not self.route:
            return FAILURE
        if not bb["world_ok"]:
            self.release_all()
            return RUNNING                      # 世界坐标断供: 原地等(绳上挂着也安全)
        cur = bb["cur"]
        goal = self.route[-1]["to"]
        if cur is not None and cur == goal and self.on_rope(bb["px"], bb["py"]) is None:
            self.route = []
            self._stale_since = 0.0
            self._nav_log(f"PATROL 提前到达层{cur}(y={self.layer_rep[cur]:.0f})")
            self.release_all()
            return RUNNING
        self.do_transit(bb["px"], bb["py"], cur)
        return RUNNING

    # ---- BT 叶子: 受击脱离(规则3) ----------------------------------------------
    def need_disengage(self, bb):
        if bb["now"] < self.next_dis_at:
            return False
        return bb["hit_streak"] >= 2 or len(bb["near_mobs"]) >= 3

    def start_disengage(self, bb):
        near = bb["near_mobs"]
        cx = sum(m["x"] for m in near) / len(near) if near else bb["px"]
        d = -1 if cx > bb["px"] else 1
        if bb["px"] < -230:                  # 地图边缘: 只能向图内撤
            d = 1
        elif bb["px"] > 1060:
            d = -1
        self._dis_d = d
        self._dis_until = bb["now"] + 0.55
        self.next_dis_at = bb["now"] + 2.5
        self.logline(f"受击/被围x{len(near)} → 跳跃后撤向{'右' if self._dis_d > 0 else '左'} "
                     f"(拉到挥拳距离外)")
        return SUCCESS

    def disengage_leaf(self, bb):
        d = self._dis_d
        self.send(left=(d < 0), right=(d > 0), jump=True)   # 持续跳+位移拉开
        return RUNNING

    # ---- BT 条件: 目标选择(规则1/2) ---------------------------------------------
    def pick_target(self, bb):
        reach = [(abs(m["x"] - bb["px"]), m) for m in bb["mobs"]
                 if self.star_envelope(bb["px"], bb["py"], m["x"], m["y"])]
        if not reach:
            # 2s持久性: 视觉抖动时沿用上个目标(位置用最后已知值)
            if self.target and bb["now"] < self.target_until:
                t = self.target
                if self.star_envelope(bb["px"], bb["py"], t["x"], t["y"]):
                    bb["target"], bb["target_dist"] = t, abs(t["x"] - bb["px"])
                    return True
            return False
        reach.sort(key=lambda p: p[0])
        base = reach[0][0]
        tied = [m for d, m in reach if d - base <= TIE_DIST]
        golems = [m for m in tied if m.get("name") == "石头人"]
        bb["target"] = golems[0] if golems else reach[0][1]
        bb["target_dist"] = abs(bb["target"]["x"] - bb["px"])
        self._stale_since = 0.0
        return True

    # ---- BT 叶子: 战斗(规则1/3) -------------------------------------------------
    def combat_leaf(self, bb):
        t, now, dist = bb["target"], bb["now"], bb["target_dist"]
        toward = 1 if t["x"] - bb["px"] > 0 else -1
        self.target, self.target_until = t, now + 2.0
        if now >= self.next_attack_at:
            # 攻击帧: 朝向键+攻击同帧发(游戏在攻击帧用方向键定朝向), 位移≈2px可忽略
            self.face = toward
            self.attack_count += 1
            if self.attack_count % self.jump_star_at == 0 and random.random() < 0.8:
                self.send(left=(toward < 0), right=(toward > 0), jump=True)
                threading.Timer(0.09, lambda: self.send(attack=True)).start()
                self.logline(f"跳标! 目标{t['name']} dist={dist:.0f}")
            else:
                self.send(left=(toward < 0), right=(toward > 0), attack=True)
                self.logline(f"开火 #{self.attack_count} 目标{t['name']} dist={dist:.0f}")
            self.next_attack_at = now + random.uniform(0.55, 0.85)
        elif dist < SAFE_DIST:
            away = -toward
            if bb["px"] < -230:              # 地图边缘: 只能向图内拉开
                away = 1
            elif bb["px"] > 1060:
                away = -1
            self.send(left=(away < 0), right=(away > 0))    # 非攻击帧拉开, 防相撞
        else:
            self.send()
        return RUNNING

    # ---- BT 叶子: 换站巡逻(规则4/5) ---------------------------------------------
    def coverage(self, py, mobs):
        """从高度 py 站位可打到的怪数: 出手高度±扇形封顶(规则5: 站二层覆盖一/三层)。"""
        star_y = py - 38
        return sum(1 for m in mobs if abs(m["y"] - star_y) <= STAR_CONE_MAX + 50)

    def station_expired(self, bb):
        return (bb["cur"] in (self.L_SECOND, self.L_GROUND)
                and bb["now"] - self.station_since > self.station_budget)

    def rotate_station(self, bb):
        cur = bb["cur"]
        other = self.L_GROUND if cur == self.L_SECOND else self.L_SECOND
        self.station_since = bb["now"]
        self.station_budget = random.uniform(12, 20)
        return self._goto(bb, other, bb["now"])

    def reposition_leaf(self, bb):
        now, cur = bb["now"], bb["cur"]
        if not bb["world_ok"] or cur is None:
            self.send()
            return RUNNING
        stations = (self.L_SECOND, self.L_GROUND)
        cov = {s: self.coverage(self.layer_rep[s], bb["mobs"]) for s in stations}
        if cur in stations and cov[cur] > 0:
            same = [m for m in bb["mobs"] if self.layer_of(m["y"]) == cur]
            if same:
                m = min(same, key=lambda m: abs(m["x"] - bb["px"]))
                if abs(m["x"] - bb["px"]) > 140:
                    d = 1 if m["x"] > bb["px"] else -1
                    self.face = d
                    self.send(left=(d < 0), right=(d > 0))   # 同层走位把怪拉进射程
                    return RUNNING
            if not self._stale_since:
                self._stale_since = now
            if now - self._stale_since < 3.0:
                self.send()
                return RUNNING                               # 等邻层怪游进扇形
            other = stations[1] if cur == stations[0] else stations[0]
            return self._goto(bb, other, now)                # 等超时 → 换另一站
        best = max(stations, key=lambda s: cov[s])
        if cur in stations and cov[best] == 0:
            best = stations[1] if cur == stations[0] else stations[0]
        return self._goto(bb, best, now)

    def _goto(self, bb, goal, now):
        cur = bb["cur"]
        if cur == goal:
            self._stale_since = 0.0
            return FAILURE
        route = self.plan_route(cur, goal)
        if not route:
            self._nav_log(f"NAV 无路可达层{goal}(y={self.layer_rep[goal]:.0f})")
            self.send()
            return RUNNING
        self.route = route
        self.transit_until = now + 7.0 * len(route) + 8.0
        self.hop_until = now + 12.0
        self._drop_tries = 0
        desc = " → ".join(f"层{h['to']}({h['kind']})" for h in route)
        self.logline(f"PATROL 换站 y{self.layer_rep[cur]:.0f}→y{self.layer_rep[goal]:.0f} "
                     f"路线: {desc}")
        return RUNNING

    def idle_leaf(self, bb):
        self.send()
        return RUNNING

    # ---- 主循环 -----------------------------------------------------------------
    def send(self, left=False, right=False, up=False, down=False, attack=False, jump=False,
             skill=False):
        self.remote.send(left=left, right=right, up=up, down=down,
                         attack=attack, jump=jump, skill=skill)

    def release_all(self):
        self.send()

    def run(self):
        self.logline(f"bot 启动(BT), 运行{self.duration}s")
        last = time.time()
        while time.time() - self.t0 < self.duration:
            now = time.time()
            dt = now - last
            last = now
            vis = self.read_vision()
            if vis is None or not vis.get("player"):
                self.release_all()
                time.sleep(0.05)
                continue
            self._hp_hist = (self._hp_hist + [vis.get("hp_ratio", 1)])[-3:]
            self._mp_hist = (self._mp_hist + [vis.get("mp_ratio", 1)])[-3:]
            vis["hp_ratio"] = sorted(self._hp_hist)[len(self._hp_hist) // 2]
            vis["mp_ratio"] = sorted(self._mp_hist)[len(self._mp_hist) // 2]
            if vis.get("hit"):
                self._hit_streak += 1
            else:
                self._hit_streak = 0
            if now - self._last_view_log > 2.0:
                self._last_view_log = now
                ms = [(m["name"], int(m.get("wx", m["x"])), int(m.get("wy", m["y"])))
                      for m in vis.get("mob_world") or vis.get("mobs", [])]
                self.logline(f"VIEW 我=({vis['player']['x']:.0f},{vis['player']['y']:.0f}) "
                             f"pw={vis.get('player_world')} 怪={ms[:5]}")
            # 轻功管理: 图标消失/闪烁(视觉判定) → 按A补放
            haste = vis.get("haste")
            if haste is None:
                if not self._haste_tried and now - self.t0 > 2:
                    self._haste_tried = True
                    self._skill_until = now + 0.25
                    self.next_haste_at = now + 8
                    self.logline("轻功: 开局补放(兼容模式)")
            elif haste.get("can_recast") and now >= self.next_haste_at:
                self._skill_until = now + 0.25
                self.next_haste_at = now + 8
                self.logline("轻功补放")
            bb = self._perceive(now, vis)
            if bb is None:
                self.release_all()
                time.sleep(0.05)
                continue
            if self._skill_until > now:
                self.send(skill=True)
                continue
            self.tree.tick(bb)
            time.sleep(0.05)
        self.release_all()
        self.logline(f"bot 结束: 攻击{self.attack_count}次")
        self.log.close()


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=90)
    args = ap.parse_args()
    Bot(args.duration).run()


if __name__ == "__main__":
    main()
