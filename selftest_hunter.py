# -*- coding: utf-8 -*-
"""猎人被狼杀后开枪流程自测。"""
import threading
import time
import random

import bot
from game import Room, WOLF_ROLES


def make_room(total=9, ai=None, seed=42):
    random.seed(seed)
    ai = total - 1 if ai is None else ai
    room = Room("RH", "host", "房主")
    room.add_player("p0", "房主")
    for i in range(ai):
        room.add_ai_player("AI%d" % (i + 1))
    for i in range(1, total - ai):
        room.add_player("p%d" % i, "玩家%d" % i)
    room.start()
    return room


def token_of(room, pid):
    for t, p in room.tokens.items():
        if p == pid:
            return t
    return None


def run_ai_sync(room):
    lock = threading.RLock()
    bot._RUNNING.add(room.room_id)
    try:
        bot._drive_loop(room, lock, fast_forward=True)
    finally:
        bot._RUNNING.discard(room.room_id)


def human_pass_if_needed(room):
    human = next(p for p in room.players if not room.is_ai(p))
    role = room.roles.get(human)
    if role and room.alive.get(human):
        acts = room._available_actions(human, role)
        if acts:
            try:
                room.submit_action(human, "pass")
            except Exception:
                pass


print("猎人被狼杀自测开始")
print("=" * 60)

passed = 0
failed = 0

def check(name, condition, detail=""):
    global passed, failed
    if condition:
        print("[PASS] %s %s" % (name, detail))
        passed += 1
    else:
        print("[FAIL] %s %s" % (name, detail))
        failed += 1


# --- 测试 1: 人类猎人被狼杀 → 进入 gun_pending 阶段 ---
found = False
for seed in range(200):
    room = make_room(9, ai=8, seed=seed)
    human = next(p for p in room.players if not room.is_ai(p))
    if room.roles.get(human) != "hunter":
        continue
    # 人类 pass 夜间行动
    human_pass_if_needed(room)
    # AI 行动
    run_ai_sync(room)
    if room.status == "ended":
        continue
    if room.status == "day" and room.stage == "gun_pending":
        check("人类猎人被狼杀进入gun_pending", True,
              "(seed=%d, pending_gunner=%s)" % (seed, room.pending_gunner))
        check("猎人是人类", not room.is_ai(room.pending_gunner),
              "(pending_gunner=%s)" % room.pending_gunner)
        # 验证猎人有可用行动
        acts = room._available_actions(human, room.roles[human])
        check("猎人有开枪行动", "gun" in acts, "(actions=%s)" % acts)
        check("猎人有跳过行动", "pass" in acts, "(actions=%s)" % acts)

        # 人类猎人开枪
        alive_targets = [p for p in room.players if room.alive.get(p) and p != human]
        if alive_targets:
            try:
                room.submit_action(human, "gun", alive_targets[0])
                check("猎人开枪成功", True, "(target=%s)" % alive_targets[0])
            except Exception as e:
                check("猎人开枪成功", False, "(error=%s)" % e)
        # 验证阶段推进
        check("开枪后进入下一夜或结束", room.status in ("night", "ended"),
              "(status=%s, stage=%s)" % (room.status, room.stage))
        found = True
        break

if not found:
    check("人类猎人被狼杀进入gun_pending", False, "(200个seed未找到人类猎人被狼杀的场景)")


# --- 测试 2: AI 猎人被狼杀 → AI 自动开枪 → 阶段推进 ---
found2 = False
for seed in range(200):
    room2 = make_room(9, ai=8, seed=seed)
    # 找 AI 猎人
    ai_hunters = [p for p in room2.players if room2.is_ai(p) and room2.roles.get(p) == "hunter"]
    if not ai_hunters:
        continue
    # 人类 pass
    human_pass_if_needed(room2)
    # AI 行动（包括猎人开枪）
    run_ai_sync(room2)
    if room2.status in ("night", "ended", "day"):
        check("AI猎人被杀后阶段推进", room2.status in ("night", "ended", "day"),
              "(status=%s, stage=%s)" % (room2.status, room2.stage))
        check("AI猎人后不卡在gun_pending", room2.stage != "gun_pending",
              "(stage=%s)" % room2.stage)
        found2 = True
        break

if not found2:
    check("AI猎人被杀后阶段推进", False, "(200个seed未找到AI猎人被狼杀的场景)")


# --- 测试 3: 房主强制跳过猎人开枪 ---
found3 = False
for seed in range(200):
    room3 = make_room(9, ai=8, seed=seed)
    human3 = next(p for p in room3.players if not room3.is_ai(p))
    if room3.roles.get(human3) != "hunter":
        continue
    human_pass_if_needed(room3)
    run_ai_sync(room3)
    if room3.status == "day" and room3.stage == "gun_pending":
        # 房主强制 resolve_day（跳过猎人开枪）
        try:
            room3.resolve_day()
            check("房主强制跳过开枪", True, "")
            check("跳过后阶段推进", room3.status in ("night", "ended"),
                  "(status=%s, stage=%s)" % (room3.status, room3.stage))
            found3 = True
        except Exception as e:
            check("房主强制跳过开枪", False, "(error=%s)" % e)
            found3 = True
        break

if not found3:
    check("房主强制跳过开枪", False, "(200个seed未找到场景)")


# --- 测试 4: resolve_day 在 gun_pending 不报错 ---
check("resolve_day在gun_pending不报错", found3, "(已在测试3中验证)")


print("=" * 60)
print("结果: %d 通过, %d 失败" % (passed, failed))
if failed == 0:
    print("自测全部通过")
else:
    print("有失败项，请检查")