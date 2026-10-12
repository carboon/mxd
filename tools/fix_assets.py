#!/usr/bin/env python3
"""Round 2: NCC-locate the player/pets with clean templates, then bg-diff cutout."""
import json, os
import numpy as np
from PIL import Image
from scipy.signal import fftconvolve
from scipy import ndimage

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRAMES = "/tmp/mxd_all"
files = sorted(os.listdir(FRAMES))
TRACK = json.load(open(f"{R}/data/camera_track.json"))
TB = json.load(open(f"{R}/data/trackB.json"))
BG = np.asarray(Image.open(f"{R}/assets/map/106010104_bg.png").convert("RGB"), np.int16)
MAP_X, MAP_Y = -347, -353
_cache = {}

def frame(i):
    if i not in _cache:
        _cache[i] = np.asarray(Image.open(f"{FRAMES}/{files[i]}").convert("RGB"), np.float32)
    return _cache[i]

def cam(i):
    d = TRACK[i]
    return d["camX"], d["camY"]

def ncc(img_rgb, tmpl_rgb):
    t = tmpl_rgb.mean(axis=2); t0 = t - t.mean()
    tn = np.sqrt((t0**2).sum())
    win = img_rgb.mean(axis=2)
    corr = fftconvolve(win, t0[::-1, ::-1], mode="valid")
    ones = np.ones_like(t0)
    m1 = fftconvolve(win, ones, mode="valid")
    m2 = fftconvolve(win**2, ones, mode="valid")
    var = np.maximum(m2 - m1**2/t0.size, 1e-3)
    return (corr - m1*t0.mean()) / (np.sqrt(var)*tn + 1e-3)

def locate(i, tmpl, center, rad=90):
    fr = frame(i)
    h, w = tmpl.shape[:2]
    cx, cy = center
    x0, y0 = max(0, cx-rad), max(0, cy-rad)
    x1, y1 = min(fr.shape[1]-w, cx+rad), min(fr.shape[0]-h, cy+rad)
    nccm = ncc(fr[y0:y1+h, x0:x1+w], tmpl)
    j, k = np.unravel_index(np.argmax(nccm), nccm.shape)
    return x0+k, y0+j, float(nccm[j, k])

def cutout(i, rect, thr=70, min_px=25, keep_parts=True):
    x, y, w, h = [int(v) for v in rect]
    frgb = frame(i).astype(np.int16)
    ox, oy = cam(i)
    bx, by = x + ox - MAP_X, y + oy - MAP_Y
    sub = frgb[y:y+h, x:x+w]
    bgsub = np.zeros_like(sub)
    sx0, sy0 = max(0, -bx), max(0, -by)
    bx0, by0 = max(0, bx), max(0, by)
    ww = min(w - sx0, BG.shape[1] - bx0); hh = min(h - sy0, BG.shape[0] - by0)
    if ww > 0 and hh > 0:
        bgsub[sy0:sy0+hh, sx0:sx0+ww] = BG[by0:by0+hh, bx0:bx0+ww]
    m = np.abs(sub - bgsub).sum(axis=2) > thr
    m = ndimage.binary_closing(m, iterations=2)
    m = ndimage.binary_opening(m, iterations=1)
    lab, n = ndimage.label(m)
    if n == 0:
        return None
    sizes = ndimage.sum(m, lab, range(1, n+1))
    main = int(np.argmax(sizes)) + 1
    out = lab == main
    if keep_parts:
        for k in range(1, n+1):
            if k != main and sizes[k-1] > min_px:
                out |= lab == k
    if out.sum() < min_px:
        return None
    a = ndimage.binary_dilation(out, iterations=1)
    rgb = np.dstack([sub.astype(np.uint8), (a*255).astype(np.uint8)])
    ys, xs = np.nonzero(a)
    return Image.fromarray(rgb[ys.min():ys.max()+1, xs.min():xs.max()+1], "RGBA")

# template from a good attack frame (player occupies most of the 70x80 box)
tx, ty, tw, th = TB["217"]
tmpl_player = frame(217)[ty:ty+th, tx:tx+tw]

def save(im, path):
    if im is None:
        print("MISS", path); return
    im.save(path); print(f"{im.width}x{im.height}", os.path.relpath(path, R))

jobs = [
    # (frame, tmpl, approx center, out, rect_pad)
    (142, tmpl_player, (860, 415), "walkR", (6, 10)),
    (143, tmpl_player, (845, 415), "walkR2", (6, 10)),
    (141, tmpl_player, (875, 415), "walkR3", (6, 10)),
    (36,  tmpl_player, (880, 415), "standA", (6, 10)),
    (38,  tmpl_player, (872, 415), "standB", (6, 10)),
    (154, tmpl_player, (940, 470), "atkR", (8, 12)),
    (39,  tmpl_player, (845, 415), "hitA", (8, 12)),
    (300, tmpl_player, (770, 300), "hitB", (8, 12)),
]
for fi, tp, ctr, name, pad in jobs:
    x, y, s = locate(fi, tp, ctr)
    print(f"#{fi} {name}: locate ({x},{y}) ncc={s:.2f}")
    if s > 0.32:
        save(cutout(fi, (x-pad[0], y-pad[1], tw+2*pad[0], th+2*pad[1])), f"{R}/assets/player/{name}.png")

# pets via pet templates
tm = np.asarray(Image.open(f"{R}/assets/pets/mush_a.png").convert("RGB"), np.float32)
ty2 = np.asarray(Image.open(f"{R}/assets/pets/yeti_a.png").convert("RGB"), np.float32)
pet_jobs = [
    (150, ty2, (770, 440), "yeti_walkA"),
    (154, ty2, (755, 440), "yeti_walkB"),
    (158, ty2, (735, 440), "yeti_walkC"),
    (74,  tm,  (890, 415), "mush_walkA"),
    (78,  tm,  (875, 415), "mush_walkB"),
    (115, ty2, (1210, 390), "yeti_idle"),
]
for fi, tp, ctr, name in pet_jobs:
    h, w = tp.shape[:2]
    x, y, s = locate(fi, tp, ctr, rad=70)
    print(f"#{fi} {name}: locate ({x},{y}) ncc={s:.2f}")
    if s > 0.4:
        save(cutout(fi, (x-2, y-2, w+4, h+4), thr=80), f"{R}/assets/pets/{name}.png")

# fx with corrected rects (plain crops)
def plain(i, rect, path):
    x, y, w, h = rect
    Image.fromarray(frame(i)[y:y+h, x:x+w].astype(np.uint8)).save(path)
    print("fx", path)
plain(250, (1000, 280, 100, 100), f"{R}/assets/fx/starburst.png")
plain(100, (700, 340, 60, 50), f"{R}/assets/fx/star_blue.png")
plain(104, (765, 345, 80, 65), f"{R}/assets/fx/hitpuff.png")
plain(236, (640, 250, 90, 60), f"{R}/assets/fx/star_red.png")
