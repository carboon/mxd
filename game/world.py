#!/usr/bin/env python3
"""World: map image, foothold collision, ropes, spawn points, camera."""
import json
import os

import pygame

from . import config as C


class World:
    def __init__(self):
        with open(os.path.join(C.ROOT, "data", "map.json")) as f:
            self.data = json.load(f)
        self.meta = self.data["meta"]
        self.footholds = self.data["footholds"]
        self.ropes = self.data["ropes"]
        self.spawns = self.data["spawns"]
        self.portals = self.data["portals"]
        bg = pygame.image.load(C.MAP_BG).convert_alpha()
        self.bg = bg
        self.sky = pygame.image.load(C.SKY_BG).convert()
        self.bg_ox, self.bg_oy = C.BG_ORIGIN

    # ---- foothold ---------------------------------------------------------
    def fh_line_y(self, fh, x):
        x1, x2 = fh["x1"], fh["x2"]
        if x2 == x1 or not (min(x1, x2) - 2 <= x <= max(x1, x2) + 2):
            return None
        t = (x - x1) / (x2 - x1)
        return fh["y1"] + (fh["y2"] - fh["y1"]) * t

    def ground_y(self, x, y, vy, drop_through=None, prev_y=None):
        """Landing y when falling across a foothold this frame, else None.
        prev_y: 上一帧的y——只允许落到本帧跨越的线（防止吸到高处平台）。"""
        best = None
        for fh in self.footholds:
            if drop_through is not None and fh.get("id", -1) == drop_through:
                continue
            fy = self.fh_line_y(fh, x)
            if fy is None:
                continue
            top = y if prev_y is None else max(y, prev_y - 0.0)
            crossed = (fy <= y + 0.5) and (prev_y is None or prev_y <= fy + 1.0)
            if not crossed:
                continue
            if best is None or fy > best:   # 本帧跨越的最高一条 = 唯一合理的落点
                best = fy
        return best

    def fh_on(self, x, y, fh_id):
        """当前所在foothold在新x处的y；x离开该foothold返回None。"""
        for fh in self.footholds:
            if fh.get("id") == fh_id:
                return self.fh_line_y(fh, x)
        return None

    def fh_id_at_y(self, x, y):
        """与(y±6)平齐的foothold id（行走转移用）。"""
        for fh in self.footholds:
            fy = self.fh_line_y(fh, x)
            if fy is not None and abs(fy - y) <= 6:
                return fh["id"]
        return None

    def drop_target(self, x, y):
        """Nearest foothold strictly below y at x (for down-jump check)."""
        best = None
        for fh in self.footholds:
            x1, x2 = fh["x1"], fh["x2"]
            if not (min(x1, x2) <= x <= max(x1, x2)):
                continue
            if x2 == x1:
                continue
            t = (x - x1) / (x2 - x1)
            fy = fh["y1"] + (fh["y2"] - fh["y1"]) * t
            if fy > y + 6 and (best is None or fy < best):
                best = fy
        return best

    def fh_id_at(self, x, y):
        for fh in self.footholds:
            x1, x2 = fh["x1"], fh["x2"]
            if not (min(x1, x2) <= x <= max(x1, x2)) or x2 == x1:
                continue
            t = (x - x1) / (x2 - x1)
            fy = fh["y1"] + (fh["y2"] - fh["y1"]) * t
            if abs(fy - y) < 4:
                return fh["id"]
        return None

    # ---- ropes ------------------------------------------------------------
    def rope_near(self, x, y):
        for r in self.ropes:
            if abs(x - r["x"]) <= 20 and r["y1"] - 12 <= y <= r["y2"] + 12:
                return r
        return None

    def portal_near(self, x, y):
        for p in self.portals:
            if abs(x - p["x"]) <= 30 and abs(y - p["y"]) <= 40:
                return p
        return None

    # ---- spawn points -----------------------------------------------------
    def spawn_points(self):
        out = []
        for s in self.spawns:
            out.append({"x": s["x"], "y": s["cy"], "fh": s["fh"], "mobId": s["mobId"]})
        return out

    # ---- camera -----------------------------------------------------------
    def clamp_cam(self, cx, cy):
        b = C.CAM_BOUNDS
        cx = max(b["left"], min(b["right"], cx))
        cy = max(b["top"], min(b["bottom"], cy))
        return cx, cy

    def draw(self, surface, cam_x, cam_y):
        # sky: world-anchored
        sx = int(cam_x - self.bg_ox)
        sy = int(cam_y - (C.CAM_BOUNDS["top"]))
        area = pygame.Rect(sx, sy, C.VIEW_W, C.VIEW_H)
        surface.blit(self.sky, (0, 0), area)
        # map layer
        surface.blit(self.bg, (self.bg_ox - cam_x, self.bg_oy - cam_y))

    def debug_draw(self, surface, cam_x, cam_y):
        for fh in self.footholds:
            a = (fh["x1"] - cam_x, fh["y1"] - cam_y)
            b = (fh["x2"] - cam_x, fh["y2"] - cam_y)
            pygame.draw.line(surface, (255, 60, 60), a, b, 1)
        for r in self.ropes:
            pygame.draw.line(surface, (80, 255, 80),
                             (r["x"] - cam_x, r["y1"] - cam_y),
                             (r["x"] - cam_x, r["y2"] - cam_y), 2)
        for s in self.spawn_points():
            pygame.draw.circle(surface, (255, 255, 0),
                               (int(s["x"] - cam_x), int(s["y"] - cam_y)), 3, 1)
