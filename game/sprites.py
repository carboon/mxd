#!/usr/bin/env python3
"""Sprite/animation registry: player sheets, mob sheets from WZ-extracted PNGs."""
import json
import os

import pygame

from . import config as C

_frame_cache = {}


def load_frame(path):
    if path not in _frame_cache:
        _frame_cache[path] = pygame.image.load(path).convert_alpha()
    return _frame_cache[path]


def flip_frame(path):
    key = "flip:" + path
    if key not in _frame_cache:
        _frame_cache[key] = pygame.transform.flip(load_frame(path), True, False)
    return _frame_cache[key]


class Anim:
    """frames = [(path, flip)] with per-frame duration."""

    def __init__(self, frames, dur, loop=True):
        self.paths = frames
        self.dur = dur
        self.loop = loop

    def frame(self, path_getter, t):
        if not self.paths:
            return None
        i = int(t / self.dur)
        if self.loop:
            i %= len(self.paths)
        else:
            i = min(i, len(self.paths) - 1)
        return path_getter(self.paths[i])


def _mob_frames(mid, action, n, flip=False):
    getter = flip_frame if flip else load_frame
    return [f"{C.ROOT}/assets/mobs/{mid}/{action}_{i}.png" for i in range(n)]


class MobSkin:
    def __init__(self, mid, manifest):
        self.mid = mid
        acts = manifest[str(mid)]
        self.stand = _mob_frames(mid, "stand", acts.get("stand", 1))
        self.move = _mob_frames(mid, "move", acts.get("move", acts.get("walk", 1)))
        self.hit = _mob_frames(mid, "hit1", acts.get("hit1", 1))
        self.die = _mob_frames(mid, "die1", acts.get("die1", 3))


def load_mob_skins():
    with open(f"{C.ROOT}/assets/mobs/frames_manifest.json") as f:
        manifest = json.load(f)
    return {int(k): MobSkin(k, manifest) for k in manifest}


def load_player_sheets():
    p = f"{C.ROOT}/assets/player"
    s = {
        "stand": [f"{p}/stand.png"],
        "walk": [f"{p}/walk1.png", f"{p}/stand.png", f"{p}/walk2.png", f"{p}/stand.png"],
        "jump": [f"{p}/jump.png"],
        "climb": [f"{p}/climb1.png", f"{p}/climb2.png"],
        "prone": [f"{p}/prone.png"],
        "hit": [f"{p}/hit.png"],
        "attack": [f"{p}/atkL_{f}.png" for f in (217, 218, 219, 220, 221, 223)],
    }
    for k in list(s):
        s[k] = [f for f in s[k] if os.path.exists(f)]
    return s


def load_pet_sheets():
    p = f"{C.ROOT}/assets/pets"
    return {
        "mush": [f"{p}/mush_walkA.png", f"{p}/mush_walkB.png"],
        "yeti": [f"{p}/yeti_idle.png"],
    }


def load_fx():
    p = f"{C.ROOT}/assets/fx"
    out = {}
    for name, fn in [("miss", "miss_clean.png"), ("star", "star_projectile.png")]:
        fp = os.path.join(p, fn)
        if os.path.exists(fp):
            out[name] = pygame.image.load(fp).convert_alpha()
    return out


_white_cache = {}


def _white_of(frame, strength=200):
    """白色剪影（受击闪白），按surface对象缓存。"""
    key = id(frame)
    im = _white_cache.get(key)
    if im is None:
        import numpy as np
        w, h = frame.get_size()
        arr = pygame.surfarray.array3d(frame).transpose(1, 0, 2).copy()
        alpha = pygame.surfarray.array_alpha(frame).transpose(1, 0).copy()
        arr[:, :, 0] = strength; arr[:, :, 1] = strength; arr[:, :, 2] = strength
        rgba = np.ascontiguousarray(np.dstack([arr, alpha]))
        im = pygame.image.frombuffer(rgba.tobytes(), (w, h), "RGBA").convert_alpha()
        _white_cache[key] = im
    return im
