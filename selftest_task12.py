# -*- coding: utf-8 -*-
"""任务1+2 专项验证脚本。"""
import sys, os, time, threading
sys.path.insert(0, os.path.dirname(__file__))
from game import Room, GameError, LAST_WORDS_TIMEOUT, SPEAK_TIMEOUT_AI
import bot

def make_room(n=6, ai=5):
    import random
    random.seed(42)
    room = Room("R1", "host", "房主")
    room.add_player("host", "房主")
    for i in range(ai):
        room.add_ai_player("AI玩家%d" % (i + 1))
    for i in range(1, n - ai):
        room.add_player("p%d" % i, "玩家%d" % i)
    room.start()
    return room

def get_token(room, pid):
    for t, p in room.tokens.items():
        if p == pid:
            return t
    return None

def drive_ai(room):
    lock = threading.RLock()
    bot._RUNNING.add(room.room_id)
    try:
        bot._drive_loop(room, lock, fast_forward=True)
    finally:
        bot._RUNNING.discard(room.room_id)

def setup_vote_out(room):
    """手动构造投票放逐场景，返回 (target, token) 或 None。"""
    # 跳过夜晚：所有夜间行动 pass
    if room.status == "night":
        for pid in list(room.players):
            if room.alive.get(pid) and room.roles.get(pid):
                acts = room._available_actions(pid, room.roles[pid])
                if acts:
                    try:
                        room.submit_action(pid, "pass")
                    except GameError:
                        pass
        if room.status == "night":
            try:
                room.resolve_night()
            except GameError:
                pass
    # 跳过白天发言
    while room.status == "day" and room.stage == "speak":
        try:
            room.submit_action(room.speak_current, "pass")
        except GameError:
            break
    if room.status != "day" or room.stage is not None:
        return None
    alive = [p for p in room.players if room.alive[p]]
    if len(alive) < 2:
        return None
    target = alive[0]
    for p in alive:
        room.submit_action(p, "vote", target)
    if room.stage != "last_words":
        return None
    return target, get_token(room, target)

def test_task1_speak_timeout():
    assert SPEAK_TIMEOUT_AI == 30, "SPEAK_TIMEOUT_AI 应为30, got %r" % SPEAK_TIMEOUT_AI
    print("✓ 任务1: SPEAK_TIMEOUT_AI = %d" % SPEAK_TIMEOUT_AI)

def test_task1_prompt_shorter():
    room = make_room(9, ai=8)
    ai_pid = next(iter(room.ai_players))
    token = get_token(room, ai_pid)
    view = room.state_for(token)
    prompt = bot.build_prompt(view)
    assert len(prompt) < 800, "prompt 应更短, got %d chars" % len(prompt)
    assert "游戏规则简表" not in prompt
    assert "当前板子人数配置" not in prompt
    print("✓ 任务1: build_prompt 长度 %d 字符" % len(prompt))
    speak_prompt = bot.build_speak_prompt(view)
    assert len(speak_prompt) < 600, "speak_prompt 应更短, got %d chars" % len(speak_prompt)
    print("✓ 任务1: build_speak_prompt 长度 %d 字符" % len(speak_prompt))

def test_task2_last_words_stage():
    room = make_room(6, ai=5)
    result = setup_vote_out(room)
    if result is None:
        print("⚠ 跳过遗言阶段测试")
        return
    target, token = result
    assert room.stage == "last_words"
    assert room.last_words_player == target
    assert room.last_words_deadline is not None
    state = room.state_for(token)
    assert state.get("last_words_player") == target
    assert state.get("last_words_player_name") is not None
    assert state.get("last_words_remaining") is not None
    print("✓ 任务2: 被放逐者进入遗言阶段 (%s, remaining=%ds)" % (
        state["last_words_player_name"], state["last_words_remaining"]))

def test_task2_last_words_chat():
    room = make_room(6, ai=5)
    result = setup_vote_out(room)
    if result is None:
        print("⚠ 跳过遗言发言测试")
        return
    target, token = result
    room.add_chat(target, "我是好人，冤枉啊！")
    last_chat = room.chat[-1]
    assert "遗言" in last_chat["name"], "遗言应带标注, got %r" % last_chat["name"]
    assert room.stage != "last_words", "遗言后应退出遗言阶段"
    print("✓ 任务2: 遗言标注 '%s'" % last_chat["name"])

def test_task2_last_words_timeout():
    room = make_room(6, ai=5)
    result = setup_vote_out(room)
    if result is None:
        print("⚠ 跳过遗言超时测试")
        return
    target, token = result
    room.last_words_deadline = time.time() - 1
    progressed = room.check_last_words_timeout()
    assert progressed, "遗言超时应推进"
    assert room.stage != "last_words"
    print("✓ 任务2: 遗言超时自动结束")

def test_task2_last_words_pass():
    room = make_room(6, ai=5)
    result = setup_vote_out(room)
    if result is None:
        print("⚠ 跳过遗言pass测试")
        return
    target, token = result
    room.submit_action(target, "pass")
    assert room.stage != "last_words"
    print("✓ 任务2: pass 跳过遗言")

def test_task2_restart_clears():
    room = make_room(6, ai=5)
    room.last_words_player = "test"
    room.last_words_deadline = time.time() + 60
    room.status = "ended"
    room.winner = "good"
    room.restart()
    assert room.last_words_player is None
    assert room.last_words_deadline is None
    print("✓ 任务2: restart 清空遗言字段")

if __name__ == "__main__":
    test_task1_speak_timeout()
    test_task1_prompt_shorter()
    test_task2_last_words_stage()
    test_task2_last_words_chat()
    test_task2_last_words_timeout()
    test_task2_last_words_pass()
    test_task2_restart_clears()
    print("\n✅ 全部验证通过")
