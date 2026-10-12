#!/usr/bin/env python3
"""系统3：模拟人类操作的自动战斗 bot（基于系统2的视觉识别结果）。

不读取游戏内部状态，只看 /tmp/mxd_vision.json（视觉输出）与地图公开数据
(平台/藤蔓，等价于玩家对地图的熟悉)。通过虚拟键盘注入操作。

行为:
  - 轻功管理: 图标消失/闪烁(视觉) → 按A补放(人类化间隔≥8s)
  - 被围殴脱离: ≥2只怪贴身(或贴身+连续受击) → 利用无敌时间
    ①朝怪少的一侧跳跃脱离 ②下方更安全则平台下跳 ③上方更安全则爬藤离层
  - 分层巡逻(核心): 把地图平台聚成层, 预生成 绳上行/顺绳滑下/平台下跳 路线图
    (BFS多跳规划); 同层清怪驻留9~15s后换"可见怪最多"的层, 本层无怪立即换层
    → 像人一样逐层清场: 走到藤蔓→跳抓绳→攀爬→翻上平台 / 顺绳滑下 / 平台下跳
  - 选择同层最近怪 → 走近 → 双飞斩(人类化反应延迟)
  - 每3-5次攻击插入"跳标"(跳起后空中丢镖, 标飞标志性操作)
  - 后撤丢镖(核心站位): 贴太近会被迫挥拳(伤害≈0)且被反复触碰 → 保持75~135px,
    <75px后撤着丢, <50px后跳拉开; 永不主动挥拳
  - 猴子是远程怪: 400px内直接远距丢镖狙杀, 无需接近
  (MP无限/HP自动回血模式: 无MP风筝与低血后撤逻辑)
用法: python3 bot/autoplay.py [--duration 90]
"""
import argparse
import json
import os
import random
import sys
import time
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from game.ipc import RemoteInputClient  # noqa: E402

VISION = "/tmp/mxd_vision.json"
R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

JUMP_REACH = 115      # 轻功跳高≈111px, 留余量(抓绳判定用)
LAYER_TOL = 50        # 世界y归层容差(小地图定位±10抖动)


def load_map():
    with open(f"{R}/data/map.json") as f:
        return json.load(f)


class Bot:
    def __init__(self, duration):
        self.remote = RemoteInputClient()
        self.map = load_map()
        self.ropes = self.map["ropes"]
        # 平台(按y聚合)
        plats = {}
        for fh in self.map["footholds"]:
            if abs(fh["y1"] - fh["y2"]) > 6:
                continue
            y = round((fh["y1"] + fh["y2"]) / 2)
            plats.setdefault(y, []).append((min(fh["x1"], fh["x2"]), max(fh["x1"], fh["x2"])))
        self.platforms = {y: (min(a for a, _ in v), max(b for _, b in v)) for y, v in plats.items()}
        self.platform_ys = sorted(self.platforms.keys())
        self.duration = duration
        self.state = "idle"
        self.state_t = 0.0
        self.attack_count = 0
        self.next_attack_at = 0.0
        self.next_hop_at = 0.0      # 后跳拉开冷却(战斗)
        self._last_nav_log = 0.0
        self.target = None
        self.target_until = 0.0
        self._hp_hist = []          # 撕裂帧防护: 中值滤波
        self._mp_hist = []
        self._hit_streak = 0
        self.jump_star_at = random.randint(3, 5)     # 第几次攻击跳标
        self.last_seen_player = None
        # 轻功管理
        self.next_haste_at = 0.0
        self._haste_tried = False
        self._skill_until = 0.0     # skill边缘持续发送截止时刻
        # 围殴脱离
        self.escape_mode = None     # jump / drop / vine
        self.escape_d = 0           # jump: ±1方向  vine: 藤蔓x
        self.escape_until = 0.0
        self.escape_jump_at = 0.0
        self.next_escape_at = 0.0
        self.dir = 0
        # ---- 分层巡逻导航 ----
        self._build_nav_graph()
        self.route = []             # 待执行hop序列
        self.transit_until = 0.0    # 整段导航承诺期
        self.hop_until = 0.0        # 单hop超时(卡住兜底)
        self.hop_cd = 0.0           # 抓绳/下跳动作冷却
        self._drop_tries = 0        # 当前hop的下跳尝试次数
        self.layer_since = time.time()
        self.layer_budget = random.uniform(9, 15)
        self.log = open(f"{R}/logs/bot.log", "a")
        self.t0 = time.time()

    def _nav_log(self, msg):
        now = time.time()
        if now - self._last_nav_log > 1.2:      # 同类导航日志限频
            self._last_nav_log = now
            self.logline(msg)

    def logline(self, msg):
        self.log.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        self.log.flush()

    # ---- 视觉输入 ----
    def read_vision(self):
        try:
            with open(VISION) as f:
                d = json.load(f)
            if time.time() - d.get("t", 0) > 1.0:
                return None
            return d
        except Exception:
            return None

    # ---- 地图知识(分层导航图) --------------------------------------------
    def layer_of(self, y):
        """世界y → 层索引(最近层代表y, 超出容差返回None=层间空中)。"""
        best, bd = None, 1e9
        for i, rep in enumerate(self.layer_rep):
            d = abs(y - rep)
            if d < bd:
                bd, best = d, i
        return best if bd <= LAYER_TOL else None

    def on_rope(self, px, py):
        """角色世界坐标是否在某条藤蔓上(几何判定, 攀爬检测用)。"""
        for r in self.ropes:
            top, bot = min(r["y1"], r["y2"]), max(r["y1"], r["y2"])
            if abs(px - r["x"]) <= 26 and top - 12 <= py <= bot + 12:
                return r
        return None

    def _build_nav_graph(self):
        # 1) 平台按y聚类成层
        groups = []
        for y in self.platform_ys:
            if groups and y - groups[-1][-1] <= 40:
                groups[-1].append(y)
            else:
                groups.append([y])
        self.layer_groups = groups
        self.layer_rep = [g[len(g) // 2] for g in groups]
        self.layer_span = {}
        self.layer_segs = {}                       # 每层全部 foothold 线段(并集会掩盖缺口)
        for i, g in enumerate(groups):
            segs = []
            for y in g:
                a, b = self.platforms[y]
                segs.append((a, b))
            self.layer_segs[i] = segs
            self.layer_span[i] = (min(a for a, _ in segs), max(b for _, b in segs))
        # 2) 藤蔓边: 绳上行(跳抓) + 顺绳滑下(绳顶站立按下)
        self.edges = {i: [] for i in range(len(groups))}
        for r in self.ropes:
            top, bot = min(r["y1"], r["y2"]), max(r["y1"], r["y2"])
            up_l = self.layer_of(top)
            if up_l is None:
                continue
            for i, rep in enumerate(self.layer_rep):
                # 上行边: 从"绳底可达层"跳抓(起点≠绳顶层)
                if i != up_l and rep - JUMP_REACH <= bot <= rep + 12:
                    self.edges[i].append({"kind": "rope_up", "to": up_l,
                                          "x": float(r["x"]), "top_y": top})
                # 下行边: 起点就是绳顶层(站绳顶按下→滑到底)
                if i == up_l and top - 12 <= rep <= bot + 12 and rep < bot - 4:
                    land = self._rope_landing(r["x"], bot)
                    if land is not None and land[1] != i:
                        self.edges[i].append({"kind": "rope_down", "to": land[1],
                                              "x": float(r["x"]), "land_y": land[0]})
        # 3) 平台下跳边: 只连"最近下一层"; 落点x取两层 foothold 线段交集的中点(避开藤蔓)
        order = sorted(range(len(self.layer_rep)), key=lambda i: self.layer_rep[i])
        for idx, i in enumerate(order):
            for j in order[idx + 1:]:
                if self.layer_rep[j] <= self.layer_rep[i]:
                    continue
                x = self._drop_x(i, j)
                if x is not None:
                    self.edges[i].append({"kind": "drop", "to": j, "x": x})
                break   # 只考虑相邻下一层

    def _drop_x(self, i, j, prefer=None):
        """层i→层j的下跳落点: 下层 foothold 与上层 foothold 真实重叠段的中点。"""
        best = None
        for a1, a2 in self.layer_segs[i]:
            for b1, b2 in self.layer_segs[j]:
                lo, hi = max(a1, b1), min(a2, b2)
                if hi - lo <= 30:
                    continue
                mid = (lo + hi) / 2
                width = hi - lo
                score = width
                if prefer is not None:
                    score -= abs(mid - prefer)      # 靠近当前位置优先
                if best is None or score > best[0]:
                    best = (score, lo, hi)
        if best is None:
            return None
        return self._avoid_ropes((best[1] + best[2]) / 2, best[1], best[2])

    def _rope_landing(self, x, rope_bot):
        """绳底(x, rope_bot)松手后的落点: 返回(落点平台y, 层索引)或None。"""
        best = None
        for i, g in enumerate(self.layer_groups):
            for py in g:
                a, b = self.platforms[py]
                if a - 10 <= x <= b + 10 and rope_bot - 25 <= py <= rope_bot + 90:
                    if best is None or py < best[0]:
                        best = (py, i)
                    break
        return best

    def _avoid_ropes(self, x, lo, hi):
        """落点x避开藤蔓±32px(防止下跳途中误抓绳/误判在绳)。"""
        for _ in range(8):
            hit = next((r for r in self.ropes if abs(r["x"] - x) < 32), None)
            if hit is None:
                return x
            if hit["x"] - 40 >= lo:
                x = hit["x"] - 40
            elif hit["x"] + 40 <= hi:
                x = hit["x"] + 40
            else:
                break
        return x

    def plan_route(self, src, dst):
        """BFS多跳路线: 返回hop列表(rope_up/rope_down/drop), 失败返回None。"""
        if src is None or dst is None or src == dst:
            return None
        prev = {src: None}
        q = deque([src])
        while q:
            u = q.popleft()
            if u == dst:
                break
            edges = list(self.edges.get(u, []))
            random.shuffle(edges)       # 同跳数路线随机选(滑绳/下跳都可能出现)
            for e in edges:
                v = e["to"]
                if v not in prev:
                    prev[v] = (u, dict(e))
                    q.append(v)
        if dst not in prev:
            return None
        hops = []
        cur = dst
        while prev[cur] is not None:
            u, e = prev[cur]
            hops.append(e)
            cur = u
        hops.reverse()
        return hops

    def choose_patrol_layer(self, mobs, cur):
        """巡逻目标层: 可见怪最多的其他层, 平局取最近。"""
        counts = {}
        for m in mobs:
            L = self.layer_of(m["y"])
            if L is None or L == cur:
                continue
            counts[L] = counts.get(L, 0) + 1
        if not counts:
            return None
        rep = self.layer_rep[cur]
        return max(counts, key=lambda L: (counts[L], -abs(self.layer_rep[L] - rep)))

    # ---- 导航执行 ---------------------------------------------------------
    def do_transit(self, px, py, cur):
        now = time.time()
        if now > self.transit_until:
            self.route = []
            self._nav_log("NAV 导航超时, 放弃本次换层")
            self.release_all()
            return
        hop = self.route[0]
        to_rep = self.layer_rep[hop["to"]]
        on = self.on_rope(px, py)
        # --- hop完成判定 ---
        done = False
        if hop["kind"] == "rope_up":
            # 已翻上目标平台(脚y≤绳顶+6): 跳一下彻底脱离绳子
            done = py <= hop["top_y"] + 6
        elif hop["kind"] == "rope_down":
            done = cur == hop["to"] and py >= hop["land_y"] - 20
        elif hop["kind"] == "drop":
            done = cur == hop["to"] and py >= to_rep - 20
        if done:
            self.route.pop(0)
            self._drop_tries = 0
            if hop["kind"] == "rope_down" and not self.route:
                # 绳底脱离: 0.7s保障期反复↓+跳, 防止单次jump边缘被丢帧吞掉后吊死在绳上
                self._dismount_until = now + 0.7
            self.send(jump=True)    # 脱绳/落地缓冲跳(空中/地面均无害)
            if self.route:
                self.hop_until = now + 12.0
                self._nav_log(f"NAV hop完成 → 层{hop['to']}(y={to_rep:.0f}), "
                              f"剩{len(self.route)}跳")
            else:
                self.layer_since = now
                self.layer_budget = random.uniform(9, 15)
                self._nav_log(f"PATROL 到达层{hop['to']}(y={to_rep:.0f}), "
                              f"驻留清怪{self.layer_budget:.1f}s")
            return
        if now > self.hop_until:
            self.route = []
            self._nav_log(f"NAV hop超时({hop['kind']}→层{hop['to']}), 放弃本次换层")
            self.release_all()
            return
        # --- hop动作 ---
        if hop["kind"] == "rope_up":
            if on is not None and abs(px - hop["x"]) <= 26:
                self.send(up=True)                       # 攀爬中: 持续↑
            elif abs(px - hop["x"]) > 8:
                d = 1 if hop["x"] > px else -1
                self.send(left=(d < 0), right=(d > 0))   # 走向藤蔓
            else:
                jump = now >= self.hop_cd
                self.send(up=True, jump=jump)            # 起跳抓绳(站绳底也可直接↑抓)
                if jump:
                    self.hop_cd = now + 0.8
                    self._nav_log(f"NAV 抓绳x={hop['x']:.0f} 上爬 → 层{hop['to']} "
                                  f"(y={py:.0f})")
        elif hop["kind"] == "rope_down":
            if on is not None:
                # 已在绳上(含悬挂): 按住↓滑到底(小地图±10抖动不可作为走位依据)
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
                self.send(down=True, jump=jump)          # ↓+跳 = 平台下跳
                if jump:
                    self.hop_cd = now + 0.85
                    self._drop_tries += 1
                    # 小地图量化误差±13px: 箭头对齐≠真身在平台上, 重试时左右偏移猎位
                    if self._drop_tries == 2:
                        hop["x"] -= 32
                    elif self._drop_tries == 3:
                        hop["x"] += 64
                    self._nav_log(f"NAV 平台下跳 → 层{hop['to']}(y={to_rep:.0f}) "
                                  f"第{self._drop_tries}次")
                    if self._drop_tries >= 4:
                        self.route = []                  # 落点无效等异常 → 快速重规划
                        self._nav_log("NAV 下跳多次未生效, 放弃本次换层")
                        self.release_all()
                        return

    def _start_patrol(self, cur, goal, others_n, now):
        route = self.plan_route(cur, goal)
        if not route:
            self._nav_log(f"NAV 无路可达层{goal}(y={self.layer_rep[goal]:.0f}) — 放弃")
            return False
        self.route = route
        self.transit_until = now + 7.0 * len(route) + 8.0
        self.hop_until = now + 12.0
        self._drop_tries = 0
        desc = " → ".join(f"层{h['to']}({h['kind']})" for h in route)
        self.logline(f"PATROL 换层 y{self.layer_rep[cur]:.0f}→y{self.layer_rep[goal]:.0f} "
                     f"(他层怪x{others_n}) 路线: {desc}")
        return True

    # ---- 围殴脱离(利用无敌时间跳离/换层) --------------------------------
    def _mob_count(self, mobs, y0, y1, x0, x1):
        return sum(1 for m in mobs if y0 <= m["y"] <= y1 and x0 <= m["x"] <= x1)

    def plan_escape(self, px, py, mobs):
        """被围殴时选脱身方式: ①朝怪少的一侧跳 ②下方更安全→平台下跳 ③上方更安全→爬藤。"""
        near = [m for m in mobs if abs(m["y"] - py) < 60 and abs(m["x"] - px) < 90]
        if not near:
            return False
        now = time.time()
        cx = sum(m["x"] for m in near) / len(near)
        away = -1 if cx > px else 1
        side_mobs = self._mob_count(mobs, py - 70, py + 70,
                                    px + (10 if away > 0 else -170),
                                    px + (170 if away > 0 else -10))
        lower = [y for y in self.platform_ys if y > py + 55]
        drop_ok = any(self.platforms[y][0] - 24 <= px <= self.platforms[y][1] + 24
                      for y in lower)
        below_safe = self._mob_count(mobs, py + 80, 400, -400, 1200) < len(near)
        rx = None
        for r in self.ropes:
            top, bot = min(r["y1"], r["y2"]), max(r["y1"], r["y2"])
            if bot + 72 >= py and top <= py - 60 and abs(r["x"] - px) < 140:
                if self._mob_count(mobs, -400, py - 80, -400, 1200) < len(near):
                    if rx is None or abs(r["x"] - px) < abs(rx - px):
                        rx = r["x"]
        if side_mobs == 0:
            mode, d = "jump", away
            self.logline(f"SURROUNDED x{len(near)} → 跳跃脱离向{'右' if d > 0 else '左'} "
                         f"(无敌期内跳离)")
        elif drop_ok and below_safe:
            mode, d = "drop", 0
            self.logline(f"SURROUNDED x{len(near)} → 平台下跳脱离 (下方更安全)")
        elif rx is not None:
            mode, d = "vine", rx
            self.logline(f"SURROUNDED x{len(near)} → 爬藤向上脱离 x={rx:.0f} (上方更安全)")
        else:
            mode, d = "jump", away
            self.logline(f"SURROUNDED x{len(near)} → 跳跃脱离向{'右' if d > 0 else '左'}")
        self.escape_mode, self.escape_d = mode, d
        self.escape_until = now + (4.0 if mode == "vine" else 2.6)
        self.escape_jump_at = 0.0
        self.next_escape_at = now + 6.0
        return True

    def do_escape(self, px, py):
        now = time.time()
        if now >= self.escape_until or self.escape_mode is None:
            self.escape_mode = None
            # 脱离可能已换层/换位 → 清空路线, 下一拍从实际位置重规划
            if self.route:
                self.route = []
                self._nav_log("NAV 脱离结束, 路线作废待重规划")
            self.release_all()
            return
        if self.escape_mode == "jump":
            d = self.escape_d
            self.send(left=(d < 0), right=(d > 0), jump=(now >= self.escape_jump_at))
            if now >= self.escape_jump_at:
                self.escape_jump_at = now + 0.55
        elif self.escape_mode == "drop":
            self.send(down=True, jump=(now >= self.escape_jump_at))
            if now >= self.escape_jump_at:
                self.escape_jump_at = now + 0.6
        elif self.escape_mode == "vine":
            if abs(px - self.escape_d) > 10:
                d = 1 if self.escape_d > px else -1
                self.send(left=(d < 0), right=(d > 0))
            else:
                self.send(up=True, jump=(now >= self.escape_jump_at))
                if now >= self.escape_jump_at:
                    self.escape_jump_at = now + 0.7

    # ---- 输出 ----
    def send(self, left=False, right=False, up=False, down=False, attack=False, jump=False,
             skill=False):
        # skill边缘持续重发0.25s: 游戏掉帧跳seq时单次边缘会丢(haste_cd=1s去重, 无副作用)
        if not skill and time.time() < self._skill_until:
            skill = True
        self.remote.send(left=left, right=right, up=up, down=down,
                         attack=attack, jump=jump, skill=skill)

    def release_all(self):
        self.send()

    # ---- 主循环 ----
    def run(self):
        self.logline(f"bot 启动, 运行{self.duration}s")
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
            # 撕裂帧防护: hp/mp取最近3帧中值, hit需连续2帧
            self._hp_hist = (self._hp_hist + [vis.get("hp_ratio", 1)])[-3:]
            self._mp_hist = (self._mp_hist + [vis.get("mp_ratio", 1)])[-3:]
            vis["hp_ratio"] = sorted(self._hp_hist)[len(self._hp_hist) // 2]
            vis["mp_ratio"] = sorted(self._mp_hist)[len(self._mp_hist) // 2]
            if vis.get("hit"):
                self._hit_streak += 1
            else:
                self._hit_streak = 0
            vis["hit"] = self._hit_streak >= 2
            # 视野状态快照(调试/取证)
            if now - getattr(self, "_last_view_log", 0) > 2.0:
                self._last_view_log = now
                wm = "world" if (vis.get("player_world") and vis.get("cam")) else "screen"
                ms = [(m["name"], int(m.get("wx", m["x"])), int(m.get("wy", m["y"]))) for m in vis.get("mob_world") or vis.get("mobs", [])]
                self.logline(f"VIEW 模式={wm} 我=({vis['player']['x']:.0f},{vis['player']['y']:.0f}) "
                             f"pw={vis.get('player_world')} 怪={ms[:5]}")
            # 世界坐标: 小地图定位(优先) → 相机换算; 无世界信息时用屏幕坐标
            if vis.get("player_world") and vis.get("cam"):
                self.world_mode = True
            else:
                self.world_mode = False
            # 轻功管理: 图标消失/闪烁(视觉判定) → 按A补放(人类化间隔≥8s)
            haste = vis.get("haste")
            if haste is None:
                # 兼容旧监控: 开局补放一次
                if not self._haste_tried and now - self.t0 > 2:
                    self._haste_tried = True
                    self._skill_until = now + 0.25
                    self.next_haste_at = now + 8
                    self.logline("轻功: 开局补放(监控无haste字段, 兼容模式)")
            elif haste.get("can_recast") and now >= self.next_haste_at:
                self._skill_until = now + 0.25
                self.next_haste_at = now + 8
                why = "图标消失" if not haste.get("present") else "图标闪烁中"
                self.logline(f"轻功补放 ({why})")
            self.tick(dt, vis)
            time.sleep(0.05)                       # ~20Hz 决策
        self.release_all()
        self.logline(f"bot 结束: 攻击{self.attack_count}次")
        self.log.close()

    def tick(self, dt, vis):
        now = time.time()
        self.state_t += dt
        p = vis["player"]
        px, py = p["x"], p["y"]
        self.last_seen_player = (px, py)
        # 导航与瞄准统一用世界坐标(小地图定位; 相机为纯平移, 方向与屏幕一致)
        # 小地图断供时用≤2.5s内的快照兜底(攀爬中箭头偶尔丢检), 断供太久则原地等待
        pw = vis.get("player_world")
        if pw and vis.get("cam") is not None:
            # 位置跳变合理性: 箭头偶发误检(文字橙色碎片)会造成瞬移假读数, 拒收
            prev = getattr(self, "_pw_prev", None)
            prev_t = getattr(self, "_pw_prev_t", 0.0)
            if prev and now - prev_t < 0.5:
                jump = abs(pw["x"] - prev["x"]) + abs(pw["y"] - prev["y"])
                if jump > 250:                       # 玩家极限速度~140px/s + 击退
                    pw = prev                        # 拒收瞬移假读数, 沿用上次
            self._pw_prev, self._pw_prev_t = dict(pw), now
            self._world_snap = (pw, vis["cam"], vis.get("mob_world", []), now)
        elif getattr(self, "_world_snap", None) is not None:
            pw2, cam2, mb2, t2 = self._world_snap
            if now - t2 <= 2.5:
                pw, vis["cam"], vis["mob_world"] = pw2, cam2, mb2
            else:
                pw = None
        if pw and vis.get("mob_world") is not None and vis.get("cam") is not None:
            px, py = pw["x"], pw["y"]
            mobs = []
            for m in vis["mob_world"]:
                if -320 < m["wy"] < 340 and -360 < m["wx"] < 1170:
                    mm = dict(m)
                    mm["x"] = m["wx"]            # 修正: mob_world的x原为屏幕坐标
                    mobs.append(mm)
        else:
            mobs = vis.get("mobs", [])
        world_ok = pw is not None
        hit = vis.get("hit", False)

        # 被围殴 → 脱离(最高优先级): ≥2只贴身, 或贴身+连续受击
        if now < self.escape_until and self.escape_mode is not None:
            self.do_escape(px, py)
            return
        near_mobs = [m for m in mobs if abs(m["y"] - py) < 60 and abs(m["x"] - px) < 90]
        # 赶路途中阈值更严(≥2只贴身且确实连续受击), 防止换层被追怪无限打断
        surrounded = len(near_mobs) >= 3 or (near_mobs and self._hit_streak >= 2
                                             and (len(near_mobs) >= 2 or not self.route))
        if surrounded and now >= self.next_escape_at:
            if self.plan_escape(px, py, mobs):
                return

        # 分层巡逻赶路中 → 专心执行路线
        cur = self.layer_of(py)
        if self.route:
            if not world_ok:
                # 世界坐标断供: 原地等待(在绳上挂着是安全的), 恢复后继续
                self.release_all()
                return
            if (cur is not None and cur == self.route[-1]["to"]
                    and self.on_rope(px, py) is None):
                self.route = []                   # 已提前到达整体目标层
                self.layer_since = now
                self.layer_budget = random.uniform(9, 15)
                self._nav_log(f"PATROL 提前到达层{cur}(y={self.layer_rep[cur]:.0f})")
                self.release_all()
                return
            self.do_transit(px, py, cur)
            return

        # 刚完成绳下行: 专用0.7s确保真正离绳(↓滑到底自动脱绳 + 跳边缘重发)
        if now < getattr(self, "_dismount_until", 0):
            self.send(down=True, jump=True)
            return

        # 目标选择: 同层(层判定+dy双保险)最近优先, 带2秒持久性防视觉抖动
        same = [m for m in mobs
                if (self.layer_of(m["y"]) == cur and abs(m["y"] - py) < 70)
                or (self.layer_of(m["y"]) is None and abs(m["y"] - py) < 60)]
        same = sorted(same, key=lambda m: abs(m["x"] - px))
        target = same[0] if same else None
        if target is None and self.target is not None and now < self.target_until:
            if abs(self.target.get("y", 9999) - py) < 90:
                target = self.target

        # 换层巡逻: 驻留超时(清完该逛下一层) 或 本层无怪但别层有怪
        others = [m for m in mobs
                  if self.layer_of(m["y"]) is not None and self.layer_of(m["y"]) != cur]
        if (world_ok and cur is not None and others
                and (target is None or now - self.layer_since >= self.layer_budget)):
            goal = self.choose_patrol_layer(mobs, cur)
            if goal is not None and self._start_patrol(cur, goal, len(others), now):
                self.do_transit(px, py, cur)
                return

        if target is None:
            self.release_all()
            self.state = "navigate"
            return

        # 有同层目标 → 战斗
        self.target = target
        self.target_until = now + 2.0
        dx = target["x"] - px
        dist = abs(dx)
        now = time.time()

        # 猴子为远程怪: 保持远距离即可丢镖命中, 无需接近; 其余怪接近到丢镖距离
        atk_max = 400 if target.get("name") == "猴子" else 135
        if dist > atk_max:
            d = 1 if dx > 0 else -1
            self.dir = d
            self.send(left=(d < 0), right=(d > 0))
            return

        d = 1 if dx > 0 else -1
        # 后撤站位(核心): 贴太近会被迫挥拳(伤害≈0)且被反复触碰 → 后撤丢镖;
        # 距离<50px 再后跳拉开. 永不主动挥拳.
        if dist < 75:
            away = -1 if dx > 0 else 1
            hop = dist < 50 and now >= self.next_hop_at
            if hop:
                self.next_hop_at = now + 0.8
            self.send(left=(away < 0), right=(away > 0), jump=hop)
        else:
            self.send(left=(d < 0), right=(d > 0))
        if now >= self.next_attack_at:
            self.attack_count += 1
            do_jump_star = (self.attack_count % self.jump_star_at == 0)
            if do_jump_star and random.random() < 0.8:
                # 跳标: 起跳后空中丢镖
                self.send(jump=True)
                self.logline(f"跳标! 目标{target['name']} dist={dist:.0f}")
                import threading
                threading.Timer(0.09, lambda: self.send(attack=True)).start()
            else:
                self.send(attack=True)
                tag = ("[后撤丢镖]" if dist < 75
                       else ("[远距狙猴]" if atk_max > 135 else ""))
                self.logline(f"攻击 #{self.attack_count} 目标{target['name']} "
                             f"{tag} dist={dist:.0f}")
            self.next_attack_at = now + random.uniform(0.55, 0.85)   # 人类化节奏


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=90)
    args = ap.parse_args()
    os.makedirs(f"{R}/logs", exist_ok=True)
    Bot(args.duration).run()


if __name__ == "__main__":
    main()
