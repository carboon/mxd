#!/usr/bin/env python3
"""离线验证双飞斩目标搜索/直线弹道(经典客户端规格, 不启动游戏循环)。

规格来源: NiaMeowDB MapleStory Classic — Lucky Seven 隐藏机制:
  步进楔形搜索(65px起, 1:4扩宽, 垂直封顶脚上124/脚下68, 水平400),
  弹道直线从出手点飞向锁定目标身体中心(上下平台时倾斜)。
"""
import os

os.environ["SDL_VIDEODRIVER"] = "dummy"
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pygame

pygame.init()
pygame.display.set_mode((64, 64))

from game.combat import Combat
from game.mob import Mob
from game.sprites import load_mob_skins
from game.world import World

world = World()
skins = load_mob_skins()
spawn = world.spawns[0]
mob = Mob(world, spawn, spawn["mobId"], skins)
print(f"测试怪: {mob.name} hp={mob.hp} 身高44")


class FakePlayer:
    def __init__(self, x, y, face=1):
        self.x, self.y, self.face, self.state = x, y, face, "stand"


combat = Combat(lambda: [mob])


def shot(px, py, mx, my, expect):
    """玩家在(px,py) 朝右, 怪在(mx,my): 模拟一次双飞斩, 验证是否命中。"""
    mob.x, mob.y = float(mx), float(my)
    mob.hp, mob.state = mob.max_hp, "move"
    combat.stars.clear()
    combat.throw_star(FakePlayer(px, py))
    assert combat.stars, "应产生飞镖"
    n_stars = len(combat.stars)
    hit = False
    for _ in range(120):                     # 模拟2秒(60fps)
        combat.update(1 / 60)
        if mob.hp < mob.max_hp or mob.state == "die":
            hit = True
            break
    ok = "✓" if hit == expect else "✗✗✗"
    tilt = f" 倾斜角={__import__('math').degrees(__import__('math').atan2(combat.stars[0].vy, 760)):.1f}°" if combat.stars else ""
    print(f"{ok} ({px},{py})→({mx},{my}) 命中={hit} 预期={expect} 镖数={n_stars}{tilt}")
    return hit == expect


allok = True
# --- 同层: 楔形窗口内(65~465px)全可打, 超出索敌距离不打 ---
allok &= shot(350, 305, 500, 305, True)      # dx=150
allok &= shot(100, 305, 450, 305, True)      # dx=350
allok &= shot(100, 305, 700, 305, True)      # dx=600 超出索敌距离, 无锁定水平直飞
#                                             但同层怪在弹道路径上仍会被物理命中(经典行为)
# --- 站第二层(26) 打第三层(123): 相邻下层, 头部(脚+53)进入波段(脚下68)即可 ---
allok &= shot(150, 26, 450, 123, True)       # dx=300 波段下沿86 ≥ 头53 ✓
allok &= shot(350, 26, 450, 123, False)      # dx=100 波段下沿37 < 头53 近端够不到
# --- 站第二层(26) 打第一层(-71): 相邻上层, 远端波段上沿≥97+44 才覆盖身体 ---
allok &= shot(150, 26, 600, -71, True)       # dx=450 远端 ✓ 倾斜向上
allok &= shot(250, 26, 450, -71, False)      # dx=200 波段上沿65 < 97 够不到
# --- 站第三层(123) 打地面(305): 跨两层, 头顶+150 > 脚下68 永远够不到 ---
allok &= shot(50, 123, 500, 305, False)
allok &= shot(50, 123, 515, 305, False)      # dx=465 最大索敌距离也不行
# --- 站地面 打第三层(123): 跨两层向上, 同样够不到 ---
allok &= shot(200, 305, 600, 123, False)
print("全部通过" if allok else "存在失败")
sys.exit(0 if allok else 1)
