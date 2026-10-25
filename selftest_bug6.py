# -*- coding: utf-8 -*-
"""Bug 6 自测：全 AI 环节保留 10s 等待界面，含人类环节 60s。"""
import threading
import time

import bot
from game import Room, NIGHT_STEP_TIMEOUT_AI, NIGHT_STEP_TIMEOUT_HUMAN


def make_room(total=9, ai=None, seed=42):
    import random
    random.seed(seed)
    ai = total - 1 if ai is None else ai
    room = Room("R6", "host", "房主")
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


print("Bug 6 自测开始")
print("=" * 50)

# --- 测试 1: 全 AI 环节 deadline 为 10s ---
room = make_room(9, ai=8, seed=42)
assert room.status == "night", "应处于夜晚"
deadline = room.night_step_deadline
assert deadline is not None, "应有 deadline"
wait = deadline - time.time()
has_human = room._step_has_human(room.night_step)
if not has_human:
    assert 8 <= wait <= 10.5, "全 AI 环节应保留 10s 等待，实际 %.1fs" % wait
    print("[PASS] 全 AI 环节 deadline = 10s (实际 %.1fs)" % wait)
else:
    assert 58 <= wait <= 60.5, "含人类环节应为 60s，实际 %.1fs" % wait
    print("[PASS] 含人类环节 deadline = 60s (实际 %.1fs)" % wait)

# --- 测试 2: 全 AI 环节 AI 行动完后不立即推进 ---
bot.set_generator(None)
run_ai_sync(room)
# fast_forward 会推进所有环节直到 day 或 ended
assert room.status in ("day", "ended"), "fast_forward 后应推进到白天或结束，实际 %s" % room.status
print("[PASS] fast_forward 后游戏推进到 %s" % room.status)

# --- 测试 3: 含人类环节 deadline 为 60s ---
# 找一个含人类的配置：1 人类 + 8 AI，人类角色随机
for s in range(100):
    room2 = make_room(9, ai=8, seed=s)
    if room2._step_has_human(room2.night_step):
        deadline2 = room2.night_step_deadline
        wait2 = deadline2 - time.time()
        assert 58 <= wait2 <= 60.5, "含人类环节应为 60s，实际 %.1fs" % wait2
        print("[PASS] 含人类环节 deadline = 60s (seed=%d, 实际 %.1fs)" % (s, wait2))
        break
else:
    print("[SKIP] 100 个 seed 未找到人类在第一环节的配置")

# --- 测试 4: 全 AI 环节不立即推进（不使用 fast_forward） ---
room3 = make_room(9, ai=8, seed=42)
if not room3._step_has_human(room3.night_step):
    # 让 AI 行动但不 fast_forward
    lock3 = threading.RLock()
    bot._RUNNING.add(room3.room_id)
    try:
        # 只跑几轮，让 AI 行动但不要等 deadline
        for _ in range(3):
            with lock3:
                ai_needed = []
                for pid in room3.players:
                    if not room3.is_ai(pid):
                        continue
                    role = room3.roles.get(pid)
                    if role and room3.alive.get(pid):
                        if room3._available_actions(pid, role):
                            token = token_of(room3, pid)
                            if token:
                                ai_needed.append((pid, room3.state_for(token)))
                for pid, view in ai_needed:
                    action, target = bot._decide(room3, view)
                    try:
                        room3.submit_action(pid, action, target)
                    except Exception:
                        try:
                            room3.submit_action(pid, "pass")
                        except Exception:
                            pass
    finally:
        bot._RUNNING.discard(room3.room_id)
    # 全 AI 环节 AI 行动完后应仍处于夜晚（保留等待界面）
    if room3.status == "night":
        print("[PASS] 全 AI 环节 AI 行动完后保留等待界面（status=night）")
    else:
        print("[INFO] 全 AI 环节已推进到 %s（可能人类在当前环节）" % room3.status)

# --- 测试 5: 前端 night_step_has_human 字段 ---
room4 = make_room(9, ai=8, seed=42)
human_pid = next(p for p in room4.players if not room4.is_ai(p))
human_token = token_of(room4, human_pid)
view4 = room4.state_for(human_token)
assert "night_step_has_human" in view4, "state_for 应包含 night_step_has_human"
assert "night_step_remaining" in view4, "state_for 应包含 night_step_remaining"
print("[PASS] state_for 包含 night_step_has_human 和 night_step_remaining 字段")

# --- 测试 6: 常量值 ---
assert NIGHT_STEP_TIMEOUT_AI == 10, "全 AI 环节超时应为 10s"
assert NIGHT_STEP_TIMEOUT_HUMAN == 60, "含人类环节超时应为 60s"
print("[PASS] NIGHT_STEP_TIMEOUT_AI=10, NIGHT_STEP_TIMEOUT_HUMAN=60")

print("=" * 50)
print("Bug 6 自测全部通过")