# -*- coding: utf-8 -*-
"""任务1-3 专项验证。"""
import sys, os, random
sys.path.insert(0, os.path.dirname(__file__))
from game import Room, WOLF_ROLES, ROLE_NAMES, ROLE_CONFIGS
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

def test_task1_witch_prompt_has_kill():
    """任务1：AI女巫prompt包含狼人刀口。"""
    room = make_room(8, seed=42)
    wolves = [p for p in room.players if room.roles[p] in WOLF_ROLES]
    witch_pid = [p for p in room.players if room.roles[p] == "witch"]
    if not witch_pid:
        print("⚠ 无女巫，跳过"); return
    witch_pid = witch_pid[0]
    target = [p for p in room.players if p not in wolves and p != witch_pid][0]
    for w in wolves:
        room.submit_action(w, "kill", target)
    token = get_token(room, witch_pid)
    view = room.state_for(token)
    prompt = bot.build_prompt(view)
    assert "今晚狼刀目标" in prompt, "女巫prompt应包含狼刀目标"
    assert "解药只能救此人" in prompt, "女巫prompt应说明解药只能救被刀者"
    print("✓ 任务1: 女巫prompt包含狼刀目标")

def test_task2_effective_role_config():
    """任务2：state_for暴露effective_role_config。"""
    room = make_room(8, seed=42)
    token = get_token(room, room.players[0])
    state = room.state_for(token)
    assert "effective_role_config" in state, "应有effective_role_config"
    cfg = state["effective_role_config"]
    assert len(cfg) > 0, "配置不应为空"
    print("✓ 任务2: effective_role_config = %s" % cfg)

def test_task2_prompt_has_config():
    """任务2：AI prompt包含角色配置。"""
    room = make_room(8, seed=42)
    token = get_token(room, room.players[0])
    view = room.state_for(token)
    prompt = bot.build_prompt(view)
    assert "角色配置" in prompt, "build_prompt应包含角色配置"
    speak_prompt = bot.build_speak_prompt(view)
    assert "角色配置" in speak_prompt, "build_speak_prompt应包含角色配置"
    lw_prompt = bot.build_last_words_prompt(view)
    assert "角色配置" in lw_prompt, "build_last_words_prompt应包含角色配置"
    print("✓ 任务2: 所有prompt包含角色配置")

def test_task2_custom_config():
    """任务2：自定义配置正确暴露。"""
    random.seed(99)
    room = Room("R2", "host", "房主")
    room.add_player("host", "房主")
    for i in range(1, 6):
        room.add_player("p%d" % i, "玩家%d" % i)
    room.set_config(role_config={"werewolf": 2, "villager": 3, "seer": 1})
    room.start()
    token = get_token(room, room.players[0])
    state = room.state_for(token)
    cfg = state["effective_role_config"]
    assert cfg.get("狼人") == 2, "自定义配置应正确暴露"
    assert cfg.get("预言家") == 1
    print("✓ 任务2: 自定义配置正确暴露: %s" % cfg)

if __name__ == "__main__":
    test_task1_witch_prompt_has_kill()
    test_task2_effective_role_config()
    test_task2_prompt_has_config()
    test_task2_custom_config()
    print("\n✅ 全部验证通过")
