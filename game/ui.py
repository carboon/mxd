#!/usr/bin/env python3
"""UI: bottom HUD with dynamic HP/MP/EXP, minimap with mob dots, nameplates."""
import math
import os

import pygame

from . import config as C


def _cn_font(size, bold=True):
    for name in ("pingfangsc", "hiraginosansgb", "stheiti", "arial"):
        f = pygame.font.match_font(name, bold)
        if f:
            return pygame.font.Font(f, size)
    return pygame.font.Font(None, size + 4)


class Ui:
    def __init__(self):
        self.hud = pygame.image.load(C.HUD_IMG).convert()
        self.minimap = pygame.image.load(C.MINIMAP_PANEL).convert()
        self.f_hud = _cn_font(11)
        self.f_small = _cn_font(10, False)
        self.f_plate = _cn_font(12)
        self.f_badge = _cn_font(11)
        # 视频帧里的槽位（本地坐标, hud 1280x79 对应屏幕 y645-724）
        self.hp_rect = pygame.Rect(439, 56, 152, 18)
        self.mp_rect = pygame.Rect(600, 56, 145, 18)
        self.exp_rect = pygame.Rect(767, 56, 173, 18)
        self.hp_txt = (450, 40)
        self.mp_txt = (606, 40)
        self.exp_txt = (778, 40)
        self.slot_bg = (64, 62, 70)
        self.minimap_world = {"left": -347, "top": -521, "w": 1501, "h": 1177}

    def bar(self, surface, rect, ratio, fill, label, value_txt, txt_pos):
        pygame.draw.rect(surface, self.slot_bg, rect)
        w = int(rect.width * max(0.0, min(1.0, ratio)))
        if w > 0:
            pygame.draw.rect(surface, fill, (rect.x, rect.y, w, rect.height))
        pygame.draw.rect(surface, (30, 28, 34), rect, 1)
        t1 = self.f_hud.render(label, True, (255, 255, 255))
        t2 = self.f_hud.render(value_txt, True, (255, 255, 255))
        surface.blit(t1, txt_pos)
        surface.blit(t2, (txt_pos[0] + t1.get_width() + 3, txt_pos[1]))

    def draw(self, surface, player, session, world, mobs, cam_x, cam_y, fx):
        hud_y = C.VIEW_H - 79
        surface.blit(self.hud, (0, hud_y))
        off = (0, hud_y)
        self.bar(surface, self.hp_rect.move(off), player.hp / C.PLAYER_MAX_HP, (200, 30, 40),
                 "HP", f"({int(player.hp)}/{C.PLAYER_MAX_HP})",
                 (self.hp_txt[0], self.hp_txt[1] + hud_y))
        self.bar(surface, self.mp_rect.move(off), player.mp / C.PLAYER_MAX_MP, (40, 90, 220),
                 "MP", f"({int(player.mp)}/{C.PLAYER_MAX_MP})",
                 (self.mp_txt[0], self.mp_txt[1] + hud_y))
        self.bar(surface, self.exp_rect.move(off), session.exp / C.EXP_TO_NEXT, (210, 220, 40),
                 "EXP", f"{session.exp}/{session.exp / C.EXP_TO_NEXT:.2f}%",
                 (self.exp_txt[0], self.exp_txt[1] + hud_y))
        self.draw_buffs(surface, player)
        self.draw_minimap(surface, player, mobs)
        fx.draw_notices(surface)

    def draw_buffs(self, surface, player):
        """右上角buff栏: 轻功图标。剩余<15秒闪烁(提示可补放), 到期消失。"""
        if player.haste_t <= 0:
            return
        if player.haste_t < C.HASTE_BLINK_REMAIN and int(player.haste_t * 4) % 2 == 0:
            return                                  # 闪烁相位(2Hz)
        s = C.HASTE_ICON_SIZE
        x, y = C.HASTE_ICON_POS
        icon = pygame.Surface((s, s), pygame.SRCALPHA)
        pygame.draw.rect(icon, (38, 58, 108), (0, 0, s, s), border_radius=5)
        pygame.draw.rect(icon, (150, 225, 255), (0, 0, s, s), 2, border_radius=5)
        # 青白色翅膀(轻功)图形 —— 高饱和青色色块供视觉系统识别
        pygame.draw.polygon(icon, (120, 215, 255),
                            [(3, 17), (s // 2, 4), (s - 3, 17), (s // 2, 13)])
        pygame.draw.polygon(icon, (235, 251, 255),
                            [(6, 20), (s // 2, 12), (s - 6, 20), (s // 2, 19)])
        surface.blit(icon, (x, y))

    def draw_minimap(self, surface, player, mobs):
        surface.blit(self.minimap, (0, 0))
        mw = self.minimap_world
        ox, oy = C.MINIMAP_ORIGIN
        w, h = C.MINIMAP_SPAN

        def to_map(wx, wy):
            mx = ox + int((wx - mw["left"]) / mw["w"] * w)
            my = oy + int((wy - mw["top"]) / mw["h"] * h)
            return mx, my

        for m in mobs:
            if m.state != "die":
                pygame.draw.circle(surface, (230, 60, 60), to_map(m.x, m.y), 2)
        px, py = to_map(player.x, player.y)
        pygame.draw.polygon(surface, (255, 170, 40),
                            [(px, py - 3), (px - 3, py + 2), (px + 3, py + 2)])


def draw_nameplate(surface, font, x, y, text, style):
    """style: player(白底黑字) / mob(深色底白字) / pet_o(橙底) / badge(绿底)"""
    t = font.render(text, True, (20, 20, 20) if style == "player" else (255, 255, 255))
    pad = 4
    w, h = t.get_width() + pad * 2, t.get_height() + 3
    r = pygame.Rect(int(x - w / 2), int(y), w, h)
    if style == "player":
        pygame.draw.rect(surface, (245, 245, 235), r, border_radius=3)
        tc = (30, 30, 30)
    elif style == "mob":
        pygame.draw.rect(surface, (40, 40, 48), r, border_radius=3)
        tc = (255, 255, 255)
    elif style == "pet_o":
        pygame.draw.rect(surface, (235, 150, 40), r, border_radius=3)
        tc = (255, 255, 255)
    else:  # badge
        pygame.draw.rect(surface, (70, 130, 70), r, border_radius=3)
        tc = (255, 255, 255)
    pygame.draw.rect(surface, (20, 20, 20), r, 1, border_radius=3)
    surface.blit(t, (r.x + pad, r.y + 1))
    return r
