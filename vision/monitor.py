#!/usr/bin/env python3
"""系统2：实时视觉监控。

并行程序：从游戏帧共享缓冲读取画面(亦兼容 mss 抓屏)，识别角色框、怪物位置，
实时判断能否攻击(近身将挥拳)、轻功buff是否可补放，受击时给出操作建议。
结果写入 /tmp/mxd_vision.json (原子替换)，供系统3 bot 与人工观察使用。

用法: python3 vision/monitor.py [--preview] [--once]
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

OUT = "/tmp/mxd_vision.json"


class ShmSource:
    """游戏帧共享缓冲(首选)。"""

    def __init__(self):
        from game.ipc import FrameReader
        self.reader = FrameReader()

    def read(self):
        seq, frame = self.reader.read()
        return frame


class ScreenSource:
    """真实屏幕抓取(mss)，抓游戏窗口区域。备用路径。"""

    def __init__(self, box):
        import mss  # noqa
        self.box = box

    def read(self):
        try:
            import mss
            with mss.mss() as sct:
                shot = sct.grab(self.box)
                arr = np.asarray(shot)[:, :, :3]
                return arr
        except Exception:
            return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true", help="写标注帧 /tmp/mxd_vision_preview.png")
    ap.add_argument("--screen", action="store_true", help="用mss抓屏代替共享帧")
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    src = None
    if not args.screen:
        try:
            src = ShmSource()
        except Exception as e:
            print("共享帧不可用:", e)
    if src is None:
        src = ScreenSource({"left": 0, "top": 0, "width": 1280, "height": 724})
        print("使用屏幕捕获模式")

    from vision.detector import Detector
    det = Detector()

    last_hp = None
    hit_time = 0.0
    last_write = 0.0
    frames = 0
    haste_hist = []        # [(t, present)] 轻功图标出现史(3秒窗口, 用于闪烁判定)
    t0 = time.time()
    print("视觉监控启动 →", OUT)
    while True:
        frame = src.read()
        if frame is None:
            time.sleep(0.005)
            if args.once and frames > 0:
                break
            if time.time() - t0 > 20 and frames == 0:
                print("20秒内未收到帧，退出")
                return 1
            continue
        frames += 1
        now = time.time()
        player = det.find_player(frame)
        mobs = det.find_mobs(frame)
        mp_ratio, hp_ratio = det.hud_bars(frame)
        px, py = (player["x"], player["y"]) if player else (640, 400)
        fx = det.effects_near(frame, px, py)
        # 小地图箭头 → 世界坐标 + 相机估计(供bot在世界坐标系导航)
        mw = det.player_world_from_minimap(frame)
        cam = None
        mob_world = []
        if mw and player:
            cam = {"x": mw["x"] - player["x"], "y": mw["y"] - player["y"]}
            mob_world = []
            for m in mobs:
                m2 = dict(m)
                m2["wx"] = m["x"] + cam["x"]
                m2["wy"] = m["y"] + cam["y"]
                m2["y"] = m2["wy"]          # 统一: bot读到的y即世界y
                mob_world.append(m2)

        # 受击判定: HP条下跌 或 玩家附近伤害数字
        hit_now = False
        if last_hp is not None and hp_ratio < last_hp - 0.02:
            hit_now = True
        if fx["damage_number"] and player:
            hit_now = True
        if hit_now:
            hit_time = now
        last_hp = hp_ratio

        # 轻功buff图标: 出现/闪烁/消失 → 可否补放
        h_present, h_score = det.haste_icon(frame)
        haste_hist.append((now, h_present))
        haste_hist[:] = [h for h in haste_hist if now - h[0] < 3.0]
        blinking = bool(h_present) and any(not p for _, p in haste_hist)
        can_recast = (not h_present) or blinking

        # 攻击可行性判断
        same_platform = [m for m in mobs if player and abs(m["y"] - player["y"]) < 70]
        in_skill_range = [m for m in same_platform if abs(m["x"] - px) <= 200]
        in_punch_range = [m for m in same_platform if abs(m["x"] - px) <= 62]
        mp_ok = mp_ratio > 0.02            # 双飞斩耗20/1615≈1.2%
        can_skill = bool(in_skill_range) and mp_ok
        will_punch = bool(in_punch_range)   # 近身即挥拳(与MP无关, 同游戏规则)
        invincible = now - hit_time < 2.2

        # 操作建议
        advice = []
        if hit_now:
            advice.append("受击！进入约2.2秒无敌闪烁，期间可安全输出或后撤")
        if not player:
            advice.append("未找到角色，检查画面")
        if mobs and not same_platform and player:
            dy = min(m["y"] - py for m in mobs)
            advice.append("目标在%s层平台，建议%s" % ("上" if dy < 0 else "下",
                                                    "爬藤蔓/跳上台阶" if dy < 0 else "下跳/走下台阶"))
        if in_skill_range and mp_ok:
            advice.append("可放双飞斩：目标距离%dpx" % min(abs(m["x"] - px) for m in in_skill_range))
        elif in_punch_range:
            advice.append("目标近身，将挥拳(微量伤害且吸引仇恨)；建议跳离/拉距后用双飞斩")
        elif same_platform and not in_skill_range:
            advice.append("目标在同层但超出射程，先接近")
        if can_recast:
            advice.append("轻功未激活(未显示/闪烁中)，可按A补放")
        if hp_ratio < 0.15:
            advice.append("HP危险(<15%%)，建议撤到无怪处回血")

        vis = {
            "t": now,
            "player": player,
            "mobs": mobs,
            "mp_ratio": round(mp_ratio, 3),
            "hp_ratio": round(hp_ratio, 3),
            "can_skill": can_skill,
            "will_punch": will_punch,
            "punch_range": bool(in_punch_range),
            "haste": {"present": h_present, "score": h_score,
                      "blinking": blinking, "can_recast": can_recast},
            "hit": hit_now,
            "invincible": invincible,
            "effects": fx,
            "player_world": mw,
            "cam": cam,
            "mob_world": mob_world,
            "advice": advice,
            "fps": round(frames / (now - t0 + 1e-6), 1),
        }
        tmp = OUT + ".tmp"
        with open(tmp, "w") as f:
            json.dump(vis, f, ensure_ascii=False)
        os.replace(tmp, OUT)

        if args.preview and frames % 15 == 0:
            from PIL import Image, ImageDraw
            im = Image.fromarray(frame)
            d = ImageDraw.Draw(im)
            if player:
                d.rectangle([player["x"] - 17, player["y"] - 60, player["x"] + 17, player["y"]],
                            outline=(0, 255, 0), width=2)
            for m in mobs:
                d.rectangle([m["x"] - m["w"] // 2, m["y"] - m["h"], m["x"] + m["w"] // 2, m["y"]],
                            outline=(255, 60, 60), width=2)
            d.text((10, 100), f"MP {mp_ratio:.0%} HP {hp_ratio:.0%} can_skill={can_skill} "
                              f"punch={will_punch} hit={hit_now}", fill=(255, 255, 0))
            im.save("/tmp/mxd_vision_preview.png")

        if frames % 60 == 0:
            print(f"[{now - t0:6.1f}s] fps={vis['fps']} 角色={'有' if player else '无'} "
                  f"怪x{len(mobs)} 可技能={can_skill} 挥拳={will_punch} "
                  f"轻功={'有' if h_present else '无'}{'(闪烁)' if blinking else ''} "
                  f"受击={hit_now} 建议:{advice[:1]}")
        if args.once:
            print(json.dumps(vis, ensure_ascii=False)[:400])
            break
        time.sleep(0.004)


if __name__ == "__main__":
    sys.exit(main() or 0)
