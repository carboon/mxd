#!/usr/bin/env python3
"""Track camera offsets of every video frame against the map render, then
extract moving-sprite cutouts (player/pets) and a stitched sky canvas.

Outputs:
  data/camera_track.json   [{frame, camX, camY, px, py}]  (world = screen + cam)
  out/cut/frame_XXXX.png   RGBA cutouts of the tracked sprite cluster
  out/track_debug/f_XXXX.jpg  frame with cam+player box drawn (sampled)
  out/sky.png              sky canvas in world coordinates
"""
import json, os, sys
import numpy as np
from PIL import Image

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRAMES_DIR = "/tmp/mxd_all"
BG_PATH = f"{R}/assets/map/106010104_bg.png"
OUT_TRACK = f"{R}/data/camera_track.json"
OUT_CUT = "/tmp/mxd_cut"
OUT_DBG = "/tmp/mxd_dbg"

MAP_X, MAP_Y = -347, -353          # world coords of bg image origin
W, H = 1280, 724

def load_frames():
    files = sorted(os.listdir(FRAMES_DIR))
    return files

def build_samples(bg_rgb, bg_a):
    """Sample points on the bg that are opaque and in low-texture areas."""
    h, w = bg_a.shape
    pts = []
    step = 9
    for y in range(0, h - step, step):
        for x in range(0, w - step, step):
            if bg_a[y, x] < 250:
                continue
            patch = bg_rgb[y:y+step, x:x+step].astype(np.int16)
            if patch.std(axis=(0, 1)).mean() < 6.0:   # flat area -> robust
                pts.append((x + step // 2, y + step // 2))
    return np.array(pts, dtype=np.int32)  # (N,2)

def frame_diff_score(frgb, bgrgb, samples, ox, oy):
    """SAD of sampled bg pixels vs frame at camera offset (ox,oy = world of screen 0,0).
    samples are bg-image coords; screen = bg + (MAP_X,MAP_Y) - (ox,oy)."""
    bx, by = samples[:, 0], samples[:, 1]
    sx = bx + MAP_X - ox
    sy = by + MAP_Y - oy
    ok = (sx >= 0) & (sx < W) & (sy >= 0) & (sy < H)
    if ok.sum() < 200:
        return 1e18
    bp = bgrgb[by[ok], bx[ok]].astype(np.int32)
    fp = frgb[sy[ok], sx[ok]].astype(np.int32)
    return np.abs(bp - fp).mean()

def match_camera(frgb, bgrgb, samples, prev):
    best, bo = 1e18, prev
    if prev is None:
        cands = [(x, y) for x in range(MAP_X, MAP_X + bgrgb.shape[1] - W + 1, 6)
                        for y in range(MAP_Y, MAP_Y + bgrgb.shape[0] - H + 1, 6)]
        for c in cands:
            s = frame_diff_score(frgb, bgrgb, samples, *c)
            if s < best:
                best, bo = s, c
        prev = bo
    # refine around prev
    for c in [(prev[0] + dx, prev[1] + dy) for dx in range(-8, 9) for dy in range(-8, 9)]:
        s = frame_diff_score(frgb, bgrgb, samples, *c)
        if s < best:
            best, bo = s, c
    return bo, best

def moving_mask(frgb, bgrgb, cam):
    """pixels where frame differs from bg (world-aligned)."""
    ox, oy = cam
    bx0, by0 = ox - MAP_X, oy - MAP_Y
    region = np.zeros((H, W, 3), dtype=np.uint8)
    sx0, sy0 = max(0, -bx0), max(0, -by0)
    bx0, by0 = max(0, bx0), max(0, by0)
    w = min(W - sx0, bgrgb.shape[1] - bx0)
    h = min(H - sy0, bgrgb.shape[0] - by0)
    if w > 0 and h > 0:
        region[sy0:sy0 + h, sx0:sx0 + w] = bgrgb[by0:by0 + h, bx0:bx0 + w]
    d = np.abs(frgb.astype(np.int16) - region.astype(np.int16)).sum(axis=2)
    return (d > 90), True

def main():
    os.makedirs(OUT_CUT, exist_ok=True)
    os.makedirs(OUT_DBG, exist_ok=True)
    bg = Image.open(BG_PATH).convert("RGBA")
    bgrgb = np.asarray(bg, dtype=np.uint8)
    bg_a = bgrgb[:, :, 3]
    bg_rgb = bgrgb[:, :, :3]
    samples = build_samples(bg_rgb, bg_a)
    print(f"samples: {len(samples)}")

    files = load_frames()
    track = []
    prev_cam = None
    prev_box = None
    debug_every = 40
    for i, fn in enumerate(files):
        frgb = np.asarray(Image.open(f"{FRAMES_DIR}/{fn}").convert("RGB"), dtype=np.uint8)
        cam, score = match_camera(frgb, bg_rgb, samples, prev_cam)
        prev_cam = cam
        # moving mask & player cluster
        mask, valid = moving_mask(frgb, bg_rgb, cam)
        if not valid:
            mask = np.zeros((H, W), bool)
        # player prior: near previous box
        px, py, pw, ph = None, None, 0, 0
        from scipy import ndimage  # may not exist; fallback below
        lab, n = ndimage.label(mask)
        if n:
            best = None
            for sl in ndimage.find_objects(lab):
                hgt = sl[0].stop - sl[0].start
                wid = sl[1].stop - sl[1].start
                if hgt < 18 or hgt > 140 or wid > 160:
                    continue
                cx = (sl[1].start + sl[1].stop) / 2
                cy = sl[0].stop   # feet
                if prev_box is not None:
                    d = abs(cx - (prev_box[0] + prev_box[2] / 2)) + abs(cy - (prev_box[1] + prev_box[3]))
                else:
                    d = 0
                if best is None or d < best[0]:
                    best = (d, sl, wid, hgt)
            if best:
                sl = best[1]
                px, py, pw, ph = sl[1].start, sl[0].start, best[2], best[3]
                # cutout with 2px margin, alpha from mask dilated
                m = mask[sl][None][0]
                sub = mask[max(0, py - 2):py + ph + 2, max(0, px - 2):px + pw + 2]
                rgba = frgb[max(0, py - 2):py + ph + 2, max(0, px - 2):px + pw + 2].copy()
                msub = sub.copy()
                alpha = (msub * 255).astype(np.uint8)
                out = np.dstack([rgba, alpha])
                Image.fromarray(out, "RGBA").save(f"{OUT_CUT}/frame_{i:04d}.png")
                prev_box = (px, py, pw, ph)
        track.append({"frame": i, "file": fn, "camX": cam[0], "camY": cam[1],
                      "box": [int(px) if px is not None else None,
                              int(py) if py is not None else None,
                              int(pw) if pw is not None else 0,
                              int(ph) if ph is not None else 0]})
        if i % debug_every == 0:
            dbg = frgb.copy()
            if px is not None:
                dbg[py:py + ph, px:px + pw] = (dbg[py:py + ph, px:px + pw] * 0.5 + np.array([255, 0, 0]) * 0.5).astype(np.uint8)
            Image.fromarray(dbg).save(f"{OUT_DBG}/f_{i:04d}.jpg", quality=88)
        if i % 100 == 0:
            print(i, cam, round(score, 1), flush=True)
    json.dump(track, open(OUT_TRACK, "w"), indent=0)
    print("saved", OUT_TRACK)

if __name__ == "__main__":
    main()
