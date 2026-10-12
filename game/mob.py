#!/usr/bin/env python3
"""Monsters: WZ-accurate skins/stats, wander AI, aggro-on-hit, respawn manager."""
import json
import math
import random

import pygame

from . import config as C
from . import sprites
from .sprites import _white_of

with open(f"{C.ROOT}/data/mobs.json") as f:
    MOB_STATS = json.load(f)

# speed stat -> px/s (视频标定的观感值)
SPEED_PX = {"2220100": 22.0, "3000004": 38.0, "3210800": 55.0, "5130101": 30.0}


class Mob:
    def __init__(self, world, spawn, mob_id, skins):
        self.world = world
        self.spawn = spawn
        self.mob_id = mob_id
        st = MOB_STATS[str(mob_id)]
        self.name = st["name"]
        self.body_w = st.get("body_w", 44)
        self.body_h = st.get("body_h", 44)
        self.max_hp = st["hp"]
        self.hp = st["hp"]
        self.exp = st["exp"]
        self.speed = SPEED_PX[str(mob_id)]
        self.touch_dmg = st["pad"]
        self.skin = skins[mob_id]
        self.x = float(spawn["x"])
        self.y = float(spawn["y"])
        self.face = random.choice((-1, 1))
        self.state = "move"            # move hit die
        self.anim_t = random.uniform(0, 1)
        self.hit_t = 0.0
        self.die_t = -1.0
        self.aggro_t = 0.0
        self.stun_t = 0.0
        self.show_hp_t = 0.0
        self.touch_cd = 0.0
        self.float_phase = random.uniform(0, 6.28)
        self.fly = (mob_id == 3000004)

    def rect(self):
        return pygame.Rect(self.x - self.body_w / 2, self.y - self.body_h,
                           self.body_w, self.body_h)

    def take_hit(self, dmg, from_x, fx):
        if self.die_t >= 0:
            return
        applied = min(dmg, max(self.hp, 0))     # 结算不超剩余血量(双飞斩第二发只补刀差值)
        self.hp -= applied
        self.state = "hit"
        self.hit_t = 0.32
        self.stun_t = 0.3
        self.aggro_t = 3.5
        self.show_hp_t = 4.0
        self.face = -1 if from_x < self.x else 1
        self.x += (18 if from_x < self.x else -18)   # 击退
        fx.damage_number(self.x, self.y - 55, str(applied))
        if self.hp <= 0:
            self.state = "die"
            self.die_t = 0.0

    def update(self, dt, player):
        self.anim_t += dt
        self.show_hp_t = max(0.0, self.show_hp_t - dt)
        self.touch_cd = max(0.0, self.touch_cd - dt)
        if self.state == "die":
            self.die_t += dt
            return
        if self.stun_t > 0:
            self.stun_t -= dt
            if self.hit_t > 0:
                self.hit_t -= dt
            return
        if self.state == "hit":
            self.hit_t -= dt
            if self.hit_t <= 0:
                self.state = "move"
        # 巡走 + 仇恨追击
        self.aggro_t = max(0.0, self.aggro_t - dt)
        if self.aggro_t > 0 and player.inv_t <= 0:
            self.face = -1 if player.x < self.x else 1
        elif random.random() < dt * 0.25:
            self.face *= -1
        nx = self.x + self.face * self.speed * dt
        fh = self.world.fh_id_at(nx, self.y)
        if fh is None or fh != self.world.fh_id_at(self.x, self.y):
            self.face *= -1
        else:
            self.x = nx
        if self.fly:
            self.float_phase += dt * 3
            base = self.spawn["y"]
            self.y = base - 26 + math.sin(self.float_phase) * 8
        # 触碰判定(每怪间隔1s): 50% MISS(不掉血/不击退/不硬直, 头顶漂MISS) / 50% 命中
        if (self.state != "die" and player.inv_t <= 0 and self.touch_cd <= 0
                and self.rect().colliderect(player.rect())):
            self.touch_cd = C.TOUCH_CD
            from .combat import global_fx
            if random.random() < C.PLAYER_MISS_RATE:
                global_fx.miss_float(player.x, player.y - 76)
            else:
                dmg = int(self.touch_dmg * 2.0 + (hash(self) % 40))
                if player.take_hit(dmg, self.x):
                    global_fx.damage_number(player.x, player.y - 70, str(dmg))

    def draw(self, surface, cam_x, cam_y):
        skin = self.skin
        flip = self.face == 1      # 素材默认朝左
        if self.state == "die":
            idx = min(int(self.die_t / 0.09), len(skin.die) - 1)
            path = skin.die[idx]
            done = idx >= len(skin.die) - 1 and self.die_t > 0.09 * len(skin.die)
        elif self.state == "hit":
            path = skin.hit[0]
            done = False
        else:
            act = skin.move if self.state == "move" else skin.stand
            path = act[int(self.anim_t / 0.28) % len(act)]
            done = False
        img = sprites.flip_frame(path) if flip else sprites.load_frame(path)
        px = int(self.x - cam_x - img.get_width() / 2)
        py = int(self.y - cam_y - img.get_height() + (2 if self.fly else 0))
        surface.blit(img, (px, py))
        if self.state == "hit" and int(self.hit_t * 20) % 2 == 0:
            # 受击白闪（原版白色受击特效观感）
            flash = _white_of(img)
            surface.blit(flash, (px, py))
        if self.state == "die" and done:
            self.dead_render_done = True


class SpawnManager:
    def __init__(self, world):
        self.world = world
        self.skins = sprites.load_mob_skins()
        self.mobs = []
        self.pending = []          # (t, spawn)
        ids = [s["mobId"] for s in world.spawn_points()]
        # 开局按官方点位种类生成
        for sp in world.spawn_points():
            self.mobs.append(Mob(world, sp, sp["mobId"], self.skins))

    def update(self, dt, player, session, fx):
        import time as _time
        now = _time.time()
        for m in self.mobs:
            m.update(dt, player)
            if m.state == "die" and m.die_t > 0.09 * len(m.skin.die) + 0.05:
                # 结算
                if not getattr(m, "settled", False):
                    m.settled = True
                    session.on_kill(m, now)
                    fx.exp_notice(m.exp)
                self.mobs.remove(m)
                self.pending.append([C.RESPAWN_SECONDS, m.spawn])
        for p in self.pending[:]:
            p[0] -= dt
            if p[0] <= 0:
                self.pending.remove(p)
                spawn = p[1]
                mid = random.choice(list(MOB_STATS.keys()))    # 随机种类
                self.mobs.append(Mob(self.world, spawn, int(mid), self.skins))

    def alive_count(self):
        return len(self.mobs) + len(self.pending)
