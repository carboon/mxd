#!/usr/bin/env python3
"""Detect the player per frame via tight color signature on bg-diff components.
Navy body + white hat on top; reject all-white pets / brown monkey / blue mushroom / big golem."""
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

def moving(frgb, cam):
    ox, oy = cam
    bx0, by0 = int(ox - MAP_X), int(oy - MAP_Y)
    region = np.zeros((H, W, 3), np.int16)
    sx0, sy0 = max(0, -bx0), max(0, -by0)
    bx0c, by0c = max(0, bx0), max(0, by0)
    w = min(W - sx0, BG.shape[1] - bx0c); h = min(H - sy0, BG.shape[0] - by0c)
    if w > 0 and h > 0:
        region[sy0:sy0+h, sx0:sx0+w] = BG[by0c:by0c+h, bx0c:bx0c+w]
    return np.abs(frgb.astype(np.int16) - region).sum(axis=2) > 100

def find_player(frgb, cam, prev):
    mask = moving(frgb, cam)
    lab, n = ndimage.label(mask)
    best, bs = None, -1
    for i, sl in enumerate(ndimage.find_objects(lab), 1):
        h = sl[0].stop - sl[0].start; w = sl[1].stop - sl[1].start
        if not (34 <= h <= 84 and 14 <= w <= 64):
            continue
        comp = (lab[sl] == i)
        rgb = frgb[sl]
        r, g, b = rgb[:,:,0], rgb[:,:,1], rgb[:,:,2]
        white = (r > 185) & (g > 185) & (b > 185)
        navy = (r < 90) & (g < 95) & (b > r) & (b > 40) & (b < 160)
        red = (r > 150) & (g < 90) & (b < 90)
        wh_t = white[:h//3].sum()
        navy_b = navy[h//3:].sum()
        total = comp.sum()
        wh_all = white.mean()
        if wh_all > 0.45:      # yeti pet: all white
            continue
        if wh_t < 30 or navy_b < 120:
            continue
        s = min(wh_t / 60, 1.2) + min(navy_b / 350, 1.2) + min(red.sum() / 60, 0.6)
        cx = sl[1].start + w / 2; cy = sl[0].stop
        if prev is not None:
            d = abs(cx - prev[0]) + abs(cy - prev[1])
            if d > 200:
                continue
            s -= d / 500.0
        if s > bs:
            bs, best = s, (sl[1].start, sl[0].start, w, h)
    return best, bs

def main():
    boxes = {}
    prev = None
    # seed from frame 224 (known): player at ~(676,283)-(716,336)
    for i in range(len(TRACK)):
        d = TRACK[i]
        frgb = np.asarray(Image.open(f"{FRAMES}/{d['file']}").convert("RGB"))
        if i == 224:
            boxes[i] = (676, 283, 42, 54)
            prev = (676 + 21, 283 + 54)
            continue
        b, s = find_player(frgb, (d["camX"], d["camY"]), prev)
        if b:
            boxes[i] = b
            prev = (b[0] + b[2] / 2, b[1] + b[3])
    json.dump({str(k): list(map(int, v)) for k, v in sorted(boxes.items())},
              open(f"{R}/data/player_boxes.json", "w"))
    print("frames with player:", len(boxes), "/", len(TRACK))
    keys = sorted(boxes)
    cols, cw, ch = 10, 76, 96
    for s0 in range(0, len(keys), 60):
        chunk = keys[s0:s0+60]
        rows = (len(chunk)+cols-1)//cols
        sheet = Image.new("RGB", (cols*(cw+4), rows*(ch+14)), (30,30,30))
        from PIL import ImageDraw
        dr = ImageDraw.Draw(sheet)
        for j, k in enumerate(chunk):
            x, y, w, h = boxes[k]
            im = Image.open(f"{FRAMES}/{TRACK[k]['file']}").crop((x, y, x+w, y+h))
            sheet.paste(im, ((j % cols)*(cw+4)+2, (j//cols)*(ch+14)+12))
            dr.text(((j % cols)*(cw+4)+2, (j//cols)*(ch+14)+1), f"#{k}", fill=(255,255,0))
        sheet.save(f"/tmp/pfind_{s0//60:02d}.png")
    print("sheets:", (len(keys)+59)//60)

if __name__ == "__main__":
    main()
