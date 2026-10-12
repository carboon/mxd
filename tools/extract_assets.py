#!/usr/bin/env python3
"""Extract all game assets from video frames using camera track + bg-diff alpha.
Hand-locked rects only. Outputs into assets/ with a verification contact sheet."""
import json, os
import numpy as np
from PIL import Image
from scipy import ndimage

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRAMES = "/tmp/mxd_all"
files = sorted(os.listdir(FRAMES))
TRACK = json.load(open(f"{R}/data/camera_track.json"))
TB = json.load(open(f"{R}/data/trackB.json"))
BG = np.asarray(Image.open(f"{R}/assets/map/106010104_bg.png").convert("RGB"), np.int16)
MAP_X, MAP_Y = -347, -353

def frame(i):
    return np.asarray(Image.open(f"{FRAMES}/{files[i]}").convert("RGB"), np.int16)

def cam(i):
    d = TRACK[i]
    return d["camX"], d["camY"]

def cutout(i, rect, thr=70, close=1, min_px=25):
    """rect in screen coords; alpha = |frame-bg|>thr within rect."""
    x, y, w, h = rect
    frgb = frame(i)
    ox, oy = cam(i)
    bx, by = x + ox - MAP_X, y + oy - MAP_Y
    sub = frgb[y:y+h, x:x+w].astype(np.int16)
    bgsub = np.zeros_like(sub)
    sx0, sy0 = max(0, -bx), max(0, -by)
    bx0, by0 = max(0, bx), max(0, by)
    ww = min(w - sx0, BG.shape[1] - bx0); hh = min(h - sy0, BG.shape[0] - by0)
    if ww > 0 and hh > 0:
        bgsub[sy0:sy0+hh, sx0:sx0+ww] = BG[by0:by0+hh, bx0:bx0+ww]
    d = np.abs(sub - bgsub).sum(axis=2)
    m = d > thr
    if close:
        m = ndimage.binary_closing(m, iterations=close)
        m = ndimage.binary_opening(m, iterations=1)
    lab, n = ndimage.label(m)
    if n > 1:
        sizes = ndimage.sum(m, lab, range(1, n+1))
        keep = np.argmax(sizes) + 1
        m = lab == keep
        # keep secondary components > 15% of main (weapon, hat brim)
        for k in range(1, n+1):
            if k != keep and sizes[k-1] > min_px:
                m |= lab == k
    if m.sum() < min_px:
        return None
    a = ndimage.binary_dilation(m, iterations=1)
    out = np.dstack([sub.astype(np.uint8), (a*255).astype(np.uint8)])
    ys, xs = np.nonzero(a)
    y0a, y1a, x0a, x1a = ys.min(), ys.max()+1, xs.min(), xs.max()+1
    return Image.fromarray(out[y0a:y1a, x0a:x1a], "RGBA")

def plain(i, rect):
    x, y, w, h = rect
    frgb = frame(i)
    return Image.fromarray(frgb[y:y+h, x:x+w].astype(np.uint8))

os.makedirs(f"{R}/assets/player", exist_ok=True)
os.makedirs(f"{R}/assets/pets", exist_ok=True)
os.makedirs(f"{R}/assets/fx", exist_ok=True)
os.makedirs(f"{R}/assets/ui", exist_ok=True)
report = []

def save(im, path):
    if im is None:
        report.append(("MISS", path)); return
    im.save(path)
    report.append((f"{im.width}x{im.height}", path))

# ---- player attack anim (facing left), trackB rects are (x,y,w,h) template-fixed 70x80
for f in [214, 216, 217, 218, 219, 220, 221, 222, 223, 225, 226, 234, 236, 238, 245, 247]:
    if str(f) in TB:
        x, y, w, h = TB[str(f)]
        save(cutout(f, (x, y, w, h)), f"{R}/assets/player/atkL_{f}.png")
# stand (use pre-attack idle frames if tracked) — fallback below
# walk right #142
save(cutout(142, (828, 385, 77, 73)), f"{R}/assets/player/walkR_142.png")
# attack right on ground #154
save(cutout(154, (895, 430, 105, 90)), f"{R}/assets/player/atkR_154.png")
# hit flicker #39 (visible phase) and #40
save(cutout(39, (820, 370, 80, 90), thr=90), f"{R}/assets/player/hit_39.png")
save(cutout(40, (760, 370, 90, 90), thr=90), f"{R}/assets/player/hit_40.png")

# ---- fx
# star slash streak #225 (red streak across player)
x, y, w, h = TB["225"]
save(plain(225, (x-30, y-10, w+50, 40)), f"{R}/assets/fx/slash.png")
# star burst #250 (right side)
save(plain(250, (1130, 290, 90, 90)), f"{R}/assets/fx/starburst.png")
# projectile crescents #100
save(plain(100, (715, 345, 70, 45)), f"{R}/assets/fx/star_blue.png")
save(plain(236, (690, 240, 80, 50)), f"{R}/assets/fx/star_red.png")
# mob hit puff (white flash on mushroom) #104
save(plain(104, (770, 350, 70, 60)), f"{R}/assets/fx/hitpuff.png")

# ---- pets
save(cutout(115, (1186, 360, 58, 55), thr=80), f"{R}/assets/pets/yeti_a.png")
save(cutout(150, (735, 410, 60, 55), thr=80), f"{R}/assets/pets/yeti_b.png")
save(cutout(76, (858, 383, 60, 62), thr=80), f"{R}/assets/pets/mush_a.png")
save(cutout(70, (920, 385, 60, 62), thr=80), f"{R}/assets/pets/mush_b.png")

# ---- damage digit strips (plain crops, segmented later)
save(plain(101, (790, 308, 90, 40)), f"{R}/assets/fx/dmg_1213.png")
save(plain(250, (480, 95, 185, 85)), f"{R}/assets/fx/dmg_1657_1892.png")
save(plain(40, (680, 275, 90, 45)), f"{R}/assets/fx/dmg_504.png")
save(plain(0, (920, 295, 100, 45)), f"{R}/assets/fx/dmg_1008.png")
# MISS purple #18
save(plain(18, (890, 290, 110, 45)), f"{R}/assets/fx/miss.png")

# ---- UI strips (from frame 224: clean bottom)
save(plain(224, (0, 645, 1280, 79)), f"{R}/assets/ui/hud_bottom.png")
save(plain(224, (0, 0, 130, 98)), f"{R}/assets/ui/minimap_panel.png")
save(plain(224, (1055, 12, 175, 45)), f"{R}/assets/ui/quest.png")
save(plain(224, (1090, 550, 190, 95)), f"{R}/assets/ui/quickslot.png")
# nameplates
save(plain(142, (700, 452, 340, 45)), f"{R}/assets/ui/plates_142.png")
save(plain(86, (490, 590, 130, 25)), f"{R}/assets/ui/plate_mob.png")

for s, p in report:
    print(s, os.path.relpath(p, R))
