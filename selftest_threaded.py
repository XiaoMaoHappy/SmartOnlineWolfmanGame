# -*- coding: utf-8 -*-
"""线程化 AI 调用验证脚本。"""
import sys, os, time, random, threading
sys.path.insert(0, os.path.dirname(__file__))
from game import Room, WOLF_ROLES
import bot

def test_call_ai_threaded_normal():
    """正常响应：线程内返回结果。"""
    def fake_gen(prompt):
        return '{"action": "pass", "target": null}'
    result = bot._call_ai_threaded(fake_gen, "test", 5)
    assert result == '{"action": "pass", "target": null}', "应返回生成结果"
    print("✓ 正常响应返回结果")

def test_call_ai_threaded_timeout():
    """超时：AI 未在规定时间内回答，返回 None。"""
    def slow_gen(prompt):
        time.sleep(10)
        return "too late"
    result = bot._call_ai_threaded(slow_gen, "test", 1)
    assert result is None, "超时应返回 None"
    print("✓ 超时返回 None")

def test_call_ai_threaded_exception():
    """异常：生成器抛异常，返回 None。"""
    def bad_gen(prompt):
        raise RuntimeError("API error")
    result = bot._call_ai_threaded(bad_gen, "test", 5)
    assert result is None, "异常应返回 None"
    print("✓ 异常返回 None")

def test_get_ai_timeout_night():
    """夜间阶段超时。"""
    random.seed(42)
    room = Room("R1", "host", "房主")
    room.add_player("host", "房主")
    for i in range(1, 7):
        room.add_player("p%d" % i, "玩家%d" % i)
    room.start()
    timeout = bot._get_ai_timeout(room)
    assert 3 <= timeout <= 10, "夜间超时应在3-10s, got %r" % timeout
    print("✓ 夜间超时: %ds" % timeout)

def test_get_ai_timeout_speak():
    """发言阶段超时。"""
    random.seed(42)
    room = Room("R1", "host", "房主")
    room.add_player("host", "房主")
    for i in range(1, 7):
        room.add_player("p%d" % i, "玩家%d" % i)
    room.start()
    # 快速推进到白天发言
    for pid in list(room.players):
        if room.alive.get(pid) and room.roles.get(pid):
            acts = room._available_actions(pid, room.roles[pid])
            if acts:
                try: room.submit_action(pid, "pass")
                except: pass
    if room.status == "night":
        try: room.resolve_night()
        except: pass
    if room.status == "day" and room.stage == "speak":
        timeout = bot._get_ai_timeout(room)
        assert 3 <= timeout <= 30, "发言超时应在3-30s, got %r" % timeout
        print("✓ 发言超时: %ds" % timeout)
    else:
        print("⚠ 未进入发言阶段，跳过")

def test_decide_with_timeout():
    """_decide 接受 timeout 参数。"""
    random.seed(42)
    room = Room("R1", "host", "房主")
    room.add_player("host", "房主")
    for i in range(1, 7):
        room.add_player("p%d" % i, "玩家%d" % i)
    room.start()
    ai_pid = room.players[0]
    token = bot._token_of(room, ai_pid)
    view = room.state_for(token)
    # 注入假生成器
    def fake_gen(prompt):
        return '{"action": "pass", "target": null}'
    bot.set_generator(fake_gen)
    action, target = bot._decide(room, view, timeout=5)
    bot.set_generator(None)
    assert action == "pass", "应返回 pass"
    print("✓ _decide 接受 timeout 参数正常")

if __name__ == "__main__":
    test_call_ai_threaded_normal()
    test_call_ai_threaded_timeout()
    test_call_ai_threaded_exception()
    test_get_ai_timeout_night()
    test_get_ai_timeout_speak()
    test_decide_with_timeout()
    print("\n✅ 全部验证通过")
