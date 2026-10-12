#!/usr/bin/env python3
"""系统3：模拟人类操作的自动战斗 bot（基于系统2的视觉识别结果）。

不读取游戏内部状态，只看 /tmp/mxd_vision.json（视觉输出）与地图公开数据
(平台/藤蔓，等价于玩家对地图的熟悉)。通过虚拟键盘注入操作。

行为:
  - 轻功管理: 图标消失/闪烁(视觉) → 按A补放(人类化间隔≥8s)
  - 被围殴脱离: ≥2只怪贴身(或贴身+连续受击) → 利用无敌时间
    ①朝怪少的一侧跳跃脱离 ②下方更安全则平台下跳 ③上方更安全则爬藤离层
  - 选择同层最近怪 → 走近 → 双飞斩(人类化反应延迟0.2-0.4s)
  - 每3-5次攻击插入"跳标"(跳起后空中丢镖, 标飞标志性操作)
  - 目标在其他层 → 走到藤蔓处攀爬 / 平台下跳
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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from game.ipc import RemoteInputClient  # noqa: E402

VISION = "/tmp/mxd_vision.json"
R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
        self.next_climb_jump_at = 0.0
        self._last_nav_log = 0.0
        self.target = None
        self.target_until = 0.0
        self.climbing_until = 0.0   # 攀爬承诺期: 期间只按↑(忽略小地图x抖动)
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
        self.next_hop_at = 0.0      # 后跳拉开冷却
        self.dir = 0
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

    # ---- 地图知识(平台关系) ----
    def nearest_platform_y(self, y):
        return min(self.platform_ys, key=lambda py: abs(py - y))

    def on_rope(self, px, py):
        """角色世界坐标是否在某条藤蔓上。"""
        for r in self.ropes:
            top, bot = min(r["y1"], r["y2"]), max(r["y1"], r["y2"])
            if abs(px - r["x"]) <= 26 and top - 12 <= py <= bot + 12:
                return r
        return None

    def rope_to(self, y_from, y_to):
        """找从 y_from 层向上通往 y_to 层的藤蔓x(顶部可达目标层, 底部起跳可抓)。"""
        if y_to >= y_from:
            return None
        for r in self.ropes:
            top, bot = min(r["y1"], r["y2"]), max(r["y1"], r["y2"])
            if top <= y_to + 40 and bot + 72 >= y_from:   # 72 = 跳跃高度
                return r["x"]
        return None

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
        pw = vis.get("player_world")
        if pw and vis.get("mob_world"):
            px, py = pw["x"], pw["y"]
            mobs = []
            for m in vis.get("mob_world", []):
                if -300 < m["wy"] < 340 and -360 < m["wx"] < 1170:
                    mm = dict(m)
                    mm["x"] = m["wx"]            # 修正: mob_world的x原为屏幕坐标
                    mobs.append(mm)
        else:
            mobs = vis.get("mobs", [])
        hit = vis.get("hit", False)

        # 目标选择: 同层最近优先(带2秒持久性, 防止视觉抖动导致目标翻转)
        same = sorted([m for m in mobs if abs(m["y"] - py) < 70],
                      key=lambda m: abs(m["x"] - px))
        target = same[0] if same else None
        if target is None and self.target is not None and now < self.target_until:
            if abs(self.target.get("y", 9999) - py) < 90:
                target = self.target

        # 低血后撤已移除(需求变更: HP自动补血, 不考虑死亡场景)

        # 被围殴 → 脱离(优先级高于找目标): ≥2只贴身, 或贴身+连续受击
        if now < self.escape_until and self.escape_mode is not None:
            self.do_escape(px, py)
            return
        near_mobs = [m for m in mobs if abs(m["y"] - py) < 60 and abs(m["x"] - px) < 90]
        surrounded = len(near_mobs) >= 2 or (near_mobs and self._hit_streak >= 3)
        if surrounded and now >= self.next_escape_at:
            if self.plan_escape(px, py, mobs):
                return

        # 无同层目标 → 导航(攀爬/下跳)
        if target is None:
            self.release_all()
            if mobs:
                m = min(mobs, key=lambda m: abs(m["y"] - py))
                dy = m["y"] - py
                if dy < -50:
                    # 已在藤蔓上: 持续↑攀爬(小地图x有±10抖动, 不做位置重判)
                    rope = self.on_rope(px, py)
                    if rope is not None:
                        self.climbing_until = now + 0.9      # 持续续期
                        top = min(rope["y1"], rope["y2"])
                        t_same = [mm for mm in mobs if abs(mm["y"] - py) < 70]
                        if py <= top + 16 and t_same:
                            # 顶端且同层有怪 → 跳离藤蔓
                            t2 = min(t_same, key=lambda mm: abs(mm["x"] - px))
                            d = 1 if t2["x"] > px else -1
                            self.send(left=(d < 0), right=(d > 0),
                                      jump=(now >= self.next_climb_jump_at))
                            if now >= self.next_climb_jump_at:
                                self.next_climb_jump_at = now + 0.7
                                self._nav_log(f"NAV 藤蔓顶端跳离, 落向同层目标 (y={py:.0f})")
                        else:
                            self.send(up=True)
                            self._nav_log(f"NAV 攀爬中 (y={py:.0f} → 目标{m['y']:.0f})")
                        return
                    # 目标在上层: 走到藤蔓 → 跳+↑抓绳攀爬
                    rx = self.rope_to(py, m["y"])
                    if rx is None:
                        self.release_all()
                        return
                    if now < self.climbing_until:
                        # 攀爬承诺期: 只按↑(小地图x有±10抖动, 不可靠)
                        self.send(up=True)
                        return
                    if abs(px - rx) > 8:
                        d = 1 if rx > px else -1
                        self.send(left=(d < 0), right=(d > 0))
                        self._nav_log(f"NAV 目标在上层, 走向藤蔓x={rx:.0f} (y={py:.0f})")
                    else:
                        # 到达藤蔓处: 起跳抓绳, 承诺3.5s攀爬
                        self.climbing_until = now + 3.5
                        self.send(up=True, jump=True)
                        self._nav_log(f"NAV 抓绳攀爬 (px={px:.0f} 绳{rx:.0f} y={py:.0f} → 目标{m['y']:.0f})")
                elif dy > 50:
                    # 目标在下方: 下+跳 = 平台下跳(底层自动拒绝则走下台阶)
                    self.send(down=True, jump=(now >= self.next_climb_jump_at))
                    if now >= self.next_climb_jump_at:
                        self.next_climb_jump_at = now + 0.7
                        self._nav_log(f"NAV 目标在下层, 下跳/走下台阶 (y={py:.0f} → 目标{m['y']:.0f})")
            self.state = "navigate"
            return

        # 有同层目标
        self.target = target
        self.target_until = now + 2.0
        dx = target["x"] - px
        dist = abs(dx)
        # 刚爬完藤蔓还在绳上 → 跳离绳子落向目标层
        if now < self.climbing_until:
            self.send(left=(dx < 0), right=(dx > 0), jump=(now >= self.next_climb_jump_at))
            if now >= self.next_climb_jump_at:
                self.next_climb_jump_at = now + 0.7
                self._nav_log(f"NAV 藤蔓顶端跳离, 落向目标层 (y={py:.0f})")
            return
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
