#!/usr/bin/env python3
"""标定小地图: 已知真值位置 → 检测橙色箭头像素坐标, 输出对照表。"""
import json

import numpy as np
from scipy import ndimage

frames = np.load("/tmp/cal_frames.npy")
truth = json.load(open("/tmp/cal_truth.json"))

for d, fr in zip(truth, frames):
    tx, ty = d["x"], d["y"]
    reg = fr[6:94, 4:121].astype(np.int16)
    r, g, b = reg[:, :, 0], reg[:, :, 1], reg[:, :, 2]
    m = (r >= 246) & (np.abs(g - 170) <= 14) & (b <= 70)
    lab, n = ndimage.label(m)
    hits = []
    objs = ndimage.find_objects(lab)
    for i in range(1, n + 1):
        sl = objs[i - 1]
        comp = lab[sl] == i
        px = int(comp.sum())
        if px < 5 or px > 60:
            continue
        bh, bw = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
        if bw > 10 or bh > 8:
            continue
        if not ndimage.binary_erosion(comp).any():
            continue
        pix = reg[sl][comp].astype(np.float32)
        sat = float((pix[:, 0] - pix[:, 2]).mean())
        if sat < 120:
            continue
        ys, xs = np.nonzero(comp)
        cx = float(xs.mean()) + 4 + sl[1].start
        cy = float(ys.mean()) + 6 + sl[0].start
        hits.append((int(px), round(sat, 1), round(cx, 1), round(cy, 1)))
    hits.sort(reverse=True)
    print("真值(%6.1f,%6.1f):" % (float(tx), float(ty)), hits[:4])
