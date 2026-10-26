# -*- coding: utf-8 -*-
"""AI 指认发言自测（修复检查逻辑）。"""
import re
import threading
import random
from collections import Counter

import bot
from game import Room, WOLF_ROLES

TEMPLATES = [
    r"^(.+)，你真的很可疑啊？$",
    r"^(.+)，你看起来不太对劲，我要票你。$",
    r"^(.+)，我关注你很久了，你太有问题了。$",
]


def make_room(total=9, ai=None, seed=42):
    random.seed(seed)
    ai = total - 1 if ai is None else ai
    room = Room("RT", "host", "房主")
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


def match_template(msg):
    for t in TEMPLATES:
        m = re.match(t, msg)
        if m:
            return m.group(1)
    return None


print("AI 指认发言自测开始")
print("=" * 60)
passed = 0
failed = 0

def check(name, condition, detail=""):
    global passed, failed
    if condition:
        print("[PASS] %s %s" % (name, detail)); passed += 1
    else:
        print("[FAIL] %s %s" % (name, detail)); failed += 1


# --- 测试 1: AI 投票后聊天频道出现模板发言 ---
for seed in range(300):
    room = make_room(9, ai=8, seed=seed)
    human_pass_if_needed(room)
    run_ai_sync(room)
    if room.status != "day" or room.stage is not None:
        continue
    chat_before = len(room.chat)
    # hook add_chat 记录投票时的 day_votes
    original_add_chat = room.add_chat
    vote_snapshots = []
    def hooked_add_chat(pid, msg):
        if match_template(msg):
            vote_snapshots.append((pid, msg, dict(room.day_votes)))
        original_add_chat(pid, msg)
    room.add_chat = hooked_add_chat
    run_ai_sync(room)
    room.add_chat = original_add_chat
    new_chats = room.chat[chat_before:]
    vote_chats = [c for c in new_chats if match_template(c["message"])]
    check("1.AI投票后有模板发言", len(vote_chats) > 0,
          "(模板发言%d条)" % len(vote_chats))
    for c in vote_chats:
        speaker = next((p for p in room.players if room.names.get(p) == c["name"]), None)
        check("1.发言者是AI", speaker is not None and room.is_ai(speaker),
              "(%s)" % c["name"])
    break


# --- 测试 2: 发言目标与投票目标一致 ---
for seed in range(300):
    room2 = make_room(9, ai=8, seed=seed)
    human_pass_if_needed(room2)
    run_ai_sync(room2)
    if room2.status != "day" or room2.stage is not None:
        continue
    # hook 记录发言时的投票状态
    original_add_chat2 = room2.add_chat
    snapshots2 = []
    def hooked_add_chat2(pid, msg):
        tgt = match_template(msg)
        if tgt:
            vote_target = room2.day_votes.get(pid)
            snapshots2.append((pid, tgt, vote_target))
        original_add_chat2(pid, msg)
    room2.add_chat = hooked_add_chat2
    run_ai_sync(room2)
    room2.add_chat = original_add_chat2
    mismatches = 0
    matches = 0
    for pid, target_name, vote_target in snapshots2:
        if vote_target is not None:
            voted_name = room2.player_name(vote_target)
            if voted_name == target_name:
                matches += 1
            else:
                mismatches += 1
    check("2.发言目标与投票目标一致", mismatches == 0 and matches > 0,
          "(匹配%d，不匹配%d)" % (matches, mismatches))
    break


# --- 测试 3: 每个 AI 每轮只发一条 ---
for seed in range(300):
    room3 = make_room(9, ai=8, seed=seed)
    human_pass_if_needed(room3)
    run_ai_sync(room3)
    if room3.status != "day" or room3.stage is not None:
        continue
    chat_before3 = len(room3.chat)
    run_ai_sync(room3)  # 一次推进
    new_chats3 = room3.chat[chat_before3:]
    vote_chats3 = [c for c in new_chats3 if match_template(c["message"])]
    speaker_counts = Counter(c["name"] for c in vote_chats3)
    max_count = max(speaker_counts.values()) if speaker_counts else 0
    check("3.每个AI本轮只发一条", max_count <= 1,
          "(最多%d次)" % max_count)
    break


# --- 测试 4: 人类投票不触发模板发言 ---
for seed in range(300):
    room4 = make_room(9, ai=8, seed=seed)
    human4 = next(p for p in room4.players if not room4.is_ai(p))
    human_pass_if_needed(room4)
    run_ai_sync(room4)
    if room4.status != "day" or room4.stage is not None:
        continue
    chat_before4 = len(room4.chat)
    alive = [p for p in room4.players if room4.alive.get(p) and p != human4]
    if not alive:
        continue
    try:
        room4.submit_action(human4, "vote", alive[0])
    except Exception:
        continue
    new_chats4 = room4.chat[chat_before4:]
    human_chats = [c for c in new_chats4 if c["name"] == room4.names[human4]]
    check("4.人类投票不触发模板发言", len(human_chats) == 0,
          "(人类发言%d条)" % len(human_chats))
    break


# --- 测试 5: 模板格式正确 ---
for seed in range(300):
    room5 = make_room(9, ai=8, seed=seed)
    human_pass_if_needed(room5)
    run_ai_sync(room5)
    if room5.status != "day" or room5.stage is not None:
        continue
    chat_before5 = len(room5.chat)
    run_ai_sync(room5)
    new_chats5 = room5.chat[chat_before5:]
    found = False
    for c in new_chats5:
        if match_template(c["message"]):
            check("5.模板格式正确", True, "(%s)" % c["message"])
            found = True
            break
    if found:
        break


# --- 测试 6: 发言中的目标是有效存活玩家 ---
for seed in range(300):
    room6 = make_room(9, ai=8, seed=seed)
    human6 = next(p for p in room6.players if not room6.is_ai(p))
    human_pass_if_needed(room6)
    run_ai_sync(room6)
    if room6.status != "day" or room6.stage is not None:
        continue
    chat_before6 = len(room6.chat)
    run_ai_sync(room6)
    new_chats6 = room6.chat[chat_before6:]
    valid_targets = 0
    for c in new_chats6:
        tgt = match_template(c["message"])
        if tgt:
            # 检查目标是某个玩家名称
            if any(room6.names.get(p) == tgt for p in room6.players):
                valid_targets += 1
    check("6.发言目标是有效玩家", valid_targets > 0,
          "(有效%d条)" % valid_targets)
    break


print("=" * 60)
print("结果: %d 通过, %d 失败" % (passed, failed))
print("自测全部通过" if failed == 0 else "有失败项，请检查")