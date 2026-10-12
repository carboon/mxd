#!/usr/bin/env python3
"""Stitch a world-aligned sky canvas from video frames (pixels where the map
render is transparent). Two-pass mean with outlier rejection, then column
gradient fill for never-seen pixels."""
import json, os
import numpy as np
from PIL import Image

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRAMES = "/tmp/mxd_all"
BG = Image.open(f"{R}/assets/map/106010104_bg.png").convert("RGBA")

MAP_X, MAP_Y = -347, -353
SKY_TOP = -521                    # above observed camera extreme with margin
W0, H0 = 1501, 1009
X0, Y0 = MAP_X, SKY_TOP
W, H = W0, 656 - SKY_Y if False else 656 - SKY_TOP  # to world bottom 656

track = json.load(open(f"{R}/data/camera_track.json"))
bg_rgb = np.asarray(BG, dtype=np.uint8)[:, :, :3]
bg_a = np.asarray(BG, dtype=np.uint8)[:, :, 3]

acc = np.zeros((H, W, 3), np.float32)
cnt = np.zeros((H, W), np.float32)
est = acc.copy()

files = [d["file"] for d in track]

def sky_view(frgb, cam, reject=None):
    """Return (values, mask) of sky-visible pixels in sky-canvas coords.
    Screen-space UI regions are excluded to avoid baking widgets into the sky."""
    ox, oy = cam
    fx = np.arange(1280)[None, :].repeat(724, 0)
    fy = np.arange(724)[:, None].repeat(1280, 1)
    ui = np.zeros((724, 1280), bool)
    ui[0:98, 0:125] = True         # minimap panel
    ui[15:95, 1060:1280] = True    # quest tracker
    ui[0:45, 1140:1280] = True     # corner gadgets
    ui[250:440, 0:75] = True       # left widgets
    ui[645:724, :] = True          # bottom chat + HUD
    ui[565:724, 1095:1280] = True  # quickslots
    ui[470:565, 1075:1280] = True  # pickup notices
    ok = (cx_ok := ((fx + ox - X0 >= 0) & (fx + ox - X0 < W) & (fy + oy - Y0 >= 0) & (fy + oy - Y0 < H)))
    bxx = fx + ox - MAP_X
    byy = fy + oy - MAP_Y
    ok &= (bxx >= 0) & (bxx < W0) & (byy >= 0) & (byy < H0)
    ok &= ~ui
    transp = np.zeros((724, 1280), bool)
    vb = bg_a[byy[ok], bxx[ok]] < 8
    tmp = np.zeros(ok.shape, bool)
    tmp[ok] = vb
    ok = tmp
    vals = np.zeros((724, 1280, 3), np.float32)
    vals[ok] = frgb[ok]
    if reject is not None:
        ok &= reject(frgb)
    # erode 2px to drop halo pixels at map boundaries
    from scipy import ndimage
    ok = ndimage.binary_erosion(ok, iterations=2)
    return vals, ok

def sprite_reject_factory():
    # reject pixels deviating from current estimate (sprite/number pixels)
    def rej(frgb):
        ox, oy = cur_cam
        # compare frame to shifted estimate
        sx = -(cur_cam[0] - X0); sy = -(cur_cam[1] - Y0)
        shifted = np.zeros((724, 1280, 3), np.float32)
        x0, y0 = int(sx), int(sy)
        xs0, ys0 = max(0, -x0), max(0, -y0)
        xd0, yd0 = max(0, x0), max(0, y0)
        w = min(1280 - xs0, W - xd0); h = min(724 - ys0, H - yd0)
        if w > 0 and h > 0:
            shifted[ys0:ys0+h, xs0:xs0+w] = est[yd0:yd0+h, xd0:xd0+w]
        d = np.abs(frgb.astype(np.float32) - shifted).sum(axis=2)
        return d < 90
    return rej

cur_cam = (0, 0)
files_idx = range(0, len(files), 2)
rej = sprite_reject_factory()

# pass 1: plain mean
for i in files_idx:
    frgb = np.asarray(Image.open(f"{FRAMES}/{files[i]}").convert("RGB"), np.float32)
    cur_cam = (track[i]["camX"], track[i]["camY"])
    vals, ok = sky_view(frgb, cur_cam)
    cy_, cx_ = np.nonzero(ok)
    acc[cy_, cx_] += vals[ok]
    cnt[cy_, cx_] += 1
est = acc / np.maximum(cnt, 1)[..., None]

# pass 2: outlier-rejected mean
acc[:] = 0; cnt[:] = 0
for i in files_idx:
    frgb = np.asarray(Image.open(f"{FRAMES}/{files[i]}").convert("RGB"), np.float32)
    cur_cam = (track[i]["camX"], track[i]["camY"])
    vals, ok = sky_view(frgb, cur_cam, reject=rej)
    cy_, cx_ = np.nonzero(ok)
    acc[cy_, cx_] += vals[ok]
    cnt[cy_, cx_] += 1
est2 = acc / np.maximum(cnt, 1)[..., None]
seen = cnt > 0
est[seen] = est2[seen]

# fill holes: only interpolate between confident pixels (cnt>=3)
MINC = 1
conf = cnt >= MINC
print("confident:", conf.sum(), "/", conf.size)
for x in range(W):
    col = est[:, x]
    kn = np.nonzero(conf[:, x])[0]
    if len(kn) == 0:
        continue
    top, bot = kn[0], kn[-1]
    if top > 0:
        col[:top] = col[top]
    if bot < H - 1:
        col[bot+1:] = col[bot]
    for a, b in zip(kn[:-1], kn[1:]):
        if b - a > 1:
            t = np.linspace(0, 1, b - a + 1)[:, None]
            col[a:b+1] = col[a][None, :] * (1 - t) + col[b][None, :] * t
# 水平方向补洞:对每个仍未知的像素取左右最近已知像素插值
unknown = cnt < MINC
for y in range(H):
    row = est[y]
    kn = np.nonzero(~unknown[y])[0]
    if len(kn) < 2:
        continue
    for a, b in zip(kn[:-1], kn[1:]):
        if b - a > 1:
            t = np.linspace(0, 1, b - a + 1)[:, None]
            row[a:b+1] = row[a][None, :] * (1 - t) + row[b][None, :] * t
    if kn[0] > 0:
        row[:kn[0]] = row[kn[0]]
    if kn[-1] < W - 1:
        row[kn[-1]+1:] = row[kn[-1]]

Image.fromarray(np.clip(est, 0, 255).astype(np.uint8)).save(f"{R}/assets/map/sky.png")
print("saved assets/map/sky.png", est.shape)
