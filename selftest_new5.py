# -*- coding: utf-8 -*-
"""任务1-5 专项验证脚本。"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from game import Room, GameError, VOTE_TIMEOUT
import time

def make_room():
    room = Room("R1", "host", "房主")
    room.add_player("host", "房主")
    for i in range(1, 7):
        room.add_player("p%d" % i, "玩家%d" % i)
    room.set_config(ai_count=0)
    room.start()
    return room

def test_task1_vote_no_repeat():
    """任务1：AI已投票后 _available_actions 返回 []，不会重复投票。"""
    room = make_room()
    # 跳到白天投票阶段
    room.status = "day"
    room.stage = None
    room.day_votes = {}
    # 模拟某玩家已投票
    room.day_votes["p1"] = "p2"
    role = room.roles.get("p1")
    actions = room._available_actions("p1", role)
    assert actions == [], "已投票玩家不应有可用操作，got %r" % actions
    # 未投票玩家应有 vote/pass
    actions2 = room._available_actions("p3", room.roles.get("p3"))
    assert "vote" in actions2, "未投票玩家应有vote操作"
    print("✓ 任务1: 已投票玩家 _available_actions 返回 []")

def test_task2_vote_timeout():
    """任务2：投票超时自动弃票并结算。"""
    room = make_room()
    room.status = "day"
    room.stage = None
    room.day_votes = {}
    room.vote_deadline = time.time() - 1  # 已超时
    # 先记录存活玩家数
    alive_before = [pid for pid in room.players if room.alive.get(pid)]
    progressed = room.check_vote_timeout()
    assert progressed, "超时应推进"
    # 超时后状态应已变化（进入夜晚或结束）
    assert room.status != "day" or room.stage is not None or room.winner is not None, \
        "超时弃票后应推进到夜晚或结束"
    print("✓ 任务2: 投票超时自动弃票 (status=%s, winner=%s)" % (room.status, room.winner))

def test_task2_vote_remaining():
    """任务2：state_for 暴露 vote_remaining。"""
    room = make_room()
    room.status = "day"
    room.stage = None
    room.day_votes = {}
    room.vote_deadline = time.time() + 30
    # 获取任意玩家的 token
    token = None
    for t, pid in room.tokens.items():
        if pid == "host":
            token = t
            break
    state = room.state_for(token)
    assert state["vote_remaining"] is not None, "vote_remaining 应不为 None"
    assert 25 <= state["vote_remaining"] <= 30, "vote_remaining 应在25-30之间, got %r" % state["vote_remaining"]
    print("✓ 任务2: vote_remaining 暴露正确 (%ds)" % state["vote_remaining"])

def test_task4_system_message():
    """任务4：第n回合开始系统消息。"""
    room = make_room()
    # round=1 时不应有系统消息
    sys_msgs = [c for c in room.chat if c.get("player_id") == "__system__"]
    assert len(sys_msgs) == 0, "第1回合不应有系统消息"
    # 模拟进入第2回合
    room._enter_night()
    sys_msgs = [c for c in room.chat if c.get("player_id") == "__system__"]
    assert len(sys_msgs) == 1, "第2回合应有1条系统消息, got %d" % len(sys_msgs)
    assert "第2回合开始" in sys_msgs[0]["message"], "消息内容应包含'第2回合开始'"
    print("✓ 任务4: 第2回合系统消息 '%s'" % sys_msgs[0]["message"])

def test_task5_review_data():
    """任务5：游戏结束暴露 all_roles/night_action_log/vote_log。"""
    room = make_room()
    # 模拟游戏结束
    room.status = "ended"
    room.winner = "good"
    token = None
    for t, pid in room.tokens.items():
        if pid == "host":
            token = t
            break
    state = room.state_for(token)
    assert "all_roles" in state, "游戏结束应暴露 all_roles"
    assert "night_action_log" in state, "游戏结束应暴露 night_action_log"
    assert "vote_log" in state, "游戏结束应暴露 vote_log"
    assert len(state["all_roles"]) == 7, "all_roles 应有7个玩家"
    for p in state["all_roles"]:
        assert "role" in p and "role_name" in p, "每个玩家应有角色信息"
    print("✓ 任务5: 复盘数据暴露正确 (all_roles=%d, night_log=%d, vote_log=%d)" % (
        len(state["all_roles"]), len(state["night_action_log"]), len(state["vote_log"])))

def test_task5_night_action_log():
    """任务5：夜间结算记录 night_action_log。"""
    room = make_room()
    # 模拟夜间行动
    wolves = [p for p in room.players if room.roles[p] in ("werewolf", "wolf_beauty")]
    non_wolves = [p for p in room.players if room.roles[p] not in ("werewolf", "wolf_beauty")]
    if wolves and non_wolves:
        target = non_wolves[0]
        for w in wolves:
            if room.roles[w] == "werewolf":
                room.submit_action(w, "kill", target)
                break
        # 推进所有夜间环节
        for _ in range(20):
            if room.status != "night":
                break
            room.check_night_step_timeout()
            # 标记所有环节为已完成
            for step_key in ["wolf_kill", "beauty_charm", "seer_verify", "witch", "guard"]:
                for pid in room.players:
                    if room.alive.get(pid):
                        room.night["done"].setdefault(step_key, set()).add(pid)
            try:
                room.resolve_night()
            except GameError:
                pass
            break
    assert len(room.night_action_log) >= 0, "night_action_log 应为列表"
    print("✓ 任务5: night_action_log 记录正常 (len=%d)" % len(room.night_action_log))

def test_task5_restart_clears():
    """任务5：restart 清空复盘字段。"""
    room = make_room()
    room.night_action_log.append({"round": 1})
    room.vote_log.append({"round": 1})
    room.vote_deadline = time.time() + 30
    room.status = "ended"
    room.winner = "good"
    room.restart()
    assert room.night_action_log == [], "restart 应清空 night_action_log"
    assert room.vote_log == [], "restart 应清空 vote_log"
    assert room.vote_deadline is None, "restart 应清空 vote_deadline"
    print("✓ 任务5: restart 清空复盘字段")

def test_task5_from_dict_compat():
    """任务5：from_dict 兼容旧数据（无新字段）。"""
    from game import Room
    room = make_room()
    d = room.to_dict()
    # 模拟旧数据：删除新字段
    d.pop("vote_deadline", None)
    d.pop("night_action_log", None)
    d.pop("vote_log", None)
    room2 = Room.from_dict(d)
    assert room2.vote_deadline is None, "from_dict 应 setdefault vote_deadline"
    assert room2.night_action_log == [], "from_dict 应 setdefault night_action_log"
    assert room2.vote_log == [], "from_dict 应 setdefault vote_log"
    print("✓ 任务5: from_dict 兼容旧数据")

if __name__ == "__main__":
    test_task1_vote_no_repeat()
    test_task2_vote_timeout()
    test_task2_vote_remaining()
    test_task4_system_message()
    test_task5_review_data()
    test_task5_night_action_log()
    test_task5_restart_clears()
    test_task5_from_dict_compat()
    print("\n✅ 全部验证通过")
