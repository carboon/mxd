#!/usr/bin/env python3
"""冒险岛石人寺院II 复刻 demo — 入口与主循环。

操作:
  ← → 走路   ↑/↓ 攀爬藤蔓   空格 跳跃   ↓+空格 平台下跳   ↓ 趴下(无藤蔓时)
  Shift 双飞斩(空中可用, 近身时自动变为挥拳)   A 轻功(Lv.20, 持续180秒)
  F9 经验清零   F1 碰撞调试显示   Esc 退出
"""
import json
import os
import sys

import pygame

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from game import config as C
from game import combat, sprites
from game.mob import SpawnManager
from game.player import Player
from game.stats import Session
from game.ui import Ui, draw_nameplate, _cn_font
from game.world import World


class Game:
    def __init__(self, headless=False, ipc=False):
        self.headless = headless
        if headless:
            os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        pygame.init()
        pygame.display.set_caption("冒险岛 · 石人寺院II 复刻")
        self.screen = pygame.display.set_mode((C.VIEW_W, C.VIEW_H))
        self.clock = pygame.time.Clock()
        self.world = World()
        self.player = Player(self.world)
        self.session = Session()
        self.spawns = SpawnManager(self.world)
        # 确定性导航测试场景开关
        if os.environ.get("MXD_MOBS_UP") == "1":
            upper = [s for s in self.world.spawn_points() if s["y"] <= 200]
            for i, m in enumerate(self.spawns.mobs):
                sp = upper[i % len(upper)]
                m.spawn["x"], m.spawn["y"] = float(sp["x"]), float(sp["y"])
                m.x, m.y = float(sp["x"]), float(sp["y"])
        if os.environ.get("MXD_MOBS_GROUND") == "1":
            ground = [s for s in self.world.spawn_points() if s["y"] >= 280]
            for i, m in enumerate(self.spawns.mobs):
                sp = ground[i % len(ground)]
                m.spawn["x"], m.spawn["y"] = float(sp["x"]), float(sp["y"])
                m.x, m.y = float(sp["x"]), float(sp["y"])
        player_at = os.environ.get("MXD_PLAYER_AT")
        if player_at:
            px_, py_ = [float(v) for v in player_at.split(",")]
            self.player.x, self.player.y = px_, py_
            self.player.fh_id = self.world.fh_id_at_y(px_, py_)
        # 脱围验证场景: 3只怪紧贴玩家出生点(验证bot围殴脱离)
        if os.environ.get("MXD_MOBS_AROUND") == "1":
            for m, dx in zip(self.spawns.mobs, (-45, -75, 60)):
                m.x, m.y = self.player.x + dx, 305.0
        self.ui = Ui()
        self.combat = combat.Combat(lambda: self.spawns.mobs)
        self.fx = combat.global_fx
        self.cam_x, self.cam_y = self.world.clamp_cam(
            self.player.x - C.VIEW_W / 2, self.player.y - C.VIEW_H / 2)
        self.debug = False
        self.running = True
        # 进程间通道：帧发布(视觉系统) + 虚拟键盘(bot)
        self.publisher = None
        self.remote = None
        if ipc or os.environ.get("MXD_IPC") == "1":
            from game import ipc
            if headless:
                ipc.cleanup_stale()
            self.publisher = ipc.FramePublisher(C.VIEW_W, C.VIEW_H)
            self.remote = ipc.RemoteInputServer()

    # ------------------------------------------------------------------
    def handle_events(self):
        events = set()
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                self.running = False
            elif e.type == pygame.KEYDOWN:
                if e.key == pygame.K_ESCAPE:
                    self.running = False
                elif e.key == pygame.K_SPACE:
                    events.add("jump")
                elif e.key in (pygame.K_LSHIFT, pygame.K_RSHIFT):
                    events.add("attack")
                elif e.key == pygame.K_a:
                    events.add("skill")
                elif e.key == pygame.K_F9:
                    self.session.reset()
                    self.fx.info_notice("经验统计已清零 (F9)", (150, 255, 150))
                elif e.key == pygame.K_F1:
                    self.debug = not self.debug
                elif e.key == pygame.K_UP and self.world.portal_near(self.player.x, self.player.y):
                    self.fx.info_notice(f"{self.world.portals[0]['targetName']}（demo 未实装）")
        return events

    def update(self, dt, keys, events):
        # 合并bot虚拟键盘（ipc模式）
        if self.remote is not None:
            rkeys, revents = self.remote.read()
            self._dbg_reads = getattr(self, "_dbg_reads", 0) + 1
            if rkeys:
                self._dbg_withkeys = getattr(self, "_dbg_withkeys", 0) + 1
                if rkeys.get("down"):
                    self._dbg_down = getattr(self, "_dbg_down", 0) + 1
                if rkeys.get("up"):
                    self._dbg_up = getattr(self, "_dbg_up", 0) + 1
                self._dbg_edges = getattr(self, "_dbg_edges", 0) + len(revents)
            if rkeys:
                local = {
                    pygame.K_LEFT: bool(keys[pygame.K_LEFT]),
                    pygame.K_RIGHT: bool(keys[pygame.K_RIGHT]),
                    pygame.K_UP: bool(keys[pygame.K_UP]),
                    pygame.K_DOWN: bool(keys[pygame.K_DOWN]),
                }
                for k, rk in (("left", pygame.K_LEFT), ("right", pygame.K_RIGHT),
                              ("up", pygame.K_UP), ("down", pygame.K_DOWN)):
                    local[rk] = local[rk] or rkeys[k]
                keys = local
            events = set(events) | revents
        self.player.update(dt, keys, events, self.combat, self.session)
        self.spawns.update(dt, self.player, self.session, self.fx)
        self.combat.update(dt)
        self.fx.update(dt)
        # 相机跟随
        tx = self.player.x - C.VIEW_W / 2
        ty = self.player.y - C.VIEW_H / 2 - 40
        self.cam_x += (tx - self.cam_x) * min(1.0, dt * 8)
        self.cam_y += (ty - self.cam_y) * min(1.0, dt * 8)
        self.cam_x, self.cam_y = self.world.clamp_cam(self.cam_x, self.cam_y)

    def draw(self):
        surf = self.screen
        self.world.draw(surf, self.cam_x, self.cam_y)
        if self.debug:
            self.world.debug_draw(surf, self.cam_x, self.cam_y)
        # 怪物
        for m in self.spawns.mobs:
            m.draw(surf, self.cam_x, self.cam_y)
            if m.state != "die":
                draw_nameplate(surf, self.plate_font, m.x - self.cam_x,
                               m.y - self.cam_y + 2, f"Lv.{self.mob_level(m)}|{m.name}", "mob")
                if m.show_hp_t > 0 and m.hp < m.max_hp:
                    r = pygame.Rect(int(m.x - self.cam_x - 22),
                                    int(m.y - self.cam_y + 18), 44, 5)
                    pygame.draw.rect(surf, (25, 25, 30), r)
                    pygame.draw.rect(surf, (60, 200, 250),
                                     (r.x, r.y, int(44 * m.hp / m.max_hp), 5))
        # 玩家名牌 + 徽章
        px = self.player.x - self.cam_x
        py = self.player.y - self.cam_y
        draw_nameplate(surf, self.plate_font, px, py + 4, C.PLAYER_NAME, "player")
        draw_nameplate(surf, self.badge_font, px, py + 20, C.PLAYER_BADGE, "badge")
        self.player.draw(surf, self.cam_x, self.cam_y)
        self.combat.draw(surf, self.cam_x, self.cam_y)
        self.fx.draw_texts(surf, self.cam_x, self.cam_y)
        self.ui.draw(surf, self.player, self.session, self.world,
                     self.spawns.mobs, self.cam_x, self.cam_y, self.fx)
        pygame.display.flip()
        if self.publisher is not None:
            self.publisher.publish(surf)
        if os.environ.get("MXD_TRUTH") == "1":
            now = pygame.time.get_ticks()
            if now - getattr(self, "_last_truth", 0) > 400:
                self._last_truth = now
                try:
                    with open("/tmp/mxd_truth.json", "w") as f:
                        json.dump({"x": self.player.x, "y": self.player.y,
                                   "onRope": self.player.climb is not None,
                                   "haste": round(self.player.haste_t, 1),
                                   "kills": self.session.total_kills,
                                   "stars": self.combat.stars_fired,
                                   "punches": self.combat.punches,
                                   "face": self.player.face,
                                   "state": self.player.state,
                                   "kb": round(self.player.kb_t, 2),
                                   "cd": round(self.player.cooldown, 2)}, f)
                except Exception:
                    pass

    @staticmethod
    def mob_level(m):
        from game.mob import MOB_STATS
        return MOB_STATS[str(m.mob_id)]["level"]

    def run(self):
        self.plate_font = _cn_font(12)
        self.badge_font = _cn_font(11)
        try:
            self._run_inner()
        finally:
            if os.environ.get("MXD_TRUTH") == "1":
                print("DBG reads=%s withkeys=%s up=%s down=%s edges=%s" % (
                    getattr(self, "_dbg_reads", 0), getattr(self, "_dbg_withkeys", 0),
                    getattr(self, "_dbg_up", 0), getattr(self, "_dbg_down", 0),
                    getattr(self, "_dbg_edges", 0)), flush=True)

    def _run_inner(self):
        while self.running:
            dt = min(self.clock.tick(C.FPS) / 1000.0, 0.05)
            events = self.handle_events()
            keys = pygame.key.get_pressed()
            self.update(dt, keys, events)
            self.draw()
        pygame.quit()


if __name__ == "__main__":
    headless = "--headless" in sys.argv
    Game(headless=headless, ipc="--ipc" in sys.argv).run()
