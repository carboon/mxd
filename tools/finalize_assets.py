#!/usr/bin/env python3
"""Final asset surgery: clean base sprite, synthesize jump/climb/prone/hit,
extract star projectile by color mask, segment damage digits."""
import os
import numpy as np
from PIL import Image
from scipy import ndimage

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
A = f"{R}/assets"

def largest_only(im, thr=40):
    arr = np.array(im, copy=True)
    m = arr[:, :, 3] > thr
    lab, n = ndimage.label(m)
    if n == 0:
        return im
    sizes = ndimage.sum(m, lab, range(1, n + 1))
    main = int(np.argmax(sizes)) + 1
    keep = lab == main
    arr[:, :, 3] = np.where(keep, arr[:, :, 3], 0)
    ys, xs = np.nonzero(arr[:, :, 3] > thr)
    return Image.fromarray(arr[ys.min():ys.max()+1, xs.min():xs.max()+1])

def tight_clean(path, out, thr=40):
    im = Image.open(path).convert("RGBA")
    im = largest_only(im, thr)
    im.save(out)
    return im

# 1) base body from cleanest attack frame
base = tight_clean(f"{A}/player/atkL_217.png", f"{A}/player/base.png")
base.save(f"{A}/player/stand.png")
print("base", base.size)

# 2) walk: legs alternate — split at 62% height, shift lower part ±2px
arr = np.asarray(base).copy()
h, w = arr.shape[:2]
split = int(h * 0.62)
def walk_frame(shift):
    out = arr.copy()
    leg = arr[split:, :, :].copy()
    out[split:, :, :] = 0
    legshifted = np.zeros_like(leg)
    if shift >= 0:
        legshifted[:, shift:, :] = leg[:, :leg.shape[1]-shift, :]
    else:
        legshifted[:, :shift, :] = leg[:, -shift:, :]
    out[split:, :, :] = legshifted
    return Image.fromarray(out)
walk_frame(3).save(f"{A}/player/walk1.png")
walk_frame(-3).save(f"{A}/player/walk2.png")

# 3) jump: legs tucked (remove bottom 12 rows, move up 6)
out = arr.copy()
out[h-12:, :, :] = 0
out[h-24:h-12, :, :] = arr[h-18:h-6, :, :]
Image.fromarray(out).save(f"{A}/player/jump.png")

# 4) climb: erase weapon (red-dominant pixels), then mirror alternately
cl = arr.copy()
r, g, b = cl[:, :, 0].astype(int), cl[:, :, 1].astype(int), cl[:, :, 2].astype(int)
red = (r > 120) & (g < 110) & (r - g > 50) & (arr[:, :, 3] > 0)
red = ndimage.binary_dilation(red, iterations=2)
cl[red] = 0
Image.fromarray(cl).save(f"{A}/player/climb1.png")
Image.fromarray(cl[:, ::-1, :]).save(f"{A}/player/climb2.png")

# 5) prone: rotate base -90 (lying on back), feet to the left
Image.fromarray(np.rot90(arr, k=1)).save(f"{A}/player/prone.png")

# 6) hit: base + white tint
hit = arr.copy().astype(np.float32)
mask = hit[:, :, 3] > 0
hit[:, :, :3][mask] = hit[:, :, :3][mask] * 0.45 + 255 * 0.55
Image.fromarray(hit.astype(np.uint8)).save(f"{A}/player/hit.png")

# 7) atk facing right: mirror the best throw frames
for src, dst in [("atkL_220", "atkR1"), ("atkL_223", "atkR2"), ("atkL_226", "atkR3")]:
    im = Image.open(f"{A}/player/{src}.png").convert("RGBA")
    tight_clean(f"{A}/player/{src}.png", f"{A}/player/{src}_c.png")
    a2 = np.asarray(Image.open(f"{A}/player/{src}_c.png"))
    Image.fromarray(a2[:, ::-1, :]).save(f"{A}/player/{dst}.png")

# 8) star projectile: red/pink pixels from slash.png
sl = np.asarray(Image.open(f"{A}/fx/slash.png").convert("RGBA")).astype(int)
r, g, b = sl[:, :, 0], sl[:, :, 1], sl[:, :, 2]
star = (r > 130) & (r - g > 40) & (sl[:, :, 3] > 60)
star = ndimage.binary_opening(star, iterations=1)
out = np.zeros_like(sl, dtype=np.uint8)
out[:, :, 0] = np.clip(r, 0, 255); out[:, :, 1] = np.clip(g, 0, 255); out[:, :, 2] = np.clip(b, 0, 255)
out[:, :, 3] = np.where(star, 255, 0)
ys, xs = np.nonzero(out[:, :, 3])
if len(ys):
    Image.fromarray(out[ys.min():ys.max()+1, xs.min():xs.max()+1]).save(f"{A}/fx/star_projectile.png")
    print("star projectile", xs.max()-xs.min()+1, ys.max()-ys.min()+1)

# 9) damage digits: segment glyphs from number strips
def segment_digits(path, out_prefix):
    im = np.asarray(Image.open(path).convert("RGB")).astype(int)
    # pink/red digits: r high, g low-ish
    r, g, b = im[:, :, 0], im[:, :, 1], im[:, :, 2]
    m = (r > 140) & (r - g > 50)
    m = ndimage.binary_closing(m, structure=np.ones((3, 3)))
    lab, n = ndimage.label(m)
    boxes = []
    for i, sl in enumerate(ndimage.find_objects(lab), 1):
        if (lab[sl] == i).sum() < 25:
            continue
        boxes.append(sl)
    boxes.sort(key=lambda s: s[1].start)
    outs = []
    for k, sl in enumerate(boxes):
        y0, y1 = sl[0].start, sl[0].stop
        x0, x1 = sl[1].start, sl[1].stop
        glyph = np.zeros((y1-y0, x1-x0, 4), np.uint8)
        sub = m[y0:y1, x0:x1]
        glyph[:, :, :3] = im[y0:y1, x0:x1]
        glyph[:, :, 3] = np.where(sub, 255, 0)
        outs.append(Image.fromarray(glyph))
    return outs

pool = {}
for fn in ["dmg_1213", "dmg_1657_1892", "dmg_504", "dmg_1008"]:
    gl = segment_digits(f"{A}/fx/{fn}.png", fn)
    pool[fn] = gl
    print(fn, "->", len(gl), "glyphs", [g.size for g in gl])
os.makedirs(f"{A}/fx/digits", exist_ok=True)
# 已知数字序列: 1213, 1657/1892(两行), 504, 1008
seq = []
for fn, digits in [("dmg_1213", "1213"), ("dmg_504", "504"), ("dmg_1008", "1008")]:
    if len(pool[fn]) == len(digits):
        for d, im in zip(digits, pool[fn]):
            im.save(f"{A}/fx/digits/{d}.png")
            print("digit", d, im.size)
# 1657/1892 两行:分开后各自segment
im = Image.open(f"{A}/fx/dmg_1657_1892.png")
arr = np.asarray(im).astype(int)
r, g = arr[:, :, 0], arr[:, :, 1]
m = (r > 140) & (r - g > 50)
rowsum = m.sum(axis=1)
rows = np.nonzero(rowsum > 0)[0]
mid = (rows.min() + rows.max()) // 2
for name, y0, y1 in [("1657", rows.min(), mid), ("1892", mid, rows.max()+1)]:
    sub = np.zeros_like(arr, dtype=np.uint8)
    sub[y0:y1] = arr[y0:y1].astype(np.uint8)
    Image.fromarray(sub).save(f"/tmp/dmg_{name}.png")
    gl = segment_digits(f"/tmp/dmg_{name}.png", name)
    if len(gl) == 4:
        for d, g2 in zip(name, gl):
            g2.save(f"{A}/fx/digits/{d}.png")
            print("digit", d, g2.size)

# 10) MISS: purple text mask
im = np.asarray(Image.open(f"{A}/fx/miss.png").convert("RGB")).astype(int)
r, g, b = im[:, :, 0], im[:, :, 1], im[:, :, 2]
m = (b > 120) & (b - g > 40) & (r > 60)
m = ndimage.binary_closing(m, structure=np.ones((3, 3)))
out = np.zeros((*m.shape, 4), np.uint8)
out[:, :, :3] = im
out[:, :, 3] = np.where(m, 255, 0)
ys, xs = np.nonzero(m)
if len(ys):
    Image.fromarray(out[ys.min():ys.max()+1, xs.min():xs.max()+1]).save(f"{A}/fx/miss_clean.png")
    print("miss ok")

# 11) yeti tighter
try:
    im = np.asarray(Image.open(f"{A}/pets/yeti_idle.png").convert("RGBA")).copy()
    h, w = im.shape[:2]
    core = im[h//3:, :, :]   # 上1/3是天空
    Image.fromarray(core).save(f"{A}/pets/yeti_idle2.png")
    print("yeti_idle2", core.shape)
except Exception as e:
    print(e)
print("DONE")
