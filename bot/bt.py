#!/usr/bin/env python3
"""迷你行为树(Behavior Tree)——bot 决策层的协调机制。

设计要点:
- 反应式(Reactive): 每 tick 从根重新评估, 环境一变立即切换, 无状态残留
- Selector: 按子节点顺序尝试, 第一个非 FAILURE 的结果即为本节点结果(优先级)
- Sequence: 依次执行, 任一 FAILURE 即失败(条件→动作的组合)
- Cond: 视觉谓词(只读黑板), Act: 动作叶子(只发按键/改黑板)
- Cooldown: 装饰器, 成功后 seconds 内直接 FAILURE(防抖动)

黑板(blackboard)即感知快照+记忆: 由 autoplay.tick 组装, 所有节点只读/写黑板,
不接触游戏内部状态——决策输入仅来自视觉分析, 输出仅是按键。
"""
import time

SUCCESS, FAILURE, RUNNING = "SUCCESS", "FAILURE", "RUNNING"


class Node:
    def tick(self, bb):
        raise NotImplementedError


class Selector(Node):
    def __init__(self, children):
        self.children = children

    def tick(self, bb):
        for c in self.children:
            st = c.tick(bb)
            if st != FAILURE:
                return st
        return FAILURE


class Sequence(Node):
    def __init__(self, children):
        self.children = children

    def tick(self, bb):
        for c in self.children:
            st = c.tick(bb)
            if st != SUCCESS:
                return st
        return SUCCESS


class Cond(Node):
    """条件谓词: fn(bb) 为真 → SUCCESS。"""

    def __init__(self, fn):
        self.fn = fn

    def tick(self, bb):
        return SUCCESS if self.fn(bb) else FAILURE


class Act(Node):
    """动作叶子: fn(bb) 返回 SUCCESS / FAILURE / RUNNING。"""

    def __init__(self, fn):
        self.fn = fn

    def tick(self, bb):
        return self.fn(bb)


class Cooldown(Node):
    """装饰器: 子树 SUCCESS 后 seconds 内直接 FAILURE(限制触发频率)。"""

    def __init__(self, seconds, child):
        self.seconds = seconds
        self.child = child
        self.until = 0.0

    def tick(self, bb):
        now = time.time()
        if now < self.until:
            return FAILURE
        st = self.child.tick(bb)
        if st == SUCCESS:
            self.until = now + self.seconds
        return st
