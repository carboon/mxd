#!/usr/bin/env python3
"""Local template tracker seeded from hand-locked frames. NCC via FFT."""
import json, os
import numpy as np
from PIL import Image
from scipy.signal import fftconvolve
from scipy import ndimage

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRAMES = "/tmp/mxd_all"
files = sorted(os.listdir(FRAMES))
CACHE = {}

def img(i):
    if i not in CACHE:
        CACHE[i] = np.asarray(Image.open(f"{FRAMES}/{files[i]}").convert("RGB"), np.float32)
    return CACHE[i]

def ncc_find(frame, tmpl, cx, cy, rad=60):
    """Find tmpl in frame near (cx,cy) top-left. Returns (x,y,score)."""
    th, tw = tmpl.shape[:2]
    t = tmpl.mean(axis=2)
    t0 = t - t.mean()
    tnorm = np.sqrt((t0 ** 2).sum())
    x0, y0 = max(0, cx - rad), max(0, cy - rad)
    x1, y1 = min(frame.shape[1] - tw, cx + rad), min(frame.shape[0] - th, cy + rad)
    if x1 <= x0 or y1 <= y0:
        return cx, cy, -1
    win = frame[y0:y1 + th, x0:x1 + tw].mean(axis=2)
    corr = fftconvolve(win, t0[::-1, ::-1], mode="valid")
    # local mean/std via box filter
    ones = np.ones_like(t0)
    m1 = fftconvolve(win, ones[::-1, ::-1], mode="valid")
    m2 = fftconvolve(win ** 2, ones[::-1, ::-1], mode="valid")
    n = t0.size
    var = m2 - m1 ** 2 / n
    var[var < 1e-3] = 1e-3
    ncc = (corr - m1 * t0.mean()) / (np.sqrt(var) * tnorm + 1e-3)
    j, i = np.unravel_index(np.argmax(ncc), ncc.shape)
    return x0 + i, y0 + j, float(ncc[j, i])

def track(seed_frame, seed_box, span, out):
    boxes = {seed_frame: tuple(int(v) for v in seed_box)}
    tmpl = img(seed_frame)[seed_box[1]:seed_box[1]+seed_box[3], seed_box[0]:seed_box[0]+seed_box[2]]
    for i in list(range(seed_frame+1, span[1])) + list(range(seed_frame-1, span[0]-1, -1)):
        prev = boxes.get(i-1) or boxes.get(i+1) or seed_box
        x, y, s = ncc_find(img(i), tmpl, prev[0], prev[1], rad=48)
        if s > 0.45:
            boxes[i] = (int(x), int(y), int(seed_box[2]), int(seed_box[3]))
    json.dump({str(k): list(v) for k, v in sorted(boxes.items())}, open(out, "w"))
    # montage
    keys = sorted(boxes)
    cols, cw, ch = 10, 70, 90
    rows = (len(keys)+cols-1)//cols
    from PIL import ImageDraw
    sheet = Image.new("RGB", (cols*(cw+4), rows*(ch+13)), (30,30,30))
    d = ImageDraw.Draw(sheet)
    for j, k in enumerate(keys):
        x, y, w, h = boxes[k]
        im = Image.open(f"{FRAMES}/{files[k]}").crop((x, y, x+w, y+h))
        sheet.paste(im, ((j%cols)*(cw+4)+2, (j//cols)*(ch+13)+11))
        d.text(((j%cols)*(cw+4)+2, (j//cols)*(ch+13)), f"#{k}", fill=(255,255,0))
    sheet.save(out.replace(".json", ".png"))
    print(out, len(boxes), "frames")

# walk_right seed (#142) — track the ground walking span
track(142, (828, 385, 77, 73), (60, 210), f"{R}/data/trackA.json")
# attack seed (#224) — tier fight span
track(224, (660, 270, 70, 80), (196, 280), f"{R}/data/trackB.json")
