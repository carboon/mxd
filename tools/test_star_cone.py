#!/usr/bin/env python3
"""离线验证双飞斩目标搜索/直线弹道 + 怪物真实体积(经典客户端规格)。

规格来源: NiaMeowDB MapleStory Classic — Lucky Seven 隐藏机制:
  步进楔形搜索(65px起, 1:4扩宽, 垂直封顶脚上124/脚下68, 水平400),
  弹道直线从出手点飞向锁定目标身体中心(上下平台时倾斜)。
体积: data/mobs.json body_w/body_h (石头人140x120, 其余~44-52)。
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
from game.player import Player
from game.sprites import load_mob_skins
from game.world import World

world = World()
skins = load_mob_skins()
mushroom = Mob(world, {"mobId": 2220100, "x": 0, "y": 0}, 2220100, skins)
golem = Mob(world, {"mobId": 5130101, "x": 0, "y": 0}, 5130101, skins)
print(f"小怪: {mushroom.name} hp={mushroom.hp} 身盒={mushroom.body_w}x{mushroom.body_h}")
print(f"大怪: {golem.name} hp={golem.hp} 身盒={golem.body_w}x{golem.body_h}")


class FakePlayer:
    def __init__(self, x, y, face=1):
        self.x, self.y, self.face, self.state = x, y, face, "stand"


def shot(mob, px, py, mx, my, expect):
    """玩家在(px,py) 朝右, 怪在(mx,my): 模拟一次双飞斩, 验证是否命中。"""
    mob.x, mob.y = float(mx), float(my)
    mob.hp, mob.state = mob.max_hp, "move"
    mob.die_t = -1.0                        # 复位死亡动画标记(上个用例可能打死过它)
    combat.stars.clear()
    combat.throw_star(FakePlayer(px, py))
    assert combat.stars, "应产生飞镖"
    hit = False
    for _ in range(120):                     # 模拟2秒(60fps)
        combat.update(1 / 60)
        if mob.hp < mob.max_hp or mob.state == "die":
            hit = True
            break
    ok = "✓" if hit == expect else "✗✗✗"
    print(f"{ok} [{mob.name}] ({px},{py})→({mx},{my}) 命中={hit} 预期={expect}")
    return hit == expect


combat = Combat(lambda: [mushroom])
allok = True
# --- 小怪(蓝蘑菇 h48): 同层窗口内可打, 超出索敌距离无锁定但直飞仍可物理命中 ---
allok &= shot(mushroom, 350, 305, 500, 305, True)    # dx=150
allok &= shot(mushroom, 100, 305, 700, 305, True)    # dx=600 无锁定水平直飞命中
# --- 站二层(26) 打三层(123): 相邻下层, 头部(脚+49)进入波段(脚下68)即可 ---
allok &= shot(mushroom, 150, 26, 450, 123, True)     # dx=300
allok &= shot(mushroom, 350, 26, 450, 123, False)    # dx=100 波段下沿37<49 近端够不到
# --- 站二层 打一层(-71): 相邻上层, 远端波段上沿≥97 才覆盖身体 ---
allok &= shot(mushroom, 150, 26, 600, -71, True)     # dx=450 远端 ✓ 倾斜向上
allok &= shot(mushroom, 250, 26, 450, -71, False)    # dx=200 够不到
# --- 跨两层(第三层↔地面): 小怪永远够不到 ---
allok &= shot(mushroom, 50, 123, 500, 305, False)
allok &= shot(mushroom, 200, 305, 600, 123, False)

# --- 石头人(h120): 体积大, 二层任何水平距离都能打到三层石头人(用户实测反馈) ---
combat = Combat(lambda: [golem])
allok &= shot(golem, 350, 26, 450, 123, True)        # dx=100 近端: 身顶-23已在波段内
allok &= shot(golem, 150, 26, 450, 123, True)        # dx=300
# 石头人头高(脚上120): 三层打地面石头人的头(头顶+62<脚下68)远端也可打
allok &= shot(golem, 250, 123, 500, 305, True)       # dx=250
allok &= shot(golem, 380, 123, 450, 305, False)      # dx=70 波段下沿35<62 够不到
# 地面打三层石头人: 跨两层向上仍够不到(身顶-302)
allok &= shot(golem, 200, 305, 600, 123, False)

# --- 触碰: 二层玩家与三层石头人身体重叠(玩家盒34x56, 石头人顶到达y=3<26) ---
golem.x, golem.y = 400.0, 123.0
p = Player.__new__(Player)
p.x, p.y = 430.0, 26.0
touch = golem.rect().colliderect(p.rect())
allok &= (touch is True)
print(f"{'✓' if touch else '✗✗✗'} 触碰: 二层玩家(430,26) vs 三层石头人(400,123) 碰撞={touch} 预期=True")

print("全部通过" if allok else "存在失败")

# --- 双飞斩伤害结算不超血量: 猴子1500吃两发1000 → 1000+500, 总额=1500 ---
print("--- 伤害结算回归 ---")
monkey = Mob(world, {"mobId": 3210800, "x": 0, "y": 0}, 3210800, skins)
nums = []
monkey.take_hit(1000, 0.0, type("FX", (), {"damage_number": staticmethod(lambda x, y, s: nums.append(int(s))),
                                           "miss_float": staticmethod(lambda *a: None)})())
monkey.take_hit(1000, 0.0, type("FX", (), {"damage_number": staticmethod(lambda x, y, s: nums.append(int(s))),
                                           "miss_float": staticmethod(lambda *a: None)})())
ok3 = (nums == [1000, 500] and monkey.state == "die")
allok &= ok3
print(f"{'✓' if ok3 else '✗✗✗'} 猴子两发结算={nums} (预期[1000, 500]), 总额{sum(nums)}==max_hp{monkey.max_hp}")

sys.exit(0 if allok else 1)
