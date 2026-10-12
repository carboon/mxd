#!/usr/bin/env python3
"""Session stats: EXP accumulation, kill log CSV, F9 reset."""
import csv
import json
import os
import time

from . import config as C


class Session:
    def __init__(self):
        os.makedirs(os.path.dirname(C.KILL_LOG), exist_ok=True)
        self.exp = C.START_EXP
        self.total_kills = 0
        self.session_kills = 0
        self.load_state()

    def load_state(self):
        if os.path.exists(C.EXP_STATE):
            try:
                with open(C.EXP_STATE) as f:
                    d = json.load(f)
                self.exp = d.get("exp", C.START_EXP)
                self.total_kills = d.get("total_kills", 0)
                self.session_kills = d.get("session_kills", 0)
            except Exception:
                pass

    def save_state(self):
        with open(C.EXP_STATE, "w") as f:
            json.dump({"exp": self.exp, "total_kills": self.total_kills,
                       "session_kills": self.session_kills,
                       "updated": time.strftime("%Y-%m-%d %H:%M:%S")}, f,
                      ensure_ascii=False, indent=1)

    def on_kill(self, mob, now):
        self.exp += mob.exp
        self.total_kills += 1
        self.session_kills += 1
        new = not os.path.exists(C.KILL_LOG)
        with open(C.KILL_LOG, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["time", "mob_id", "mob_name", "exp"])
            w.writerow([time.strftime("%H:%M:%S"), mob.mob_id, mob.name, mob.exp])
        self.save_state()

    def reset(self):
        """F9 清零：经验归零、计数清零、日志清空。"""
        self.exp = 0
        self.session_kills = 0
        self.total_kills = 0
        with open(C.KILL_LOG, "w", newline="") as f:
            csv.writer(f).writerow(["time", "mob_id", "mob_name", "exp"])
        self.save_state()
