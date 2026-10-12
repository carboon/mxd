#!/usr/bin/env python3
"""清理玩家精灵图残留的录屏背景(绿色草丛/灰绿虚化/白色雾团)。

判据(角色无绿色系, 脸为暖白可区分冷白雾):
  bg1 强绿: g > r+12 且 g > b+12
  bg2 灰绿虚化: g > b+12 且 g > r+4 且亮度总和<500
  bg3 冷白雾团: 三通道>210 且 |r-g|<=12 且 g-b<=25 (暖白脸 r-b>25 不受影响)
保留大连通域(>=60px), 边缘单层羽化。原图备份到 assets/player_orig/。
用法: python3 tools/clean_player_sprites.py [--apply]  (默认只预览不写盘)
"""
import os
import shutil
import sys

import numpy as np
from PIL import Image
from scipy import ndimage

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(R, "assets/player")
BAK = os.path.join(R, "assets/player_orig")

# sprites.py 实际用到的帧 + 名牌底图不涉及
USED = ["stand", "walk1", "walk2", "jump", "climb1", "climb2", "prone", "hit",
        "atkL_217", "atkL_218", "atkL_219", "atkL_220", "atkL_221", "atkL_223"]


def bg_mask(im):
    r = im[:, :, 0].astype(np.int16)
    g = im[:, :, 1].astype(np.int16)
    b = im[:, :, 2].astype(np.int16)
    a = im[:, :, 3]
    bg1 = (g > r + 12) & (g > b + 12)
    bg2 = (g > b + 12) & (g > r + 4) & (r + g + b < 500)
    bg3 = (r > 210) & (g > 210) & (b > 200) & (np.abs(r - g) <= 12) & (g - b <= 25)
    return (bg1 | bg2 | bg3) & (a > 0)


def clean(path):
    im = np.asarray(Image.open(path).convert("RGBA")).copy()
    bg = bg_mask(im)
    keep = ~bg & (im[:, :, 3] > 0)
    lab, n = ndimage.label(keep)
    if n == 0:
        return None
    sizes = ndimage.sum(keep, lab, range(1, n + 1))
    objs = ndimage.find_objects(lab)
    h, w = keep.shape
    eroded = ndimage.binary_erosion(keep)          # 1px腐蚀后消失 = 细线/草茎
    largest = int(np.argmax(sizes)) + 1
    for i in range(1, n + 1):
        sl = objs[i - 1]
        size = sizes[i - 1]
        comp = lab[sl] == i
        if i == largest:
            continue
        # 橙色飞镖白名单(暖橙: r明显高, b低)
        px = im[sl][comp][:, :3].mean(axis=0)
        if px[0] > 170 and px[1] > 110 and px[2] < 120 and size >= 60:
            continue
        touches_edge = sl[0].start == 0 or sl[1].start == 0 \
            or sl[0].stop >= h or sl[1].stop >= w
        thin = not eroded[sl][comp].any()
        if size < 60 or thin or (touches_edge and size < 300):
            keep[lab == i] = False
    # 边缘羽化: alpha = 255*keep, 3x3均值一次
    alpha = (keep * 255).astype(np.float32)
    alpha = ndimage.uniform_filter(alpha, size=3)
    out = im.copy()
    out[:, :, 3] = np.where(im[:, :, 3] > 0, np.clip(alpha, 0, 255), 0).astype(np.uint8)
    return out


def contact(files, outs, path):
    cols = 7
    rows = (len(files) + cols - 1) // cols
    cell = 160
    sheet = np.full((rows * cell, cols * cell, 3), 235, np.uint8)
    for i, (f, o) in enumerate(zip(files, outs)):
        rr, cc = divmod(i, cols)
        if o is None:
            continue
        im = o
        h, w = im.shape[:2]
        y0, x0 = rr * cell + (cell - h) // 2, cc * cell + (cell - w) // 2
        a = im[:, :, 3:4].astype(np.float32) / 255
        sheet[y0:y0 + h, x0:x0 + w] = (im[:, :, :3] * a +
                                       sheet[y0:y0 + h, x0:x0 + w] * (1 - a)).astype(np.uint8)
    Image.fromarray(sheet).save(path)


def main():
    apply = "--apply" in sys.argv
    if apply and not os.path.isdir(BAK):
        shutil.copytree(SRC, BAK)
        print("原图已备份 →", os.path.relpath(BAK, R))
    files = [f"{n}.png" for n in USED if os.path.exists(os.path.join(SRC, f"{n}.png"))]
    outs = []
    for f in files:
        p = os.path.join(SRC, f)
        out = clean(p)
        outs.append(out)
        if out is None:
            print("跳过(空):", f)
            continue
        if apply:
            Image.fromarray(out).save(p)
        orig = np.asarray(Image.open(os.path.join(BAK, f) if apply else p).convert("RGBA"))
        og = bg_mask(orig).sum()
        ng = bg_mask(out).sum()
        print(f"{f}: 残留 {og}px → {ng}px")
    tag = "after" if apply else "preview"
    contact(files, outs, f"/tmp/player_clean_{tag}.png")
    print(f"拼图 → /tmp/player_clean_{tag}.png")


if __name__ == "__main__":
    main()
