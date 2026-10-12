#!/usr/bin/env python3
"""Combat: star projectiles + hit detection; floating numbers & notices."""
import random

import pygame

from . import config as C
from . import sprites


class Star:
    """双飞斩飞镖: 发射时已锁定目标, 直线弹道(vy为倾斜速度), 不追踪。

    原版规格: 命中即消失, 不穿透(一镖一怪)。"""

    def __init__(self, x, y, face, vy=0.0):
        self.x = x
        self.y = y
        self.face = face
        self.vy = vy
        self.dead = False
        self.t = 0.0

    def update(self, dt, mobs):
        self.t += dt
        self.x += self.face * C.STAR_SPEED * dt
        self.y += self.vy * dt
        r = pygame.Rect(self.x - 12, self.y - 12, 24, 24)
        for m in mobs:
            if m.state != "die" and r.colliderect(m.rect()):
                m.take_hit(C.STAR_DAMAGE, self.x - self.face * 40, global_fx)
                self.dead = True
                return
        if self.t > 1.2 or not (-360 < self.x < 1200) or not (-400 < self.y < 420):
            self.dead = True

    def draw(self, surface, cam_x, cam_y):
        img = sprites.load_fx().get("star")
        if img is None:
            pygame.draw.circle(surface, (30, 30, 34),
                               (int(self.x - cam_x), int(self.y - cam_y)), 5)
            return
        # 四角镖随飞行旋转(经典投掷感), 不随弹道倾斜
        img2 = pygame.transform.rotate(img, (self.t * 540) % 360)
        surface.blit(img2, (int(self.x - cam_x - img2.get_width() / 2),
                            int(self.y - cam_y - img2.get_height() / 2)))


class Combat:
    def __init__(self, mobs_getter):
        self.stars = []
        self.slash_fx = []                 # (x,y,face,t) 挥砍特效
        self.mobs_getter = mobs_getter
        self.stars_fired = 0               # 诊断计数
        self.punches = 0                   # 诊断计数

    def slash(self, player):
        self.slash_fx.append([player.x + player.face * 26,
                              player.y - 36, player.face, 0.0])

    def nearest_melee(self, player):
        """近身最近的可攻击怪(原版: 贴身时攻击=挥拳, 与MP无关)。"""
        best, bd = None, None
        for m in self.mobs_getter():
            if m.state == "die":
                continue
            dx = abs(m.x - player.x)
            dy = abs(m.y - player.y)
            if dx <= C.PUNCH_RANGE and dy <= C.PUNCH_RANGE_Y and (bd is None or dx < bd):
                best, bd = m, dx
        return best

    def punch(self, player, target):
        """挥拳: 近身攻击, 微量伤害且吸引仇恨(怪物受击即进入3.5s仇恨追击)。"""
        self.punches += 1
        self.slash_fx.append([player.x + player.face * 18,
                              player.y - 34, player.face, 0.0])
        target.take_hit(C.PUNCH_DAMAGE, player.x, global_fx)

    def _l7_target(self, player, spawn_y):
        """经典客户端 Lucky Seven 目标搜索(步进楔形)。

        从身前65px起按20px波段步进, 波段边界按1:4斜率扩宽,
        垂直封顶脚上124px/脚下68px; 取最近的可打目标。
        """
        best, best_dx = None, None
        for m in self.mobs_getter():
            if m.state == "die":
                continue
            dxm = (m.x - player.x) * player.face
            if dxm < C.L7_SEARCH_START or dxm > C.L7_SEARCH_START + C.L7_RANGE:
                continue
            grow = (dxm - C.L7_SEARCH_START) / C.L7_SLOPE_DEN
            up = min(C.L7_BAND_HALF + grow, C.L7_REACH_UP)      # 脚上方
            down = min(C.L7_BAND_HALF + grow, C.L7_REACH_DOWN)  # 脚下方
            rect = m.rect()
            rel_t = rect.top - player.y                          # 怪身顶(相对脚)
            rel_b = rect.bottom - player.y
            if rel_b < -up or rel_t > down:                      # 身体与波段无重叠
                continue
            if best is None or dxm < best_dx:
                best, best_dx = m, dxm
        return best

    def throw_star(self, player):
        y = player.y - 38 if player.state != "prone" else player.y - 14
        vy = 0.0
        target = self._l7_target(player, y)
        if target is not None:
            # 直线弹道: 从出手点穿过目标身体中心(打上下平台时倾斜)
            dxm = abs(target.x - player.x)
            vy = ((target.y - 22) - y) / max(dxm, 1.0) * C.STAR_SPEED
        self.stars.append(Star(player.x + player.face * 18, y, player.face, vy))
        self.stars_fired += 1

    def update(self, dt):
        for s in self.stars:
            s.update(dt, self.mobs_getter())
        self.stars = [s for s in self.stars if not s.dead]
        for f in self.slash_fx:
            f[3] += dt
        self.slash_fx = [f for f in self.slash_fx if f[3] < 0.14]

    def draw(self, surface, cam_x, cam_y):
        for s in self.stars:
            s.draw(surface, cam_x, cam_y)
        slash = sprites.load_fx().get("slash")
        for x, y, face, t in self.slash_fx:
            if slash is None:
                continue
            img = pygame.transform.flip(slash, face == 1, False)
            surface.blit(img, (int(x - cam_x - img.get_width() / 2),
                               int(y - cam_y - img.get_height() / 2)))


class FloatingText:
    def __init__(self, x, y, text, color, outline, size, life=0.9, vy=-46):
        self.x = x + random.uniform(-6, 6)
        self.y = y
        self.text = text
        self.color = color
        self.outline = outline
        self.size = size
        self.life = life
        self.t = 0.0
        self.vy = vy


class Fx:
    def __init__(self):
        self.texts = []
        self.misses = []       # 玩家MISS漂浮(录屏同款: 头顶上升渐隐)
        self.notices = []      # 右下角拾取/经验提示
        self.font_cache = {}
        self.notice_t = 0.0

    def font(self, size, bold=True):
        key = (size, bold)
        if key not in self.font_cache:
            f = pygame.font.SysFont("pingfangsc, arial, helvetica", size, bold=bold)
            if f is None:
                f = pygame.font.Font(None, size)
            self.font_cache[key] = f
        return self.font_cache[key]

    def damage_number(self, x, y, text, color=C.DAMAGE_COLOR):
        self.texts.append(FloatingText(x, y, text, color, C.DAMAGE_OUTLINE, 21))

    def miss_float(self, x, y):
        """玩家闪避: MISS图案在头顶漂浮上升(不掉血/不击退/不硬直)。"""
        self.misses.append([x, y, 0.0])

    def exp_float(self, x, y, text):
        self.texts.append(FloatingText(x, y, text, (255, 230, 120), (60, 40, 10), 15, life=1.1))

    def exp_notice(self, exp):
        self.notices.append(("得到经验值 (+%d)" % exp, (255, 220, 120)))
        if len(self.notices) > 4:
            self.notices.pop(0)
        self.notice_t = 3.2

    def info_notice(self, text, color=(255, 255, 255)):
        self.notices.append((text, color))
        if len(self.notices) > 4:
            self.notices.pop(0)
        self.notice_t = 3.2

    def update(self, dt):
        for t in self.texts:
            t.t += dt
            t.y += t.vy * dt
            t.vy *= 0.94
        self.texts = [t for t in self.texts if t.t < t.life]
        for m in self.misses:
            m[2] += dt
            m[1] -= 30 * dt               # 录屏观测: MISS向上漂移
        self.misses = [m for m in self.misses if m[2] < 0.8]
        self.notice_t -= dt
        if self.notice_t <= 0 and self.notices:
            self.notices.pop(0)
            self.notice_t = 1.2 if self.notices else 0

    def draw_texts(self, surface, cam_x, cam_y):
        miss = sprites.load_fx().get("miss")
        for m in self.misses:
            if miss is None:
                continue
            alpha = max(0, 255 - int(255 * m[2] / 0.8))
            img = miss.copy()
            img.set_alpha(alpha)
            surface.blit(img, (int(m[0] - cam_x - img.get_width() / 2),
                               int(m[1] - cam_y - img.get_height())))
        for t in self.texts:
            alpha = max(0, 255 - int(255 * t.t / t.life))
            f = self.font(t.size)
            surf = f.render(t.text, True, t.color)
            ol = f.render(t.text, True, t.outline)
            x = int(t.x - cam_x - surf.get_width() / 2)
            y = int(t.y - cam_y)
            for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
                ol.set_alpha(alpha)
                surface.blit(ol, (x + dx, y + dy))
            surf.set_alpha(alpha)
            surface.blit(surf, (x, y))

    def draw_notices(self, surface):
        f = self.font(13)
        y = C.VIEW_H - 208
        for text, color in self.notices[-4:]:
            ol = f.render(text, True, (30, 20, 5))
            surf = f.render(text, True, color)
            x = C.VIEW_W - 246
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                surface.blit(ol, (x + dx, y + dy))
            surface.blit(surf, (x, y))
            y += 18


global_fx = Fx()
