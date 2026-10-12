#!/usr/bin/env python3
"""Global configuration: physics, layout, colors, asset paths."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --- window / world -------------------------------------------------------
VIEW_W, VIEW_H = 1280, 724
FPS = 60
MAP_BG = os.path.join(ROOT, "assets/map/106010104_bg.png")
SKY_BG = os.path.join(ROOT, "assets/map/sky.png")
BG_ORIGIN = (-347, -353)                      # world coords of bg image top-left
CAM_BOUNDS = {"left": -303, "top": -521, "right": -126, "bottom": -68}

# --- physics (classic MapleStory-calibrated) -------------------------------
GRAVITY = 2000.0
JUMP_V = -555.0
MAX_FALL = 670.0
WALK_SPEED = 140.0
AIR_ACCEL = 300.0            # 空中方向操控加速度: 动量保留, 无法全速反向回到起跳点
CLIMB_SPEED = 100.0
DOWNJUMP_V = -170.0
KNOCKBACK_V = 160.0
KNOCKBACK_TIME = 0.28
INVINCIBLE_TIME = 2.2

# --- combat ----------------------------------------------------------------
STAR_DAMAGE = 1000          # 每发飞镖固定伤害（简化规则）
STAR_COUNT = 2              # 双飞斩一次2发(经典客户端: 1目标2段伤害)
STAR_SPEED = 760.0
STAR_INNER_DELAY = 0.09     # 两发间隔
# --- 经典客户端 Lucky Seven 目标搜索/弹道规格 ---
# 来源: NiaMeowDB MapleStory Classic 隐藏机制(WZ客户端逆向整理):
#   发射时步进锁定单一目标(65px起,20px波段,边界按1:4斜率扩宽≈14°),
#   弹道为直线从出手点飞向目标(打上下平台时倾斜);
#   垂直可达脚上124px/脚下68px —— 相邻下层只露头可打, 相邻上层远端可打,
#   跨两层永远够不到(与地图观察一致)
L7_SEARCH_START = 65        # 目标搜索起点: 身前65px
L7_RANGE = 400              # 水平索敌距离(65+400=465px处为止)
L7_BAND_HALF = 28           # 搜索波段初始半高(px)
L7_SLOPE_DEN = 4            # 波段边界扩宽斜率 1:4
L7_REACH_UP = 124           # 脚上方最大垂直可达(px)
L7_REACH_DOWN = 68          # 脚下方最大垂直可达(px)
ATTACK_COOLDOWN = 0.38      # 攻击间隔(原版飞镖可连发, 仅受攻速限制)
L7_MP_COST = 0              # MP无限模式(需求变更): 双飞斩不耗魔
PLAYER_MAX_HP, PLAYER_HP = 1822, 1284
PLAYER_MAX_MP, PLAYER_MP = 1615, 1247
MP_REGEN = 6.0
HP_REGEN = 120.0            # HP自动补血(需求变更): 不考虑死亡场景, 受击中也持续回复

# --- 挥拳(近身攻击) ---------------------------------------------------------
# 规则(与原版录屏对齐): 近身时攻击 → 挥拳(微量伤害且吸引仇恨), 与MP无关;
# 非近身 → 双飞斩(MP无限模式不耗魔)
PUNCH_RANGE = 55            # 近身判定水平距离(px)
PUNCH_RANGE_Y = 50          # 近身判定垂直带宽(px)
PUNCH_DAMAGE = 10           # 挥拳微量伤害
PUNCH_COOLDOWN = 0.5

# --- 触碰MISS ---------------------------------------------------------------
# 被怪物触碰时按概率MISS: 不掉血/不击退/不硬直, 头顶漂浮"MISS"(录屏同款), 可自由行动
PLAYER_MISS_RATE = float(os.environ.get("MXD_MISS_RATE", "0.2"))
TOUCH_CD = 0.6              # 每只怪两次触碰判定间隔(MISS后防刷屏)

# --- 轻功(Haste, A键) --------------------------------------------------------
# 原版盗贼2转 Lv20 数据: 持续180秒; 本作取游戏世界上限状态(满速140%/满跳120%)
HASTE_MP_COST = 0           # MP无限模式(需求变更): 轻功不耗魔
HASTE_TIME = 180.0          # 原版Lv20持续时间(秒)
HASTE_SPEED_MULT = 1.40     # 满速 140%(+40)
HASTE_JUMP_MULT = 1.20      # 满跳 120%(起跳速度×1.2, 跳高≈104px)
HASTE_ICON_POS = (1242, 14) # 右上角buff图标位置
HASTE_ICON_SIZE = 26
HASTE_BLINK_REMAIN = 15.0   # 剩余<15秒图标闪烁(提示可补放)

# --- spawn -----------------------------------------------------------------
RESPAWN_SECONDS = 7.0       # 击杀后7秒重生

# --- exp / session ---------------------------------------------------------
EXP_TO_NEXT = 103400        # 由视频 34981/33.83% 反推
START_EXP = 34981
KILL_LOG = os.path.join(ROOT, "logs", "kill_log.csv")
EXP_STATE = os.path.join(ROOT, "logs", "session_exp.json")

# --- ui --------------------------------------------------------------------
HUD_IMG = os.path.join(ROOT, "assets/ui/hud_bottom.png")
MINIMAP_PANEL = os.path.join(ROOT, "assets/ui/minimap_panel.png")
MINIMAP_ORIGIN = (4, 6)          # 面板内小地图canvas左上（本地坐标）
MINIMAP_SPAN = (117, 88)         # canvas 宽高
MOB_COLORS = [(200, 60, 60), (255, 120, 40), (240, 200, 60), (170, 170, 255)]

DAMAGE_COLOR = (255, 110, 150)   # 伤害数字粉色
DAMAGE_OUTLINE = (255, 255, 255)
PLAYER_NAME = "梦回2008夏天"
PLAYER_BADGE = "勤奋冒险家勋章"

FONT_CN = None   # 由ui模块按平台挑选
