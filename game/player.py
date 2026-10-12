#!/usr/bin/env python3
"""Player: movement state machine, climbing, jumping, attacks, hit reaction."""
import math

import pygame

from . import config as C
from . import sprites
from .combat import global_fx


class Player:
    def __init__(self, world):
        self.world = world
        self.x, self.y = -20.0, 305.0        # 出生点：地图中部地面
        self.fh_id = world.fh_id_at_y(self.x, self.y)
        self.face = 1                        # 1 右, -1 左
        self.vy = 0.0
        self.vx = 0.0
        self.on_ground = True
        self.state = "stand"                 # stand walk jump climb prone hit
        self.climb = None                    # 当前攀爬的绳索
        self.attack_t = -1.0                 # 攻击动画计时
        self.cooldown = 0.0
        self.star_queue = 0                  # 待发射星数
        self.star_timer = 0.0
        self.kb_t = 0.0
        self.inv_t = 0.0
        self.haste_t = 0.0                   # 轻功剩余时间
        self.haste_cd = 0.0                  # 轻功释放间隔
        self.fh_id = None                    # 当前所在foothold id
        self.drop_fh = None                  # 下跳穿透的foothold id
        self.drop_t = 0.0
        self.hp = C.PLAYER_HP
        self.mp = C.PLAYER_MP
        self.no_dmg_t = 0.0
        self.sheets = sprites.load_player_sheets()
        self.anim_t = 0.0

    # ---- helpers ----------------------------------------------------------
    def rect(self):
        w, h = 34, 56
        return pygame.Rect(self.x - w / 2, self.y - h, w, h)

    def busy_attack(self):
        return 0 <= self.attack_t < 0.45

    def speed(self):
        """当前移动速度(轻功满速140%)。"""
        return C.WALK_SPEED * (C.HASTE_SPEED_MULT if self.haste_t > 0 else 1.0)

    def jump_v(self):
        """当前起跳速度(轻功满跳120%)。"""
        return C.JUMP_V * (C.HASTE_JUMP_MULT if self.haste_t > 0 else 1.0)

    def cast_haste(self, fx):
        """轻功(原版Lv20数据): 持续180秒(MP无限模式不耗魔)。图标未闪烁/未消失时不可重复释放。"""
        if self.climb is not None or self.haste_cd > 0:
            return False
        if self.haste_t > C.HASTE_BLINK_REMAIN:      # 仍处于稳定持续期
            return False
        if self.mp < C.HASTE_MP_COST:
            return False
        self.mp -= C.HASTE_MP_COST
        self.haste_t = C.HASTE_TIME
        self.haste_cd = 1.0
        fx.info_notice("轻功 (Lv.20)：速度/跳跃提升，持续180秒", (150, 225, 255))
        return True

    # ---- update -----------------------------------------------------------
    def update(self, dt, keys, events, combat, session):
        self.anim_t += dt
        self.cooldown = max(0.0, self.cooldown - dt)
        self.inv_t = max(0.0, self.inv_t - dt)
        self.haste_t = max(0.0, self.haste_t - dt)
        self.haste_cd = max(0.0, self.haste_cd - dt)
        self.no_dmg_t += dt
        # HP自动补血(需求变更): 持续回复, 不考虑死亡场景
        if self.hp < C.PLAYER_MAX_HP:
            self.hp = min(C.PLAYER_MAX_HP, self.hp + C.HP_REGEN * dt)
        self.mp = min(C.PLAYER_MAX_MP, self.mp + C.MP_REGEN * dt)

        if self.attack_t >= 0:
            self.attack_t += dt
            if self.attack_t > 0.5:
                self.attack_t = -1
        left = keys[pygame.K_LEFT]
        right = keys[pygame.K_RIGHT]
        up = keys[pygame.K_UP]
        down = keys[pygame.K_DOWN]
        jump_press = "jump" in events
        attack_press = "attack" in events
        skill_press = "skill" in events

        # ---- 轻功(A键/虚拟键盘skill) ----------------------------------------
        if skill_press:
            self.cast_haste(global_fx)

        # ---- attack -------------------------------------------------------
        # 原版行为: 近身有怪 → 挥拳(微量伤害且吸引仇恨, 与MP无关);
        #           非近身 → 双飞斩(MP无限模式不耗魔)
        if attack_press and self.cooldown <= 0 and self.climb is None and self.kb_t <= 0:
            if self.on_ground and left:
                self.face = -1
            elif self.on_ground and right:
                self.face = 1
            target = combat.nearest_melee(self)
            if target is not None:
                self.attack_t = 0.0
                self.cooldown = C.PUNCH_COOLDOWN
                combat.punch(self, target)
            elif self.mp >= C.L7_MP_COST:
                self.attack_t = 0.0
                self.mp -= C.L7_MP_COST
                self.cooldown = C.ATTACK_COOLDOWN
                self.star_queue = C.STAR_COUNT
                self.star_timer = 0.12
                combat.slash(self)
        if self.star_queue > 0:
            self.star_timer -= dt
            if self.star_timer <= 0:
                self.star_queue -= 1
                self.star_timer = C.STAR_INNER_DELAY
                combat.throw_star(self)

        # ---- climbing -----------------------------------------------------
        if self.climb is not None:
            rope = self.climb
            self.x = float(rope["x"])
            self.vy = 0
            if up:
                self.y -= C.CLIMB_SPEED * dt
            elif down:
                self.y += C.CLIMB_SPEED * dt
            self.y = max(rope["y1"], min(rope["y2"], self.y))
            self.state = "climb"
            if self.y <= rope["y1"] + 0.5 and up:
                self.climb = None
                self.on_ground = True
                gy = self.world.ground_y(self.x, self.y - 6, 0, prev_y=self.y - 6)
                if gy is not None:
                    self.y = gy
                    self.fh_id = self.world.fh_id_at_y(self.x, self.y)
            if jump_press:
                self.climb = None
                self.vy = self.jump_v() * 0.85
                self.on_ground = False
                self.state = "jump"
                self.fh_id = None
            if down and self.y >= rope["y2"] - 0.5:
                self.climb = None
                self.on_ground = False
                self.state = "jump"
                self.fh_id = None
            return

        # ---- horizontal ---------------------------------------------------
        speed = self.speed()
        if self.kb_t > 0:
            self.kb_t -= dt
            self.x += self.vx * dt
        else:
            # 击退硬直(0.28s)结束后即可自由行动(无敌闪烁不再锁操作, 同原版)
            if left and not right:
                self.x -= speed * dt
                if not self.busy_attack():
                    self.face = -1
            elif right and not left:
                self.x += speed * dt
                if not self.busy_attack():
                    self.face = 1
        self.x = max(self.world.meta["cameraBounds"]["left"] + 12,
                     min(self.world.meta["cameraBounds"]["right"] + C.VIEW_W - 12, self.x))

        # ---- vertical -----------------------------------------------------
        if self.on_ground:
            gy = None
            if self.fh_id is not None:
                gy = self.world.fh_on(self.x, self.y, self.fh_id)
            if gy is None:
                # 走出当前foothold：尝试平齐转移，否则走出边缘下落
                nid = self.world.fh_id_at_y(self.x, self.y)
                if nid is not None:
                    self.fh_id = nid
                    gy = self.world.fh_on(self.x, self.y, nid)
                else:
                    self.on_ground = False
                    self.vy = 0
                    self.state = "jump"
                    self.fh_id = None
            if gy is not None:
                self.y = gy
            if jump_press and not down and self.kb_t <= 0:
                self.vy = self.jump_v()
                self.on_ground = False
                self.state = "jump"
                self.fh_id = None
            elif jump_press and down and self.kb_t <= 0:
                # 下跳：仅当脚下确有更低平台（原版底层禁止下跳）
                if self.world.drop_target(self.x, self.y) is not None:
                    self.drop_fh = self.fh_id
                    self.on_ground = False
                    self.vy = C.DOWNJUMP_V
                    self.state = "jump"
                    self.drop_t = 0.26
                    self.fh_id = None
        else:
            self.drop_t = max(0.0, self.drop_t - dt)
            prev_y = self.y
            self.vy = min(C.MAX_FALL, self.vy + C.GRAVITY * dt)
            self.y += self.vy * dt
            gy = self.world.ground_y(self.x, self.y, self.vy,
                                     drop_through=self.drop_fh if self.drop_t > 0 else None,
                                     prev_y=prev_y)
            if self.vy > 0 and gy is not None:
                self.y = gy
                self.vy = 0
                self.on_ground = True
                self.state = "stand"
                self.fh_id = self.world.fh_id_at_y(self.x, self.y)
                self.drop_t = 0
                self.drop_fh = None
            # 空中抓绳（跳跃中按↑, 上升/下落段均可, 同原版）
            if up and self.climb is None:
                r = self.world.rope_near(self.x, self.y)
                if r and r["y1"] - 6 <= self.y <= r["y2"] + 6:
                    self.climb = r
                    self.vy = 0
                    self.x = float(r["x"])
                    self.state = "climb"

        # ---- rope attach --------------------------------------------------
        if (up or down) and self.climb is None and self.on_ground and self.kb_t <= 0:
            r = self.world.rope_near(self.x, self.y)
            if r:
                if (up and self.y > r["y1"] + 4) or (down and self.y < r["y2"] - 4):
                    self.climb = r
                    self.on_ground = False
                    self.fh_id = None
                    self.x = float(r["x"])
                    return

        # ---- states -------------------------------------------------------
        # "hit"仅表示击退硬直期; 无敌闪烁期(2.2s)可自由走/跳/攻击, 仅渲染上闪烁
        if self.kb_t > 0:
            self.state = "hit"
        elif self.state == "hit":
            self.state = "stand"
        if self.kb_t <= 0:
            if self.climb is not None:
                self.state = "climb"
            elif not self.on_ground:
                self.state = "jump"
            elif down and self.drop_t <= 0 and self.world.drop_target(self.x, self.y) is None:
                self.state = "prone"
            elif left or right:
                self.state = "walk"
            else:
                self.state = "stand"

    # ---- damage -----------------------------------------------------------
    def take_hit(self, dmg, from_x):
        if self.inv_t > 0:
            return False
        self.hp = max(1, self.hp - dmg)
        self.inv_t = C.INVINCIBLE_TIME
        self.no_dmg_t = 0
        self.state = "hit"
        self.kb_t = C.KNOCKBACK_TIME
        # 原版击退规则(录屏取证): 方向=远离怪物; 与怪物同x时退向背后(与朝向相反)
        if self.x > from_x:
            self.vx = C.KNOCKBACK_V
        elif self.x < from_x:
            self.vx = -C.KNOCKBACK_V
        else:
            self.vx = -C.KNOCKBACK_V * self.face
        return True

    # ---- render -----------------------------------------------------------
    def draw(self, surface, cam_x, cam_y):
        if self.inv_t > 0 and (int(self.inv_t * 10) % 2 == 0):
            return  # 受击闪烁
        sheet = self.sheets
        if self.state == "climb":
            frames, dur = sheet["climb"], 0.22
            img = sprites.load_frame(frames[int(self.anim_t / dur) % len(frames)])
            fx = -img.get_width() / 2
        else:
            if self.state == "walk":
                frames, dur = sheet["walk"], 0.12
            elif self.state == "jump":
                frames, dur = sheet["jump"], 1
            elif self.state == "prone":
                frames, dur = sheet["prone"], 1
            elif self.state == "hit":
                frames, dur = sheet["hit"], 1
            elif self.busy_attack():
                frames, dur = sheet["attack"], 0.08
            else:
                frames, dur = sheet["stand"], 1
            if not frames:
                return
            if self.busy_attack() and self.face == 1:
                img = sprites.flip_frame(frames[min(int(self.attack_t / dur), len(frames) - 1)])
            else:
                idx = int(self.anim_t / dur) % len(frames) if self.state in ("walk",) else \
                    (min(int(self.attack_t / dur), len(frames) - 1) if self.busy_attack() else 0)
                p = frames[idx]
                img = sprites.flip_frame(p) if self.face == 1 else sprites.load_frame(p)
            fx = -img.get_width() / 2
        fy = -img.get_height()
        surface.blit(img, (int(self.x - cam_x + fx), int(self.y - cam_y + fy)))
