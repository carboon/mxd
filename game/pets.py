#!/usr/bin/env python3
"""Pets: purely cosmetic followers with nameplates (小白雪人 / 花蘑菇仔)."""
import math

import pygame

from . import config as C
from . import sprites
from .ui import draw_nameplate


class Pet:
    def __init__(self, frames, name, plate_style, offset):
        self.frames = [sprites.load_frame(f) for f in frames if True]
        self.name = name
        self.plate_style = plate_style
        self.offset = offset            # 相对玩家x的偏移
        self.x, self.y = 0.0, 305.0
        self.t = 0.0
        self.face = 1
        self.waiting = True

    def update(self, dt, player):
        self.t += dt
        tx = player.x + self.offset
        dx = tx - self.x
        if abs(dx) > 260:                       # 太远瞬移
            self.x = tx
            self.y = player.y
        elif abs(dx) > 10:
            self.face = 1 if dx > 0 else -1
            self.x += self.face * 120 * dt
            self.waiting = False
        else:
            self.waiting = True
        # 跟随玩家所在平台（取玩家脚下最近的那层）
        gy = None
        world = player.world
        for fh in world.footholds:
            x1, x2 = fh["x1"], fh["x2"]
            if min(x1, x2) <= self.x <= max(x1, x2) and x2 != x1:
                t = (self.x - x1) / (x2 - x1)
                fy = fh["y1"] + (fh["y2"] - fh["y1"]) * t
                if fy <= player.y + 6 and (gy is None or fy > gy):
                    gy = fy
        if gy is not None:
            self.y = gy

    def draw(self, surface, cam_x, cam_y):
        img = self.frames[int(self.t / 0.18) % len(self.frames)]
        if self.face == 1:
            img = pygame.transform.flip(img, True, False)
        bob = math.sin(self.t * 6) * 1.2 if self.waiting else 0
        surface.blit(img, (int(self.x - cam_x - img.get_width() / 2),
                           int(self.y - cam_y - img.get_height() + bob)))
        draw_nameplate(surface, self.plate_font, self.x - cam_x,
                       self.y - cam_y + 3, self.name, self.plate_style)

    plate_font = None


Pet.plate_font = None


def make_pets():
    sheets = sprites.load_pet_sheets()
    yeti_frames = sheets["yeti"]
    import os
    alt = os.path.join(C.ROOT, "assets/pets/yeti_idle2.png")
    if os.path.exists(alt):
        yeti_frames = [alt]
    from . import ui as _ui
    f = _ui._cn_font(11)
    yeti = Pet(yeti_frames, "小白雪人", "player", -52)
    mush = Pet(sheets["mush"], "花蘑菇仔", "pet_o", 55)
    yeti.plate_font = f
    mush.plate_font = f
    # 视频里小白雪人在左（白名牌）、花蘑菇仔在右（橙名牌）
    return [yeti, mush]
