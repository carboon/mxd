#!/usr/bin/env python3
"""系统1交互测试套件：模拟操作并断言每个行为符合预期。
用法: python3 tools/test_game.py   （全部通过打印 ALL TESTS PASSED）"""
import os
import sys

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["MXD_IPC"] = ""
os.environ["MXD_MISS_RATE"] = "0"      # 命中确定性(MISS用例中单独改C.PLAYER_MISS_RATE)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame  # noqa: E402

from game import ipc, config as C  # noqa: E402
from game.main import Game  # noqa: E402
from game.ui import _cn_font  # noqa: E402

ipc.cleanup_stale()
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" for _ in [0]).__next__() if detail else ("PASS " if cond else "FAIL ") + name)


class Harness:
    """封装：重置玩家到指定状态、步进模拟。"""

    def __init__(self):
        self.g = Game(headless=True)
        self.g.plate_font = _cn_font(12)
        self.g.badge_font = _cn_font(11)
        self.keys = {pygame.K_LEFT: False, pygame.K_RIGHT: False,
                     pygame.K_UP: False, pygame.K_DOWN: False}

    def place(self, x, y):
        p = self.g.player
        p.x, p.y = float(x), float(y)
        p.fh_id = self.g.world.fh_id_at_y(x, y)
        p.on_ground = True
        p.vy = 0.0
        p.inv_t = 0.0
        p.cooldown = 0.0
        p.kb_t = 0.0
        p.hp = C.PLAYER_HP
        p.state = "stand"
        p.climb = None
        # 清场: 把附近怪物挪远, 避免受击干扰交互测试
        for i, m in enumerate(self.g.spawns.mobs):
            if abs(m.x - x) < 260:
                m.x = x + 420 + (i % 3) * 60

    def run(self, frames, events_at=None, hold=None):
        events_at = events_at or {}
        hold = hold or {}
        for k, v in hold.items():
            self.keys[k] = v
        for i in range(frames):
            ev = events_at.get(i, set())
            self.g.update(1 / 60, self.keys, ev)
            self.g.draw()
        self.release()

    def release(self):
        for k in self.keys:
            self.keys[k] = False

    def close(self):
        try:
            if self.g.publisher:
                self.g.publisher.close()
            if self.g.remote:
                self.g.remote.close()
        except Exception:
            pass


h = Harness()
try:
    # 1 行走: 速度与平台保持
    h.place(-20, 305)
    x0 = h.g.player.x
    h.run(60, hold={pygame.K_RIGHT: True})
    dx = h.g.player.x - x0
    check("行走速度≈140px/s", abs(dx - 140) <= 6, f"dx={dx:.1f}")
    check("行走保持在地面y=305", h.g.player.y == 305 and h.g.player.on_ground)

    # 2 走到平台边缘会掉落(中层平台右端 x=738, 下方是305地面)
    h.place(720, 121)
    h.run(90, hold={pygame.K_RIGHT: True})
    check("走出平台边缘后掉落到下层", h.g.player.on_ground and abs(h.g.player.y - 305) < 4,
          f"x={h.g.player.x:.0f} y={h.g.player.y:.0f}")

    # 3 跳跃: 落回原平台
    h.place(100, 305)
    h.run(1, events_at={0: {"jump"}})
    apex = 305.0
    landed = False
    for i in range(70):
        h.g.update(1 / 60, h.keys, set())
        apex = min(apex, h.g.player.y)
        if i > 5 and h.g.player.on_ground:
            landed = True
            break
    check("跳跃高度≈72px(60fps半隐式积分)", abs((305 - apex) - 72) <= 5, f"apex={apex:.0f}")
    check("跳跃后落回地面", landed and h.g.player.y == 305)

    # 4 下跳: 中层平台穿透, 底层拒绝
    h.place(300, 121)
    nid = h.g.world.fh_id_at_y(300, 121)
    check("中层平台foothold存在", nid is not None)
    h.keys[pygame.K_DOWN] = True
    h.run(1, events_at={0: {"jump"}})
    h.keys[pygame.K_DOWN] = False
    h.run(80)
    check("中层下跳落到y=305", h.g.player.on_ground and abs(h.g.player.y - 305) < 4,
          f"y={h.g.player.y:.0f}")
    h.place(358, 305)
    h.keys[pygame.K_DOWN] = True
    h.run(1, events_at={0: {"jump"}})
    h.keys[pygame.K_DOWN] = False
    h.run(50)
    check("底层地面禁止下跳", h.g.player.on_ground and h.g.player.y == 305)

    # 5 趴下: 无更低平台时按下 → prone
    h.place(358, 305)
    h.run(2, hold={pygame.K_DOWN: True})
    check("按↓趴下(prone)", h.g.player.state == "prone")
    h.release()

    # 6 爬藤: 跳跃中抓绳 + 上爬 + 顶部脱离
    h.place(637, 305)
    h.keys[pygame.K_UP] = True
    h.run(1, events_at={0: {"jump"}})
    attached = False
    for i in range(60):
        h.g.update(1 / 60, h.keys, set())   # UP保持按住(run已自动松键,这里重按)
        h.keys[pygame.K_UP] = True
        if h.g.player.climb is not None:
            attached = True
            break
    check("跳跃中抓藤蔓", attached)
    if attached:
        h.run(120, hold={pygame.K_UP: True})
        check("爬到绳顶并站上平台", h.g.player.on_ground and h.g.player.y < 130,
              f"y={h.g.player.y:.0f}")
    h.release()

    # 7 双飞斩: 2发飞镖/冷却/空中可放
    h.place(400, 305)
    h.g.player.mp = 200
    h.run(1, events_at={0: {"attack"}})
    h.run(10)
    n1 = len(h.g.combat.stars)
    cd = h.g.player.cooldown
    check("双飞斩进入冷却≈0.6s", 0.4 <= cd <= 0.62, f"cd={cd:.2f}")
    h.place(400, 250)   # 空中
    h.g.player.on_ground = False
    h.g.player.cooldown = 0
    h.g.player.mp = 200
    h.run(1, events_at={0: {"attack"}})
    h.run(10)
    check("空中可放双飞斩", len(h.g.combat.stars) + len(h.g.combat.slash_fx) >= 1)

    # 8 挥拳: 近身有怪 → 挥拳(与MP无关, 微量伤害+吸引仇恨)
    h.place(400, 305)
    h.g.player.mp = 0              # 刻意0 MP: 挥拳不消耗MP也不依赖MP
    h.g.player.cooldown = 0
    h.g.player.inv_t = 5.0         # 无敌防触碰干扰
    h.g.player.state = "stand"
    h.g.combat.stars = []          # 清掉上一用例还在飞的镖
    punch_mob = None
    for m in h.g.spawns.mobs:
        if m.state != "die" and m.mob_id != 3000004:
            punch_mob = m
            break
    if punch_mob:
        punch_mob.x, punch_mob.y = h.g.player.x + 40, 305   # 近身40px(<55)
        hp0, aggro0 = punch_mob.hp, punch_mob.aggro_t
        h.run(1, events_at={0: {"attack"}})
        check("近身→挥拳(无飞镖+近战特效)", len(h.g.combat.stars) == 0 and len(h.g.combat.slash_fx) >= 1,
              f"stars={len(h.g.combat.stars)} slash={len(h.g.combat.slash_fx)}")
        check("挥拳微量伤害(C.PUNCH_DAMAGE)", punch_mob.hp == hp0 - C.PUNCH_DAMAGE,
              f"{hp0}->{punch_mob.hp}")
        check("挥拳吸引仇恨(aggro+面向玩家)", punch_mob.aggro_t > aggro0 and punch_mob.face == -1,
              f"aggro={punch_mob.aggro_t:.1f} face={punch_mob.face}")
    else:
        check("存在近身挥拳目标", False)
    # 负例: 非近身(挥拳范围外) → 走双飞斩路径, 挥拳仅近身触发(MP无关)
    h.place(400, 305)
    h.g.player.mp = 0
    h.g.player.cooldown = 0
    h.g.player.inv_t = 5.0
    h.g.combat.stars = []
    h.g.combat.slash_fx = []
    far_mob = None
    for m in h.g.spawns.mobs:
        if m.state != "die":
            far_mob = m
            break
    if far_mob:
        far_mob.x, far_mob.y = h.g.player.x + 200, 305
    h.run(1, events_at={0: {"attack"}})
    h.run(10)                     # 0.16s: 飞镖在途中(尚未命中200px外的怪)
    check("非近身→双飞斩(挥拳仅近身触发, MP无关)", len(h.g.combat.stars) >= 1,
          f"stars={len(h.g.combat.stars)}")

    # 9 战斗: 石头人2次双飞斩(4发)击杀, 蓝蘑菇1次(2发)击杀
    h.place(515, 305)
    golem = None
    for m in h.g.spawns.mobs:
        if m.mob_id == 5130101 and m.state != "die":
            golem = m
            break
    check("存在石头人", golem is not None)
    if golem:
        golem.x, golem.y = 585, 305     # 距离80px: 挥拳范围(55)外, 走飞镖判定
        h.g.player.x, h.g.player.y = 505, 305
        h.g.player.face = 1
        h.g.player.mp = 200
        casts = 0
        while golem.state != "die" and casts < 5:
            h.g.player.cooldown = 0
            h.g.player.inv_t = 0
            h.g.player.state = "stand"
            h.run(1, events_at={0: {"attack"}})
            h.run(20)
            casts += 1
        check(f"石头人{casts}次双飞斩击杀(≤2)", golem.state == "die" and casts <= 2,
              f"casts={casts}")
    mush = None
    for m in h.g.spawns.mobs:
        if m.mob_id == 2220100:
            mush = m
            break
    check("存在蓝蘑菇", mush is not None)
    if mush:
        mush.x, mush.y = h.g.player.x + 90, h.g.player.y
        h.g.player.face = 1
        h.g.player.mp = 200
        h.g.player.cooldown = 0
        h.run(1, events_at={0: {"attack"}})
        h.run(25)
        check("蓝蘑菇1次双飞斩击杀", mush.state == "die")

    # 10 怪物仇恨: 受击后朝玩家移动
    h.place(0, 305)
    m0 = None
    for m in h.g.spawns.mobs:
        if m.state != "die" and abs(m.y - 305) < 20 and m.mob_id != 3000004:
            m0 = m
            break
    if m0:
        m0.x = 120
        m0.y = 305
        m0.take_hit(100, 0, h.g.fx)
        x_before = m0.x
        h.run(30)
        moved_to_player = (m0.x < x_before) if x_before > 0 else (m0.x > x_before)
        check("怪物受击后仇恨朝玩家移动", moved_to_player, f"x {x_before:.0f}->{m0.x:.0f}")

    # 11 玩家受击: 击退+无敌闪烁+伤害飘字 (MISS率已在文件头固定为0, 确定性命中)
    h.place(0, 305)
    hp0 = h.g.player.hp
    mob = None
    for m in h.g.spawns.mobs:
        if m.state != "die" and abs(m.y - 305) < 30:
            mob = m
            break
    if mob:
        mob.x = h.g.player.x + 20
        mob.y = 305
        kb0 = h.g.player.x
        h.run(20)
        check("触碰伤害扣血", h.g.player.hp < hp0, f"{hp0}->{h.g.player.hp}")
        check("受击获得无敌时间", h.g.player.inv_t > 0)
        check("受击击退位移", abs(h.g.player.x - kb0) > 10)
        check("击退方向远离怪物(怪在右→向左弹)", h.g.player.x < kb0 - 8,
              f"x {kb0:.0f}->{h.g.player.x:.0f}")
    else:
        check("存在可触碰的怪物", False)

    # 11b 击退方向规则(原版逻辑: 远离怪物; 与怪同x时退向背后=与朝向相反)
    h.place(0, 305)
    p = h.g.player
    p.take_hit(10, p.x - 30)
    check("击退方向: 怪在左→向右弹", p.vx > 0 and p.kb_t > 0, f"vx={p.vx}")
    p.inv_t = 0
    p.kb_t = 0
    p.take_hit(10, p.x + 30)
    check("击退方向: 怪在右→向左弹", p.vx < 0, f"vx={p.vx}")
    p.inv_t = 0
    p.kb_t = 0
    p.face = 1
    p.take_hit(10, p.x)
    check("击退方向: 与怪同x→退向背后(朝向反向)", p.vx < 0 and p.face == 1, f"vx={p.vx}")
    p.inv_t = 0
    p.kb_t = 0

    # 11c 触碰MISS: 强制100%MISS → 不掉血/不击退/不硬直, 头顶漂浮MISS图案
    from game import combat as combat_mod
    h.place(0, 305)
    miss_mob = None
    for m in h.g.spawns.mobs:
        if m.state != "die" and abs(m.y - 305) < 30:
            miss_mob = m
            break
    if miss_mob:
        miss_mob.x = h.g.player.x + 26
        miss_mob.y = 305
        miss_mob.touch_cd = 0
        hp0 = h.g.player.hp
        C.PLAYER_MISS_RATE = 1.0                     # 强制全MISS
        combat_mod.global_fx.misses.clear()
        h.run(3)
        check("MISS: 不掉血(仅自然回血)", h.g.player.hp >= hp0, f"{hp0}->{h.g.player.hp}")
        check("MISS: 不击退不硬直", h.g.player.kb_t <= 0 and h.g.player.inv_t <= 0,
              f"kb={h.g.player.kb_t:.2f} inv={h.g.player.inv_t:.2f}")
        check("MISS: 头顶漂浮MISS图案", len(combat_mod.global_fx.misses) >= 1,
              f"misses={len(combat_mod.global_fx.misses)}")
        C.PLAYER_MISS_RATE = 0.0                     # 强制全命中
        miss_mob.touch_cd = 0
        h.g.player.inv_t = 0
        h.g.player.kb_t = 0
        hp1 = h.g.player.hp
        x0 = h.g.player.x
        h.run(3)
        check("非MISS(命中): 掉血+向远离怪方向击退", h.g.player.hp < hp1 and h.g.player.x < x0 - 5,
              f"hp{hp1}->{h.g.player.hp} x{x0:.0f}->{h.g.player.x:.0f}")
    else:
        check("存在MISS测试用怪", False)

    # 11d 轻功(A键/skill): 不耗魔(MP无限), 持续180s; 满速140%=196px/s; 满跳120%≈104px; 闪烁/补放/图标
    h.place(400, 305)
    h.g.player.haste_t = 0
    h.g.player.haste_cd = 0
    h.g.player.mp = 200
    h.run(1, events_at={0: {"skill"}})
    check("轻功释放: 不耗MP, 持续180秒", abs(h.g.player.haste_t - 180) < 0.2
          and abs(h.g.player.mp - 200) <= 0.5,
          f"haste={h.g.player.haste_t:.1f} mp={h.g.player.mp}")
    x0 = h.g.player.x
    h.run(60, hold={pygame.K_RIGHT: True})
    dx = h.g.player.x - x0
    check("轻功满速196px/s(140%)", abs(dx - 196) <= 8, f"dx={dx:.1f}")
    h.place(100, 305)
    h.run(1, events_at={0: {"jump"}})
    apex = 305.0
    for i in range(80):
        h.g.update(1 / 60, h.keys, set())
        apex = min(apex, h.g.player.y)
        if i > 10 and h.g.player.on_ground:
            break
    check("轻功满跳apex≈104px(120%)", abs((305 - apex) - 104) <= 8, f"apex={apex:.0f}")
    mp_before = h.g.player.mp                        # 持续期(未闪烁)重复按A → 拒绝
    h.run(1, events_at={0: {"skill"}})
    check("持续期(未闪烁)不可重复释放", h.g.player.haste_t < 179.5
          and abs(h.g.player.mp - mp_before) <= 0.2,
          f"haste={h.g.player.haste_t:.1f} mp={h.g.player.mp}(前{mp_before:.1f})")
    h.g.player.haste_t = 10                          # 进入闪烁期 → 可补放
    h.run(1, events_at={0: {"skill"}})
    check("闪烁期(剩<15s)可补放→重置180s", abs(h.g.player.haste_t - 180) < 0.2,
          f"haste={h.g.player.haste_t:.1f}")
    h.g.player.haste_t = 0                           # 刚放过(1s公共冷却) → 拒绝
    h.g.player.haste_cd = 1.0
    h.run(1, events_at={0: {"skill"}})
    check("释放冷却期内不可重复释放", h.g.player.haste_t == 0, f"haste={h.g.player.haste_t}")
    # buff图标(右上角): 有buff可见 / 闪烁相位隐藏 / 过期消失
    surf = pygame.Surface((C.VIEW_W, C.VIEW_H))
    ix, iy = C.HASTE_ICON_POS
    h.g.player.haste_t = 100
    surf.fill((0, 0, 0))
    h.g.ui.draw_buffs(surf, h.g.player)
    c0 = surf.get_at((ix + 13, iy + 13))
    check("轻功图标显示(右上角青色翅膀)", c0.b > 200 and c0.g > 150, f"{c0}")
    h.g.player.haste_t = 2.7                         # int(2.7*4)=10 → 隐藏相位
    surf.fill((0, 0, 0))
    h.g.ui.draw_buffs(surf, h.g.player)
    c1 = surf.get_at((ix + 13, iy + 13))
    check("轻功剩余<15s闪烁(半相位隐藏)", c1 == (0, 0, 0, 255), f"{c1}")
    h.g.player.haste_t = 0
    surf.fill((0, 0, 0))
    h.g.ui.draw_buffs(surf, h.g.player)
    c2 = surf.get_at((ix + 13, iy + 13))
    check("轻功过期图标消失", c2 == (0, 0, 0, 255), f"{c2}")

    # 12 死亡→EXP→日志→7s重生→F9清零
    import time as _t
    h.place(-98, 305)
    target = None
    for m in h.g.spawns.mobs:
        if m.mob_id == 2220100 and m.state != "die":
            target = m
            break
    check("存在蓝蘑菇目标(重生后)", target is not None)
    if target:
        target.x = h.g.player.x + 90
        target.y = 305
        exp0 = h.g.session.exp
        h.g.player.face = 1
        h.g.player.mp = 200
        h.g.player.cooldown = 0
        h.g.player.inv_t = 0
        h.g.player.state = "stand"
        h.run(1, events_at={0: {"attack"}})
        h.run(30)
        check("击杀蓝蘑菇EXP+32", h.g.session.exp >= exp0 + 32,
              f"+{h.g.session.exp - exp0}")
        total0 = len(h.g.spawns.mobs) + len(h.g.spawns.pending)
        check("怪物总数恒定15", total0 == 15, f"{total0}")
        pending0 = len(h.g.spawns.pending)
        h.run(int(7.2 * 60))
        check("7秒后重生完成", len(h.g.spawns.mobs) + len(h.g.spawns.pending) == 15
              and len(h.g.spawns.pending) < pending0 + 1)
    log_exists = os.path.exists(C.KILL_LOG)
    exp_before = h.g.session.exp
    h.g.session.reset()
    check("F9清零: EXP归零", h.g.session.exp == 0)
    check("F9清零: 日志重建", os.path.exists(C.KILL_LOG))
finally:
    h.close()

fails = [r for r in RESULTS if not r[1]]
print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} 项通过")
if fails:
    print("失败项:")
    for name, _, detail in fails:
        print(" -", name, detail)
    sys.exit(1)
print("ALL TESTS PASSED")
