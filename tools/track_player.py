#!/usr/bin/env python3
"""Player-specific tracker: color/shape-scored cluster tracking seeded from a
hand-picked frame. Outputs cutouts + a montage for visual QA."""
import json, os
import numpy as np
from PIL import Image
from scipy import ndimage

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRAMES = "/tmp/mxd_all"
TRACK = json.load(open(f"{R}/data/camera_track.json"))
BG = np.asarray(Image.open(f"{R}/assets/map/106010104_bg.png").convert("RGB"), np.int16)
MAP_X, MAP_Y = -347, -353
W, H = 1280, 724

SEED_FRAME = 101          # player visible at screen box below
SEED_BOX = (652, 388, 46, 68)

def moving(frgb, cam):
    ox, oy = cam
    bx0, by0 = ox - MAP_X, oy - MAP_Y
    region = np.zeros((H, W, 3), np.int16)
    sx0, sy0 = max(0, -bx0), max(0, -by0)
    bx0, by0 = max(0, bx0), max(0, by0)
    w = min(W - sx0, BG.shape[1] - bx0); h = min(H - sy0, BG.shape[0] - by0)
    if w > 0 and h > 0:
        region[sy0:sy0+h, sx0:sx0+w] = BG[by0:by0+h, bx0:bx0+w]
    d = np.abs(frgb.astype(np.int16) - region).sum(axis=2)
    return d > 100

def components(mask):
    lab, n = ndimage.label(mask)
    out = []
    for i, sl in enumerate(ndimage.find_objects(lab), 1):
        h = sl[0].stop - sl[0].start; w = sl[1].stop - sl[1].start
        if h < 24 or w < 10:
            continue
        out.append((sl, (lab[sl] == i)))
    return out

def score_comp(sub_mask, frgb, sl, prev_c):
    h = sl[0].stop - sl[0].start; w = sl[1].stop - sl[1].start
    if not (30 <= h <= 90 and 12 <= w <= 70):
        return -1
    if h / max(w, 1) < 1.1:
        return -1
    rgb = frgb[sl]
    mx = rgb.max(axis=2).astype(np.int16); mn = rgb.min(axis=2).astype(np.int16)
    v = mx
    dark = ((v < 95) & (mx - mn < 60)).mean()          # dark navy/black cloth
    white = ((mn > 180)).mean()                        # white hat
    skin = ((rgb[:,:,0] > 170) & (rgb[:,:,1] > 120) & (rgb[:,:,2] > 90) & (rgb[:,:,2] < 190)).mean()
    s = dark * 2.0 + white * 2.5 + skin * 1.0
    if s < 0.35:
        return -1
    cx = (sl[1].start + sl[1].stop) / 2
    cy = sl[0].stop
    if prev_c is not None:
        d = abs(cx - prev_c[0]) + abs(cy - prev_c[1])
        if d > 150:
            return -1
        s -= d / 300.0
    return s

def main():
    os.makedirs("/tmp/mxd_player", exist_ok=True)
    boxes = {}
    prev_c = None
    order = list(range(SEED_FRAME, len(TRACK))) + list(range(SEED_FRAME - 1, -1, -1))
    for i in order:
        d = TRACK[i]
        frgb = np.asarray(Image.open(f"{FRAMES}/{d['file']}").convert("RGB"))
        mask = moving(frgb, (d["camX"], d["camY"]))
        # seed frame: force the known box
        if i == SEED_FRAME:
            x, y, w, h = SEED_BOX
            boxes[i] = (x, y, w, h)
            prev_c = (x + w / 2, y + h)
            continue
        best, bsl = -1, None
        for sl, sub in components(mask):
            sc = score_comp(sub, frgb, sl, prev_c)
            if sc > best:
                best, bsl = sc, sl
        if best > 0.35 and bsl is not None:
            x = bsl[1].start; y = bsl[0].start
            w = bsl[1].stop - x; h = bsl[0].stop - y
            boxes[i] = (x, y, w, h)
            prev_c = (x + w / 2, y + h)
        else:
            prev_c = None if prev_c is None else prev_c  # keep last, allow re-acquire
    json.dump({str(k): v for k, v in sorted(boxes.items())}, open(f"{R}/data/player_boxes.json", "w"))
    print("tracked frames:", len(boxes))
    # montage
    keys = sorted(boxes)
    per = 48
    for s in range(0, len(keys), per):
        chunk = keys[s:s+per]
        cols, rows = 8, 6
        cw, ch = 90, 110
        sheet = Image.new("RGB", (cols*cw, rows*ch), (225, 230, 225))
        for j, k in enumerate(chunk):
            x, y, w, h = boxes[k]
            im = Image.open(f"{FRAMES}/{TRACK[k]['file']}").crop((x, y, x+w, y+h))
            sheet.paste(im, ((j % cols)*cw + 5, (j//cols)*ch + 5))
        sheet.save(f"/tmp/mxd_player/mont_{s//per:02d}.png")
    print("montages:", (len(keys)+per-1)//per)

if __name__ == "__main__":
    main()
