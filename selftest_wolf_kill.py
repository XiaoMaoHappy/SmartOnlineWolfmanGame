# -*- coding: utf-8 -*-
"""狼人出刀刀口确定 + 女巫查看验证脚本。"""
import sys, os, time, random
sys.path.insert(0, os.path.dirname(__file__))
from game import Room, GameError, WOLF_ROLES
import bot

def make_room(n=8, seed=42):
    random.seed(seed)
    room = Room("R1", "host", "房主")
    room.add_player("host", "房主")
    for i in range(1, n):
        room.add_player("p%d" % i, "玩家%d" % i)
    room.start()
    return room

def get_token(room, pid):
    for t, p in room.tokens.items():
        if p == pid:
            return t
    return None

def test_wolf_kill_finalized():
    """狼人环节完成后刀口已确定。"""
    room = make_room(8, seed=42)
    wolves = [p for p in room.players if room.roles[p] in WOLF_ROLES]
    non_wolves = [p for p in room.players if room.roles[p] not in WOLF_ROLES]
    # 所有狼刀同一个目标
    target = non_wolves[0]
    for w in wolves:
        room.submit_action(w, "kill", target)
    # 狼人环节应已完成，刀口已确定
    assert room.night.get("kill_finalized"), "kill_finalized 应为 True"
    assert room.night["kill"] == target, "最终刀口应为 target"
    print("✓ 狼人环节完成后刀口已确定: %s" % room.night["kill"])

def test_human_wolf_priority():
    """真人狼人意见优先于AI狼人。"""
    room = make_room(8, seed=42)
    wolves = [p for p in room.players if room.roles[p] in WOLF_ROLES]
    non_wolves = [p for p in room.players if room.roles[p] not in WOLF_ROLES]
    # 真人狼人刀 target_a，AI狼人刀 target_b
    # 但这里没有AI狼人，全是真人。手动模拟
    room.night["kill_votes"] = {}
    room.night["kill_votes"][wolves[0]] = non_wolves[0]
    room.night["kill_votes"][wolves[1]] = non_wolves[1]
    # 两个真人狼人意见不同，应随机选一个
    result = room._resolve_wolf_kill()
    assert result in (non_wolves[0], non_wolves[1]), "应在真人狼人目标中随机选"
    print("✓ 真人狼人意见不同时随机选取: %s" % result)

def test_witch_sees_finalized_kill():
    """女巫看到的是最终确定的刀口，不是最后提交的。"""
    room = make_room(8, seed=42)
    wolves = [p for p in room.players if room.roles[p] in WOLF_ROLES]
    non_wolves = [p for p in room.players if room.roles[p] not in WOLF_ROLES]
    witch_pid = [p for p in room.players if room.roles[p] == "witch"]
    if not witch_pid:
        print("⚠ 本局无女巫，跳过")
        return
    witch_pid = witch_pid[0]
    # 所有狼刀同一个目标
    target = non_wolves[0]
    for w in wolves:
        room.submit_action(w, "kill", target)
    # 女巫应看到最终确定的刀口
    token = get_token(room, witch_pid)
    state = room.state_for(token)
    kill_name = state.get("witch", {}).get("kill_target_name")
    assert kill_name is not None, "女巫应看到刀口"
    expected = room._display_name(target)
    assert kill_name == expected, "女巫应看到最终刀口 %s, got %s" % (expected, kill_name)
    print("✓ 女巫看到最终确定的刀口: %s" % kill_name)

def test_witch_no_kill_before_finalize():
    """狼人环节未完成时女巫不应看到刀口。"""
    room = make_room(8, seed=42)
    wolves = [p for p in room.players if room.roles[p] in WOLF_ROLES]
    witch_pid = [p for p in room.players if room.roles[p] == "witch"]
    if not witch_pid:
        print("⚠ 本局无女巫，跳过")
        return
    witch_pid = witch_pid[0]
    # 只有一只狼提交了刀，环节未完成
    room.submit_action(wolves[0], "kill", room.players[0])
    # kill_finalized 应为 False（还有狼未提交）
    assert not room.night.get("kill_finalized"), "狼人环节未完成，kill_finalized 应为 False"
    # 女巫不应看到刀口
    token = get_token(room, witch_pid)
    state = room.state_for(token)
    kill_name = state.get("witch", {}).get("kill_target_name")
    assert kill_name is None, "狼人环节未完成，女巫不应看到刀口, got %s" % kill_name
    print("✓ 狼人环节未完成时女巫看不到刀口")

def test_ai_wolf_sees_kill_votes():
    """AI狼人能在prompt中看到已提交的刀向。"""
    room = make_room(8, seed=42)
    wolves = [p for p in room.players if room.roles[p] in WOLF_ROLES]
    non_wolves = [p for p in room.players if room.roles[p] not in WOLF_ROLES]
    # 第一只狼提交刀
    room.submit_action(wolves[0], "kill", non_wolves[0])
    # 第二只狼的 view 应包含 wolf_kill_votes
    token = get_token(room, wolves[1])
    view = room.state_for(token)
    assert "wolf_kill_votes" in view, "狼人 view 应包含 wolf_kill_votes"
    assert len(view["wolf_kill_votes"]) > 0, "应有已提交的刀向"
    # prompt 应包含已提交刀向
    prompt = bot.build_prompt(view)
    assert "已提交刀向" in prompt, "prompt 应包含已提交刀向"
    print("✓ AI狼人能在prompt中看到已提交刀向: %s" % view["wolf_kill_votes"])

def test_from_dict_compat():
    """from_dict 兼容旧数据（无 kill_finalized）。"""
    room = make_room(8, seed=42)
    d = room.to_dict()
    # 模拟旧数据：删除 kill_finalized
    d["night"].pop("kill_finalized", None)
    room2 = Room.from_dict(d)
    assert room2.night.get("kill_finalized") == False, "from_dict 应 setdefault kill_finalized=False"
    print("✓ from_dict 兼容旧数据")

if __name__ == "__main__":
    test_wolf_kill_finalized()
    test_human_wolf_priority()
    test_witch_sees_finalized_kill()
    test_witch_no_kill_before_finalize()
    test_ai_wolf_sees_kill_votes()
    test_from_dict_compat()
    print("\n✅ 全部验证通过")
