#!/usr/bin/env python3
"""离线验证飞镖扇形判定(不启动游戏循环)。"""
import os

os.environ["SDL_VIDEODRIVER"] = "dummy"
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pygame

pygame.init()
pygame.display.set_mode((64, 64))

from game import config as C
from game.combat import Combat
from game.mob import Mob
from game.sprites import load_mob_skins
from game.world import World

world = World()
skins = load_mob_skins()
spawn = world.spawns[0]
mob_id = spawn["mobId"]
mob = Mob(world, spawn, mob_id, skins)
print(f"测试怪: {mob.name} hp={mob.hp}")

combat = Combat(lambda: [mob])


def shot(throw_x, throw_y, mob_x, mob_y, expect):
    mob.x, mob.y = float(mob_x), float(mob_y)
    mob.hp = mob.max_hp
    mob.state = "move"
    combat.stars.clear()
    combat.stars.append(__import__("game.combat", fromlist=["Star"]).Star(
        throw_x, throw_y, 1))
    hit = False
    for _ in range(240):                     # 模拟2秒(60fps)
        combat.update(1 / 60)
        if mob.hp < mob.max_hp or mob.state == "die":
            hit = True
            break
    ok = "✓" if hit == expect else "✗✗✗"
    print(f"{ok} 从({throw_x},{throw_y})打({mob_x},{mob_y}) 命中={hit} 预期={expect}")
    return hit == expect


star_cls_y_off = 38
allok = True
# 同层: 近/中/远
allok &= shot(350, 305 - star_cls_y_off, 500, 305, True)
allok &= shot(100, 305 - star_cls_y_off, 900, 305, True)
# 站第二层(26) 打第三层(123): 需要足够远(远端扇形)
allok &= shot(150, 26 - star_cls_y_off, 450, 123, True)     # dx=300 → half=96 ≥ 85
allok &= shot(350, 26 - star_cls_y_off, 450, 123, False)    # dx=100 → half=40 < 85 近端打不到
# 站第二层(26) 打第一层(-71): 向上
allok &= shot(150, 26 - star_cls_y_off, 450, -71, True)     # dy=59 需 half≥59 → dx≥168
# 站第三层(123) 打地面(305): 应该打不到(封顶130 < 176)
allok &= shot(50, 123 - star_cls_y_off, 700, 305, False)
allok &= shot(50, 123 - star_cls_y_off, 1150, 305, False)
print("全部通过" if allok else "存在失败")
sys.exit(0 if allok else 1)
