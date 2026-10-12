#!/usr/bin/env python3
"""进程间通道：帧共享(给视觉系统当“屏幕”) + 虚拟键盘(给bot注入操作)。

布局:
  mxd_frame  shm: [u64 seq][u16 w][u16 h][RGB bytes...]
  mxd_input  shm: [u8 seq][u8 keys L R U D][u8 edges attack jump][u8 punch][pad]
所有对象都容错：不存在/不可用时游戏照常运行。
"""
import struct
import time

import numpy as np

FRAME_SHM = "mxd_frame"
INPUT_SHM = "mxd_input"

KEY_L, KEY_R, KEY_U, KEY_D = 1, 2, 4, 8
EDGE_ATTACK, EDGE_JUMP, EDGE_SKILL = 1, 2, 4

KEYMAP = {None: 0}


class FramePublisher:
    def __init__(self, w, h):
        from multiprocessing import shared_memory
        self.w, self.h = w, h
        self.size = 16 + w * h * 3
        try:
            self.shm = shared_memory.SharedMemory(name=FRAME_SHM, create=True, size=self.size)
        except FileExistsError:
            self.shm = shared_memory.SharedMemory(name=FRAME_SHM)
            self.shm.close()
            self.unlink_stale()
            self.shm = shared_memory.SharedMemory(name=FRAME_SHM, create=True, size=self.size)
        self.buf = memoryview(self.shm.buf)
        struct.pack_into("<HH", self.buf, 8, w, h)
        self.seq = 0
        self.last_pub = 0.0

    def unlink_stale(self):
        try:
            import shared_memory_cleanup  # noqa
        except Exception:
            pass

    def publish(self, surface):
        now = time.time()
        if now - self.last_pub < 1 / 30:      # 最多30fps发布
            return
        self.last_pub = now
        import pygame
        arr = pygame.surfarray.array3d(surface)     # (w,h,3)
        arr = arr.transpose(1, 0, 2)                # → (h,w,3)
        self.buf[16:16 + self.w * self.h * 3] = arr.tobytes()
        self.seq += 1
        struct.pack_into("<Q", self.buf, 0, self.seq)

    def close(self):
        try:
            self.buf.release()
            self.shm.close()
            self.shm.unlink()
        except Exception:
            pass


class FrameReader:
    """视觉端：读共享帧。attach失败则由调用方走屏幕捕获。"""

    def __init__(self):
        from multiprocessing import shared_memory
        self.shm = shared_memory.SharedMemory(name=FRAME_SHM)
        self.buf = memoryview(self.shm.buf)
        self.last_seq = 0
        w, h = struct.unpack_from("<HH", self.buf, 8)
        self.w, self.h = w, h

    def read(self):
        """返回 (seq, HxWx3 uint8) 或 (None, None)。"""
        seq = struct.unpack_from("<Q", self.buf, 0)[0]
        if seq == self.last_seq or seq == 0:
            return None, None
        self.last_seq = seq
        w, h = struct.unpack_from("<HH", self.buf, 8)
        arr = np.frombuffer(self.buf, dtype=np.uint8, count=w * h * 3, offset=16)
        return seq, arr.reshape(h, w, 3).copy()

    def close(self):
        try:
            self.buf.release()
            self.shm.close()
        except Exception:
            pass


class RemoteInputServer:
    """游戏端：读取bot写入的虚拟键盘。"""

    def __init__(self):
        from multiprocessing import shared_memory
        try:
            self.shm = shared_memory.SharedMemory(name=INPUT_SHM, create=True, size=16)
        except FileExistsError:
            self.shm = shared_memory.SharedMemory(name=INPUT_SHM)
        self.buf = memoryview(self.shm.buf)
        self.last_seq = 0

    def read(self):
        """返回 (keys_dict, events_set)。按seq消费一次(客户端seq最后写, 无需清除)。"""
        seq = self.buf[0]
        if seq == self.last_seq:
            return None, set()
        self.last_seq = seq
        kb = self.buf[1]
        edges = self.buf[2]
        keys = {
            "left": bool(kb & KEY_L), "right": bool(kb & KEY_R),
            "up": bool(kb & KEY_U), "down": bool(kb & KEY_D),
        }
        events = set()
        if edges & EDGE_ATTACK:
            events.add("attack")
        if edges & EDGE_JUMP:
            events.add("jump")
        if edges & EDGE_SKILL:
            events.add("skill")
        return keys, events

    def close(self):
        try:
            self.buf.release()
            self.shm.close()
            self.shm.unlink()
        except Exception:
            pass


class RemoteInputClient:
    """bot端：写虚拟键盘。"""

    def __init__(self):
        from multiprocessing import shared_memory
        try:
            self.shm = shared_memory.SharedMemory(name=INPUT_SHM, create=True, size=16)
        except FileExistsError:
            self.shm = shared_memory.SharedMemory(name=INPUT_SHM)
        self.buf = memoryview(self.shm.buf)
        self.seq = 0

    def send(self, left=False, right=False, up=False, down=False, attack=False, jump=False,
             skill=False):
        """写入顺序: kb → edges → seq(提交标记最后写), 保证游戏读到完整帧。"""
        kb = (KEY_L if left else 0) | (KEY_R if right else 0) | \
             (KEY_U if up else 0) | (KEY_D if down else 0)
        edges = (EDGE_ATTACK if attack else 0) | (EDGE_JUMP if jump else 0) | \
                (EDGE_SKILL if skill else 0)
        self.buf[1] = kb
        self.buf[2] = edges
        self.seq = (self.seq + 1) % 255 or 1
        self.buf[0] = self.seq

    def close(self):
        try:
            self.buf.release()
            self.shm.close()
        except Exception:
            pass


def cleanup_stale():
    """测试间清理残留shm段。"""
    from multiprocessing import shared_memory
    for name in (FRAME_SHM, INPUT_SHM):
        try:
            s = shared_memory.SharedMemory(name=name)
            s.close()
            s.unlink()
        except Exception:
            pass
