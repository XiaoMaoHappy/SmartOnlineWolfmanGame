# -*- coding: utf-8 -*-
"""猎人三种死亡场景自测（修复场景查找）。"""
import threading
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

def run_ai_sync(room):
    lock = threading.RLock()
    bot._RUNNING.add(room.room_id)
    try:
        bot._drive_loop(room, lock, fast_forward=True)
    finally:
        bot._RUNNING.discard(room.room_id)

def human_pass_if_needed(room):
    for p in room.players:
        if room.is_ai(p):
            continue
        role = room.roles.get(p)
        if role and room.alive.get(p):
            acts = room._available_actions(p, role)
            if acts:
                try:
                    room.submit_action(p, "pass")
                except Exception:
                    pass

print("猎人三种死亡场景自测")
print("=" * 60)
passed = 0
failed = 0
def check(name, condition, detail=""):
    global passed, failed
    if condition:
        print("[PASS] %s %s" % (name, detail)); passed += 1
    else:
        print("[FAIL] %s %s" % (name, detail)); failed += 1

# --- 测试 1: 猎人被狼杀 → 开枪 → 进入白天投票 ---
for seed in range(300):
    room = make_room(9, ai=8, seed=seed)
    human = next(p for p in room.players if not room.is_ai(p))
    if room.roles.get(human) != "hunter": continue
    human_pass_if_needed(room)
    run_ai_sync(room)
    if room.status != "day" or room.stage != "gun_pending": continue
    targets = [p for p in room.players if room.alive.get(p) and p != human]
    if not targets: continue
    room.submit_action(human, "gun", targets[0])
    check("1.猎人被狼杀开枪后进入白天投票",
          room.status == "day" and room.stage is None,
          "(status=%s, stage=%s)" % (room.status, room.stage))
    human_pass_if_needed(room)
    run_ai_sync(room)
    check("1.白天投票后正常推进", room.status in ("night", "ended"), "(status=%s)" % room.status)
    break

# --- 测试 2: 猎人被放逐 → 开枪 → 进入下一夜 ---
# 不让AI自动投票，手动控制所有人投猎人
for seed in range(300):
    room2 = make_room(9, ai=8, seed=seed)
    ai_hunters = [p for p in room2.players if room2.is_ai(p) and room2.roles.get(p) == "hunter"]
    if not ai_hunters: continue
    hunter_pid = ai_hunters[0]
    wolves = [p for p in room2.players if room2.roles.get(p) in WOLF_ROLES and room2.alive.get(p)]
    non_hunters = [p for p in room2.players if p != hunter_pid and room2.alive.get(p) and room2.roles.get(p) not in WOLF_ROLES]
    if not wolves or not non_hunters: continue
    for w in wolves:
        try: room2.submit_action(w, "kill", non_hunters[0])
        except Exception: pass
    human_pass_if_needed(room2)
    run_ai_sync(room2)
    if room2.status != "day" or room2.stage is not None: continue
    if not room2.alive.get(hunter_pid): continue
    # 手动让所有存活玩家投猎人（不用run_ai_sync避免AI自动投票）
    for p in list(room2.players):
        if not room2.alive.get(p) or p == hunter_pid: continue
        acts = room2._available_actions(p, room2.roles[p])
        if "vote" in acts:
            try: room2.submit_action(p, "vote", hunter_pid)
            except Exception: pass
    if room2.status != "day" or room2.stage != "gun_pending": continue
    check("2.猎人被放逐进入开枪环节", True, "(gunner=%s)" % room2.pending_gunner)
    # AI猎人开枪
    run_ai_sync(room2)
    check("2.开枪后进入下一夜", room2.status in ("night", "ended"),
          "(status=%s, stage=%s)" % (room2.status, room2.stage))
    break

# --- 测试 3: 猎人被毒杀 → 不开枪 → 不卡流程 ---
for seed in range(300):
    room3 = make_room(9, ai=8, seed=seed)
    human3 = next(p for p in room3.players if not room3.is_ai(p))
    if room3.roles.get(human3) != "hunter": continue
    ai_witch = [p for p in room3.players if room3.is_ai(p) and room3.roles.get(p) == "witch"]
    if not ai_witch: continue
    witch_pid = ai_witch[0]
    try: room3.submit_action(human3, "pass")
    except Exception: pass
    try: room3.submit_action(witch_pid, "poison", human3)
    except Exception: continue
    run_ai_sync(room3)
    if room3.death_reasons.get(human3) != "poison": continue
    # 即使游戏结束也要验证没进入gun_pending
    check("3.毒杀后不进入开枪环节",
          not (room3.status == "day" and room3.stage == "gun_pending"),
          "(status=%s, stage=%s)" % (room3.status, room3.stage))
    check("3.毒杀后不卡流程", room3.status in ("day", "night", "ended"),
          "(status=%s)" % room3.status)
    if room3.status == "day":
        check("3.毒杀后进入白天投票", room3.stage is None, "(stage=%s)" % room3.stage)
    break

# --- 测试 4: 普通局天亮后放逐投票正常 ---
for seed in range(300):
    room4 = make_room(9, ai=8, seed=seed)
    human4 = next(p for p in room4.players if not room4.is_ai(p))
    if room4.roles.get(human4) == "hunter": continue
    human_pass_if_needed(room4)
    run_ai_sync(room4)
    if room4.status == "day" and room4.stage is None:
        check("4.普通局天亮后进入白天投票", True, "(seed=%d)" % seed)
        break

# --- 测试 5: 死亡原因字段区分 ---
for seed in range(300):
    room5 = make_room(9, ai=8, seed=seed)
    human5 = next(p for p in room5.players if not room5.is_ai(p))
    if room5.roles.get(human5) != "hunter": continue
    human_pass_if_needed(room5)
    run_ai_sync(room5)
    if room5.status == "day" and room5.stage == "gun_pending":
        check("5.狼杀死亡原因为wolf", room5.death_reasons.get(human5) == "wolf",
              "(reason=%s)" % room5.death_reasons.get(human5))
        break

print("=" * 60)
print("结果: %d 通过, %d 失败" % (passed, failed))
print("自测全部通过" if failed == 0 else "有失败项，请检查")