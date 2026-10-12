#!/usr/bin/env python3
"""视觉识别（名牌定位方案）：
每个单位脚下都有系统绘制、永不遮挡的名字牌。
- 怪物: 各类型名牌全图NCC(0.95+) → 类型+脚底位置
- 玩家: 白名牌右半片段 / 宠物名牌中点推断 / 身体颜色签名 三级回退
辅助: HUD条像素测MP/HP, 伤害数字/MISS检测。
"""
import os

import pygame
import numpy as np
from PIL import Image
from scipy import ndimage
from scipy.signal import fftconvolve

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLATE_DIR = f"{R}/vision/plates"
MOB_IDS = [2220100, 3000004, 3210800, 5130101]
MOB_NAME = {2220100: "蓝蘑菇", 3000004: "蝴蝶精", 3210800: "猴子", 5130101: "石头人"}
# 小地图几何(与游戏UI一致): 面板内canvas原点/尺寸 ↔ 世界范围
MM_OX, MM_OY, MM_W, MM_H = 4, 6, 117, 88
MM_WORLD = {"left": -347, "top": -521, "w": 1501, "h": 1177}


def _gray(a):
    return a.astype(np.float32).mean(axis=2)


def _ncc_peaks(img_gray, tpl_gray, th, min_dist=24, std_min=4.0):
    corr = fftconvolve(img_gray, tpl_gray[::-1, ::-1], mode="valid")
    ones = np.ones_like(tpl_gray)
    m1 = fftconvolve(img_gray, ones, mode="valid")
    m2 = fftconvolve(img_gray ** 2, ones, mode="valid")
    n = tpl_gray.size
    var = np.maximum(m2 - m1 ** 2 / n, 1e-3)
    tnorm = np.sqrt(((tpl_gray - tpl_gray.mean()) ** 2).sum())
    nccm = (corr - m1 * tpl_gray.mean()) / (np.sqrt(var) * tnorm + 1e-6)
    nccm[np.sqrt(var) / np.sqrt(n) < std_min] = -2.0
    if tnorm / np.sqrt(n) < std_min:
        return []
    out = []
    work = nccm.copy()
    while True:
        j, i = np.unravel_index(np.argmax(work), work.shape)
        s = float(work[j, i])
        if s < th:
            break
        out.append((int(i), int(j), s))
        y0, y1 = max(0, j - min_dist), min(work.shape[0], j + min_dist)
        x0, x1 = max(0, i - min_dist), min(work.shape[1], i + min_dist)
        work[y0:y1, x0:x1] = -2.0
    return out


def _make_plate(text, style, font):
    t = font.render(text, True, (20, 20, 20) if style == "player" else (255, 255, 255))
    w, h = t.get_width() + 8, t.get_height() + 3
    surf = pygame.Surface((w, h), pygame.SRCALPHA)
    fill = {"player": (245, 245, 235), "mob": (40, 40, 48), "pet_o": (235, 150, 40)}[style]
    tc = (30, 30, 30) if style == "player" else (255, 255, 255)
    pygame.draw.rect(surf, fill, (0, 0, w, h), border_radius=3)
    pygame.draw.rect(surf, (20, 20, 20), (0, 0, w, h), 1, border_radius=3)
    surf.blit(font.render(text, True, tc), (4, 1))
    return _gray(np.asarray(pygame.surfarray.array3d(surf).transpose(1, 0, 2), np.float32))


class Detector:
    def __init__(self):
        self.plates = {}
        for name in ["player"] + [f"mob_{i}" for i in MOB_IDS]:
            p = f"{PLATE_DIR}/{name}.png"
            if os.path.exists(p):
                im = Image.open(p).convert("RGB")
                self.plates[name] = _gray(np.asarray(im, np.float32))
        if "player" in self.plates:
            full = self.plates["player"]
            self.plates["player_frag"] = full[:, full.shape[1] // 2:].copy()
            # 名牌中段纯白条(宠物两侧遮挡时中段仍露出)
            self.plates["player_strip"] = full[:, 26:62].copy()
        import pygame
        from game.ui import _cn_font
        if not pygame.get_init():
            pygame.init()
            pygame.display.set_mode((1, 1))
        font = _cn_font(12)
        self.plates["pet_snow"] = _make_plate("小白雪人", "player", font)
        self.plates["pet_mush"] = _make_plate("花蘑菇仔", "pet_o", font)
        self.prev_player = None
        self.prev_pets = {}
        self.lost_frames = 0

    # ---- 名牌全图匹配 ----
    def _find_plate(self, gfull, tpl, th=0.6):
        gfull = gfull.copy()
        gfull[:100, :130] = 0        # 小地图
        gfull[:75, 1030:] = 0        # 任务栏/角标
        h, w = gfull.shape
        small = np.asarray(
            Image.fromarray(gfull.astype(np.uint8)).resize((w // 2, h // 2)), np.float32)
        ts = np.asarray(
            Image.fromarray(tpl.astype(np.uint8)).resize(
                (max(2, tpl.shape[1] // 2), max(2, tpl.shape[0] // 2))), np.float32)
        peaks = _ncc_peaks(small, ts, th - 0.08, min_dist=14)
        out = []
        for px, py, s in peaks[:6]:
            cx, cy = px * 2, py * 2
            x0, y0 = max(0, cx - 8), max(0, cy - 8)
            sub = gfull[y0:y0 + tpl.shape[0] + 16, x0:x0 + tpl.shape[1] + 16]
            ref = _ncc_peaks(sub, tpl, th, min_dist=6)
            if ref:
                bx, by, bs = max(ref, key=lambda p: p[2])
                out.append((x0 + bx + tpl.shape[1] // 2, y0 + by + tpl.shape[0] // 2, bs))
        return out

    def find_mobs(self, frame):
        gfull = _gray(frame[:640].astype(np.float32))
        out = []
        for mid in MOB_IDS:
            tpl = self.plates.get(f"mob_{mid}")
            if tpl is None:
                continue
            for cx, cy, s in self._find_plate(gfull, tpl, th=0.68):
                out.append({"type": mid, "name": MOB_NAME[mid],
                            "x": int(cx), "y": int(cy - 10),   # 牌中心=脚底+10
                            "score": round(s, 3)})
        return out

    def find_pets(self, frame):
        gfull = _gray(frame[:640].astype(np.float32))
        pets = {}
        for pet, tpl in (("snow", self.plates.get("pet_snow")),
                         ("mush", self.plates.get("pet_mush"))):
            if tpl is None:
                continue
            th = 0.75
            if self.prev_pets.get(pet) is not None:
                # 位置持久: 只在上次位置附近找
                lx, ly = self.prev_pets[pet]
                sub = gfull[max(0, ly - 70):ly + 30, max(0, lx - 90):lx + 90]
                offx, offy = max(0, lx - 90), max(0, ly - 70)
                peaks = [(p[0] + offx, p[1] + offy, p[2]) for p in self._find_plate(sub, tpl, th=0.5)]
            else:
                peaks = self._find_plate(gfull, tpl, th=th)
            if peaks:
                cx, cy, s = max(peaks, key=lambda p: p[2])
                self.prev_pets[pet] = (cx, cy)
                pets[pet] = {"x": int(cx), "y": int(cy - 8), "score": round(s, 3)}
            else:
                self.prev_pets[pet] = None
        return pets

    def _accept_player(self, res):
        if res is None:
            return None
        if self.prev_player is not None:
            lx, ly = self.prev_player
            if abs(res["x"] - lx) > 250 or abs(res["y"] - ly) > 220:
                return None                      # 野值: 与上一帧严重不符
        return res

    def find_player(self, frame):
        gfull = _gray(frame[:640].astype(np.float32))
        # 跟踪丢失过久 → 全图重搜
        if self.prev_player is None or self.lost_frames > 15:
            return self._find_player_global(gfull, frame)
        lx, ly = self.prev_player
        cx0, cy0 = int(min(max(lx, 360), 920)), int(min(max(ly, 260), 560))
        x0, y0 = max(0, cx0 - 170), max(0, cy0 - 150)
        sub = gfull[y0:y0 + 300, x0:x0 + 340]
        # 1) 玩家完整白名牌
        tpl = self.plates.get("player")
        if tpl is not None:
            peaks = self._find_plate(sub, tpl, th=0.68)
            if peaks:
                cx, cy, s = max(peaks, key=lambda p: p[2])
                pos = (cx + x0, cy + y0 - 8)
                res = {"x": int(pos[0]), "y": int(pos[1]), "score": round(s, 3), "via": "player"}
                acc = self._accept_player(res)
                if acc:
                    self.prev_player = (acc["x"], acc["y"])
                return acc
        # 2) 名牌中段白条 / 右半片段
        for key, th2 in (("player_strip", 0.62), ("player_frag", 0.5)):
            tpl = self.plates.get(key)
            if tpl is None:
                continue
            peaks = self._find_plate(sub, tpl, th=th2)
            if peaks:
                cx, cy, s = max(peaks, key=lambda p: p[2])
                pos = (cx + x0, cy + y0 - 8)
                res = {"x": int(pos[0]), "y": int(pos[1]), "score": round(s, 3), "via": key}
                acc = self._accept_player(res)
                if acc:
                    self.prev_player = (acc["x"], acc["y"])
                    self.lost_frames = 0
                return acc
        tpl = self.plates.get("player_frag")
        if tpl is not None:
            peaks = self._find_plate(sub, tpl, th=0.5)
            if peaks:
                cx, cy, s = max(peaks, key=lambda p: p[2])
                pos = (cx + x0, cy + y0 - 8)
                res = {"x": int(pos[0]), "y": int(pos[1]), "score": round(s, 3), "via": "player_frag"}
                acc = self._accept_player(res)
                if acc:
                    self.prev_player = (acc["x"], acc["y"])
                return acc
        # 3) 宠物名牌中点(宠物始终贴身跟随, 单只也可推断)
        pets = self.find_pets(frame)
        if len(pets) == 2:
            ps = [pets["snow"], pets["mush"]]
            mx = (ps[0]["x"] + ps[1]["x"]) / 2
            my = max(ps[0]["y"], ps[1]["y"])
            pos = (mx, my)
            res = {"x": int(mx), "y": int(my), "score": 0.85, "via": "pets"}
            acc = self._accept_player(res)
            if acc:
                self.prev_player = (acc["x"], acc["y"])
            return acc
        # 3.5) 宠物名牌中点(两只都在 → 角色在中间)
        pets = self.find_pets(frame)
        if len(pets) == 2:
            ps = [pets["snow"], pets["mush"]]
            mx = (ps[0]["x"] + ps[1]["x"]) / 2
            my = max(ps[0]["y"], ps[1]["y"])
            self.prev_player = (mx, my)
            self.lost_frames = 0
            return {"x": int(mx), "y": int(my), "score": 0.85, "via": "pets"}
        # 3.7) 武器签名(全图, 但由丢失计数触发)
        if self.lost_frames > 6:
            w = self._find_player_by_weapon(frame)
            if w is not None:
                self.prev_player = (w["x"], w["y"])
                self.lost_frames = 0
                return w
        # 4) 身体颜色签名(最后回退)
        p = self._accept_player(self._find_player_body(frame))
        if p is not None:
            self.prev_player = (p["x"], p["y"])
            self.lost_frames = 0
            return p
        self.lost_frames += 1
        return None

    def _find_player_by_weapon(self, frame):
        """红拳套+深蓝身体签名(红/粉在场景中极稀有), 全图。"""
        search = frame[:640].copy()
        search[:100, :130] = 0          # 小地图
        search[:75, 1030:] = 0          # 任务栏
        search[545:, 1080:] = 0         # 快捷栏
        search[640 - 84:, :] = 0        # 底部HUD/聊天
        search[250:440, :80] = 0        # 左挂件
        r = search[:, :, 0].astype(np.int16)
        g = search[:, :, 1].astype(np.int16)
        b = search[:, :, 2].astype(np.int16)
        red = (r > 140) & (g < 95) & (b < 95)
        red = ndimage.binary_opening(red, iterations=1)
        lab, n = ndimage.label(red)
        for i, sl in enumerate(ndimage.find_objects(lab), 1):
            pw = int((lab[sl] == i).sum())
            if not (8 <= pw <= 500):
                continue
            cx = (sl[1].start + sl[1].stop) // 2
            cy = sl[0].stop
            y0, y1 = max(0, sl[0].start - 50), min(search.shape[0], sl[0].stop + 12)
            x0, x1 = max(0, sl[1].start - 40), min(search.shape[1], sl[1].stop + 40)
            win = search[y0:y1, x0:x1]
            wr = win[:, :, 0].astype(np.int16)
            wg = win[:, :, 1].astype(np.int16)
            wb = win[:, :, 2].astype(np.int16)
            navy = ((wr < 95) & (wg < 100) & (wb >= wr) & (wb > 35) & (wb < 175)).sum()
            white = ((wr > 185) & (wg > 185) & (wb > 185)).sum()
            if navy >= 150 and white >= 15:
                # 脚底 ≈ 红簇中心y + 20
                return {"x": int(cx), "y": int(cy + 22), "score": 0.9, "via": "weapon"}
        return None

    def _find_player_global(self, gfull, frame):
        """全图搜索(首次/丢失过久): 武器签名 → 宠物推断 → 名牌 → 颜色。"""
        w = self._find_player_by_weapon(frame)
        if w is not None:
            self.prev_player = (w["x"], w["y"])
            self.lost_frames = 0
            return w
        gfull_ui = gfull.copy()
        gfull_ui[:100, :130] = 0        # 小地图
        gfull_ui[:75, 1030:] = 0        # 任务栏/角标
        gfull_ui[545:, 1080:] = 0       # 快捷栏/拾取提示
        gfull_ui[250:440, :80] = 0      # 左侧挂件
        # 1) 名牌中段白条(两侧被宠物牌遮时中段仍露出)
        tpl = self.plates.get("player_strip")
        if tpl is not None:
            peaks = self._find_plate(gfull_ui, tpl, th=0.5)
            if peaks:
                cx, cy, s = max(peaks, key=lambda p: p[2])
                self.prev_player = (cx, cy - 8)
                self.lost_frames = 0
                return {"x": int(cx), "y": int(cy - 8), "score": round(s, 3), "via": "strip_global"}
        # 2) 宠物名牌推断(需高阈值, 避免花丛假匹配)
        pets = self.find_pets(frame)
        expected = None
        if len(pets) == 2:
            expected = ((pets["snow"]["x"] + pets["mush"]["x"]) / 2,
                        max(pets["snow"]["y"], pets["mush"]["y"]))
        elif len(pets) == 1:
            if "snow" in pets:
                expected = (pets["snow"]["x"] + 52, pets["snow"]["y"])
            else:
                expected = (pets["mush"]["x"] - 55, pets["mush"]["y"])
        if expected is not None:
            self.prev_player = expected
            self.lost_frames = 0
            return {"x": int(expected[0]), "y": int(expected[1]), "score": 0.75, "via": "pets_global"}
        # 3) 名牌片段
        tpl = self.plates.get("player_frag")
        tpl = self.plates.get("player_frag")
        if tpl is not None:
            peaks = self._find_plate(gfull_ui, tpl, th=0.5)
            if peaks:
                cx, cy, s = max(peaks, key=lambda p: p[2])
                self.prev_player = (cx, cy - 8)
                self.lost_frames = 0
                return {"x": int(cx), "y": int(cy - 8), "score": round(s, 3), "via": "frag_global"}
        tpl = self.plates.get("player")
        if tpl is not None:
            peaks = self._find_plate(gfull_ui, tpl, th=0.68)
            if peaks:
                cx, cy, s = max(peaks, key=lambda p: p[2])
                self.prev_player = (cx, cy - 8)
                self.lost_frames = 0
                return {"x": int(cx), "y": int(cy - 8), "score": round(s, 3), "via": "player_global"}
        p = self._find_player_body(frame)
        if p is not None:
            self.prev_player = (p["x"], p["y"])
            self.lost_frames = 0
            return p
        self.lost_frames += 1
        return None

    def _find_player_body(self, frame):
        search = frame[160:620, 130:1030].copy()
        oy, ox = 160, 130
        r = search[:, :, 0].astype(np.int16)
        g = search[:, :, 1].astype(np.int16)
        b = search[:, :, 2].astype(np.int16)
        white = (r > 185) & (g > 185) & (b > 185)
        navy = (r < 95) & (g < 100) & (b >= r) & (b > 35) & (b < 175)
        cm = ndimage.binary_closing(white | navy, structure=np.ones((3, 3)))
        lab, n = ndimage.label(cm)
        best, bs = None, 0.0
        for i, sl in enumerate(ndimage.find_objects(lab), 1):
            ch = sl[0].stop - sl[0].start; cw = sl[1].stop - sl[1].start
            if not (24 <= ch <= 90 and 8 <= cw <= 60):
                continue
            comp = (lab[sl] == i)
            if comp.sum() < 100:
                continue
            cr = search[sl][:, :, 0].astype(np.int16)
            cg = search[sl][:, :, 1].astype(np.int16)
            cb = search[sl][:, :, 2].astype(np.int16)
            top_w = ((cr > 185) & (cg > 185) & (cb > 185))[:max(1, ch // 3)].sum()
            body_n = ((cr < 95) & (cg < 100) & (cb >= cr) & (cb > 35) & (cb < 175))[ch // 3:].sum()
            if top_w < 20 or body_n < 80:
                continue
            sc = min(top_w / 50, 1.2) + min(body_n / 260, 1.2)
            if sc > bs:
                bs = sc
                best = (sl[1].start + cw // 2 + ox, sl[0].stop + oy)
        if best is None or bs < 1.0:
            return None
        return {"x": best[0], "y": best[1], "score": round(bs, 2), "via": "color"}

    def player_world_from_minimap(self, frame):
        """小地图上的橙色玩家箭头 → 世界坐标(纯视觉)。
        只搜索画布下半区(y≥48), 排除标题文字与"大地图"橙色按钮。"""
        y_top = MM_OY + 42
        reg = frame[y_top:MM_OY + MM_H, MM_OX:MM_OX + MM_W].astype(np.int16)
        r, g, b = reg[:, :, 0], reg[:, :, 1], reg[:, :, 2]
        # 箭头是ui.py绘制的纯色(255,170,40)三角, 画布无同色 → 精确掩码唯一
        m = (r >= 246) & (abs(g - 170) <= 14) & (b <= 70)
        lab, n = ndimage.label(m)
        best, bscore = None, -1.0
        for i, sl in enumerate(ndimage.find_objects(lab), 1):
            comp = (lab[sl] == i)
            px = int(comp.sum())
            if px < 2:
                continue
            pix = reg[sl][comp].astype(np.float32)
            sat = float((pix[:, 0] - pix[:, 2]).mean())    # 橙箭头 r-b≈215, 干扰更低
            score = sat * min(px, 30)
            if score > bscore:
                bscore = score
                ys, xs = np.nonzero(comp)
                best = (xs.mean() + MM_OX + sl[1].start, ys.mean() + y_top + sl[0].start)
        if best is None:
            return None
        mx, my = best
        wx = MM_WORLD["left"] + (mx - MM_OX) / MM_W * MM_WORLD["w"]
        wy = MM_WORLD["top"] + (my - MM_OY) / MM_H * MM_WORLD["h"]
        return {"x": float(wx), "y": float(wy)}

    # ---- 轻功buff图标(右上角) ----
    @staticmethod
    def haste_icon(frame):
        """右上角轻功图标检测: 青翅色块像素计数。
        返回 (present, score)。图标约150+青色像素, 阈值40。"""
        reg = frame[5:50, 1225:1280].astype(np.int16)
        if reg.size == 0:
            return False, 0
        r = reg[:, :, 0]
        g = reg[:, :, 1]
        b = reg[:, :, 2]
        cyan = (b > 180) & (g > 165) & (b - r > 55) & (g - r > 35)
        score = int(cyan.sum())
        return score > 40, score

    # ---- HUD ----
    @staticmethod
    def hud_bars(frame):
        def ratio(x0, x1, mask_fn):
            reg = frame[701:719, x0:x1]
            m = mask_fn(reg)
            cols = m.sum(axis=0)
            filled = np.nonzero(cols > 4)[0]
            if len(filled) == 0:
                return 0.0
            return float((filled.max() - filled.min() + 1) / (x1 - x0))
        r = frame[:, :, 0].astype(np.int16)
        g = frame[:, :, 1].astype(np.int16)
        b = frame[:, :, 2].astype(np.int16)
        mp = ratio(600, 745, lambda a: (a[:, :, 2] > 150) & (a[:, :, 0] < 90))
        hp = ratio(439, 591, lambda a: (a[:, :, 0] > 150) & (a[:, :, 1] < 70))
        return mp, hp

    @staticmethod
    def effects_near(frame, px, py):
        y0, y1 = max(0, py - 120), min(frame.shape[0], py + 30)
        x0, x1 = max(0, px - 110), min(frame.shape[1], px + 110)
        reg = frame[y0:y1, x0:x1].astype(np.int16)
        r, g, b = reg[:, :, 0], reg[:, :, 1], reg[:, :, 2]
        pink = (r > 150) & (r - g > 45) & (b < 200)
        purple = (b > 120) & (b - g > 40) & (r > 60)
        return {"damage_number": int(pink.sum()) > 60,
                "miss": int(purple.sum()) > 80}
