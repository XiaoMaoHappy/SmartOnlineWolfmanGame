# -*- coding: utf-8 -*-
"""游戏引擎单元测试：直接驱动 game.Room，不启动 Flask。"""
import pytest

from game import (GameError, MAX_PLAYERS, MIN_PLAYERS, ROLE_CONFIGS,
                  ROLE_NAMES, WOLF_ROLES, Room)


def make_room(n, seed=None):
    if seed is not None:
        import random
        random.seed(seed)
    room = Room("R%02d" % n, "host", "房主")
    for i in range(n):
        room.add_player("p%d" % i, "玩家%d" % i)
    return room


def by_role(room, role, alive_only=True):
    return [
        p for p in room.players
        if room.roles[p] == role and (not alive_only or room.alive[p])
    ]


def vote_all(room, target, exclude=None):
    exclude = exclude or set()
    for p in room.players:
        if room.alive[p] and p not in exclude:
            room.submit_action(p, "vote", target)


def skip_speak(room):
    """问题1：跳过发言阶段，让所有存活玩家依次 pass。"""
    while room.status == "day" and room.stage == "speak":
        room.submit_action(room.speak_current, "pass")


def skip_last_words(room):
    """任务2：跳过遗言阶段。"""
    if room.status == "day" and room.stage == "last_words" and room.last_words_player:
        room.submit_action(room.last_words_player, "pass")


# ---------- 发牌 ----------
def test_all_configs_deal_correct_roles():
    for n, config in ROLE_CONFIGS.items():
        room = make_room(n, seed=n)
        room.start()
        deck = dict(config)
        for p in room.players:
            assert room.roles[p] is not None
            deck[room.roles[p]] -= 1
        assert all(v == 0 for v in deck.values()), (n, deck)


def test_start_min_players():
    room = make_room(MIN_PLAYERS - 1)
    with pytest.raises(GameError):
        room.start()


def test_start_max_players():
    room = make_room(MAX_PLAYERS + 1)
    with pytest.raises(GameError):
        room.start()


# ---------- 夜晚 ----------
def test_night_wolf_kill_alone():
    room = make_room(6, seed=1)
    room.start()
    seer = by_role(room, "seer")[0]
    victim = next(p for p in room.players if room.roles[p] == "villager")
    room.submit_action(seer, "verify", victim)
    for w in by_role(room, "werewolf"):
        room.submit_action(w, "kill", victim)
    assert room.status == "day"
    assert victim in room.night_deaths
    assert not room.alive[victim]


def test_guard_blocks_wolf_kill():
    room = make_room(12, seed=3)
    room.start()
    guard = by_role(room, "guard")[0]
    victim = next(p for p in room.players if room.roles[p] == "villager")
    room.submit_action(guard, "guard", victim)
    for w in by_role(room, "werewolf"):
        room.submit_action(w, "kill", victim)
    for b in by_role(room, "wolf_beauty"):
        room.submit_action(b, "pass")
    for s in by_role(room, "seer"):
        room.submit_action(s, "verify", victim)
    for w in by_role(room, "witch"):
        room.submit_action(w, "pass")
    assert room.status == "day"
    assert room.night_deaths == []
    assert room.alive[victim]


def test_witch_save_revives():
    room = make_room(8, seed=2)
    room.start()
    witch = by_role(room, "witch")[0]
    victim = next(p for p in room.players if room.roles[p] == "villager")
    for w in by_role(room, "werewolf"):
        room.submit_action(w, "kill", victim)
    room.submit_action(witch, "save")
    room.submit_action(witch, "pass")
    for s in by_role(room, "seer"):
        room.submit_action(s, "pass")
    assert room.night_deaths == []
    assert room.alive[victim]


def test_witch_poison_kills():
    room = make_room(7, seed=4)
    room.start()
    witch = by_role(room, "witch")[0]
    target = next(p for p in room.players if room.roles[p] == "villager")
    for w in by_role(room, "werewolf"):
        room.submit_action(w, "kill", w)  # 狼自刀，验证毒药与自刀并存
    room.submit_action(witch, "poison", target)
    room.submit_action(witch, "pass")
    for s in by_role(room, "seer"):
        room.submit_action(s, "pass")
    assert target in room.night_deaths
    assert not room.alive[target]


def test_witch_cannot_save_and_poison_same_night():
    room = make_room(7, seed=5)
    room.start()
    witch = by_role(room, "witch")[0]
    target = next(p for p in room.players if room.roles[p] == "villager")
    room.submit_action(witch, "save")
    with pytest.raises(GameError):
        room.submit_action(witch, "poison", target)


def test_wolf_kill_unified_team_target():
    room = make_room(9, seed=6)
    room.start()
    wolves = by_role(room, "werewolf")
    beauty = by_role(room, "wolf_beauty")[0]
    victim = next(p for p in room.players if room.roles[p] == "villager")
    room.submit_action(wolves[0], "kill", victim)
    room.submit_action(wolves[1], "kill", victim)
    room.submit_action(beauty, "pass")
    for s in by_role(room, "seer"):
        room.submit_action(s, "pass")
    for w in by_role(room, "witch"):
        room.submit_action(w, "pass")
    assert room.night_deaths == [victim]


# ---------- 白天 ----------
def test_vote_draw_is_peaceful_day():
    room = make_room(6, seed=7)
    room.start()
    seer = by_role(room, "seer")[0]
    victim = next(p for p in room.players if room.roles[p] == "villager")
    for w in by_role(room, "werewolf"):
        room.submit_action(w, "kill", victim)
    room.submit_action(seer, "verify", victim)
    assert room.status == "day"
    skip_speak(room)
    # 平票：a、b 各 2 票，余下 1 人弃权
    others = [p for p in room.players if room.alive[p] and p != victim]
    a, b = others[0], others[1]
    for i, p in enumerate(others):
        if i == 4:
            room.submit_action(p, "pass")          # 弃权
        else:
            room.submit_action(p, "vote", a if i % 2 == 0 else b)
    assert room.alive[a]
    assert room.alive[b]
    # 平票无人出局，进入下一夜
    assert room.status == "night"


def test_wolf_beauty_charm_on_banishment():
    room = make_room(9, seed=8)
    room.start()
    seer = by_role(room, "seer")[0]
    witch = by_role(room, "witch")[0]
    beauty = by_role(room, "wolf_beauty")[0]
    victim = next(p for p in room.players if room.roles[p] == "villager")
    # 第一夜：狼美人提前指定魅惑目标
    charm_target = next(p for p in room.players if p != beauty and room.alive[p] and p != victim)
    for w in by_role(room, "werewolf"):
        room.submit_action(w, "kill", victim)
    room.submit_action(beauty, "charm", charm_target)
    room.submit_action(seer, "verify", beauty)
    room.submit_action(witch, "pass")
    assert room.status == "day"
    skip_speak(room)
    # 投票放逐狼美人 -> 自动触发魅惑同死
    for p in room.players:
        if room.alive[p]:
            room.submit_action(p, "vote", beauty)
    skip_last_words(room)
    assert not room.alive[beauty]
    assert not room.alive[charm_target]
    assert room.death_reasons[charm_target] == "charm"


def test_hunter_gun_on_banishment():
    room = make_room(11, seed=9)
    room.start()
    hunter = by_role(room, "hunter")[0]
    seer = by_role(room, "seer")[0]
    witch = by_role(room, "witch")[0]
    wolves = by_role(room, "werewolf")
    victim = next(p for p in room.players if room.roles[p] == "villager")
    for w in wolves:
        room.submit_action(w, "kill", victim)
    for b in by_role(room, "wolf_beauty"):
        room.submit_action(b, "pass")
    room.submit_action(seer, "verify", victim)
    room.submit_action(witch, "pass")
    assert room.status == "day"
    skip_speak(room)
    for p in room.players:
        if room.alive[p]:
            room.submit_action(p, "vote", hunter)
    skip_last_words(room)
    assert room.stage == "gun_pending"
    shot = next(p for p in room.players if room.alive[p] and p != hunter)
    room.submit_action(hunter, "gun", shot)
    assert not room.alive[hunter]
    assert not room.alive[shot]
    assert room.death_reasons[shot] == "gun"


def test_hunter_poisoned_cannot_gun():
    room = make_room(11, seed=10)
    room.start()
    hunter = by_role(room, "hunter")[0]
    witch = by_role(room, "witch")[0]
    seer = by_role(room, "seer")[0]
    for w in by_role(room, "werewolf"):
        room.submit_action(w, "kill", w)  # 狼自刀
    for b in by_role(room, "wolf_beauty"):
        room.submit_action(b, "pass")
    room.submit_action(witch, "poison", hunter)
    room.submit_action(witch, "pass")
    room.submit_action(seer, "verify", hunter)
    assert room.status == "day"
    assert room.stage == "speak"  # 毒死猎人不进入开枪子阶段，进入发言阶段
    assert not room.alive[hunter]


def test_night_dead_hunter_guns():
    room = make_room(12, seed=11)
    room.start()
    hunter = by_role(room, "hunter")[0]
    guard = by_role(room, "guard")[0]
    seer = by_role(room, "seer")[0]
    witch = by_role(room, "witch")[0]
    for w in by_role(room, "werewolf"):
        room.submit_action(w, "kill", hunter)
    for b in by_role(room, "wolf_beauty"):
        room.submit_action(b, "pass")
    room.submit_action(guard, "guard", guard)
    room.submit_action(seer, "verify", hunter)
    room.submit_action(witch, "pass")
    assert room.status == "day"
    assert room.stage == "gun_pending"
    shot = next(p for p in room.players if room.alive[p] and p != hunter)
    room.submit_action(hunter, "gun", shot)
    assert not room.alive[shot]
    assert room.status in ("day", "night", "ended")


# ---------- 胜负 ----------
def test_good_win_when_all_wolves_dead():
    room = make_room(6, seed=12)
    room.start()
    wolves = by_role(room, "werewolf")
    seer = by_role(room, "seer")[0]
    villager = by_role(room, "villager")[0]
    # 第1夜：狼刀村民，预言家验狼2
    for w in wolves:
        room.submit_action(w, "kill", villager)
    room.submit_action(seer, "verify", wolves[1])
    assert room.status == "day"
    skip_speak(room)
    # 第1天：放逐狼1
    for p in room.players:
        if room.alive[p]:
            room.submit_action(p, "vote", wolves[0])
    skip_last_words(room)
    assert room.status == "night"
    # 第2夜：狼2刀另一个村民
    other = next(p for p in room.players if room.roles[p] == "villager" and room.alive[p])
    room.submit_action(wolves[1], "kill", other)
    room.submit_action(seer, "verify", wolves[1])
    assert room.status == "day"
    skip_speak(room)
    # 第2天：放逐狼2 -> 狼全灭，好人胜
    for p in room.players:
        if room.alive[p]:
            room.submit_action(p, "vote", wolves[1])
    skip_last_words(room)
    assert room.status == "ended"
    assert room.winner == "good"


def test_wolf_win_when_outnumber():
    room = make_room(6, seed=13)
    room.start()
    wolves = by_role(room, "werewolf")
    seer = by_role(room, "seer")[0]
    villager = by_role(room, "villager")[0]
    # 第1夜：狼刀村民
    for w in wolves:
        room.submit_action(w, "kill", villager)
    room.submit_action(seer, "verify", villager)
    assert room.status == "day"
    skip_speak(room)
    # 第1天：放逐预言家 -> 剩2狼2民，狼数>=好人数，狼胜
    for p in room.players:
        if room.alive[p]:
            room.submit_action(p, "vote", seer)
    skip_last_words(room)
    assert room.status == "ended"
    assert room.winner == "werewolf"


# ---------- 状态视角 ----------
def test_state_does_not_leak_roles():
    room = make_room(6, seed=14)
    room.start()
    tokens = {room.token_owner(t): t for t in room.tokens}
    p0 = room.players[0]
    view = room.state_for(tokens[p0])
    for p in view["players"]:
        assert "role" not in p
        assert "roles" not in p
    assert view["my_role"] == room.roles[p0]
    assert view["me"] == p0


def test_wolf_state_shows_teammates():
    room = make_room(9, seed=15)
    room.start()
    wolf = by_role(room, "werewolf")[0]
    token = next(t for t, p in room.tokens.items() if p == wolf)
    view = room.state_for(token)
    assert "wolf_teammates" in view
    assert all(room.roles[t["player_id"]] in WOLF_ROLES for t in view["wolf_teammates"])


def test_seer_verify_history_visible():
    room = make_room(6, seed=16)
    room.start()
    seer = by_role(room, "seer")[0]
    token = next(t for t, p in room.tokens.items() if p == seer)
    room.submit_action(seer, "verify", room.players[0])
    view = room.state_for(token)
    assert len(view["verify_history"]) == 1
    assert view["verify_history"][0]["target_name"] == room._display_name(room.players[0])


# ---------- 序列化 ----------
def test_room_serialization_roundtrip(tmp_path):
    room = make_room(7, seed=17)
    room.start()
    for w in by_role(room, "werewolf"):
        room.submit_action(w, "kill", room.players[-1])
    restored = Room.from_dict(room.to_dict())
    assert restored.room_id == room.room_id
    assert restored.status == room.status
    assert set(restored.night["done"]) == set(room.night["done"])
    assert restored.roles == room.roles
    assert restored.death_reasons == room.death_reasons
    token = next(iter(room.tokens))
    view = restored.state_for(token)
    assert view["room_id"] == room.room_id


# ---------- 全流程冒烟 ----------
def test_full_game_smoke():
    """驱动一局完整的狼美人局，直到产生胜负。"""
    room = make_room(9, seed=42)
    room.start()
    safety = 0
    while room.status != "ended":
        safety += 1
        assert safety < 40, "游戏未能在40回合内结束"
        if room.status == "night":
            good_ids = [
                p for p in room.players
                if room.alive[p] and room.roles[p] not in WOLF_ROLES
            ]
            kill_target = good_ids[0] if good_ids else room.players[0]
            for v in by_role(room, "werewolf", alive_only=True):
                room.submit_action(v, "kill", kill_target)
            for b in by_role(room, "wolf_beauty", alive_only=True):
                room.submit_action(b, "pass")
            seer = by_role(room, "seer", alive_only=True)
            if seer:
                room.submit_action(seer[0], "verify", good_ids[0])
            witch = by_role(room, "witch", alive_only=True)
            if witch:
                room.submit_action(witch[0], "pass")
            continue
        if room.status == "day":
            if room.stage == "speak":
                skip_speak(room)
                continue
            if room.stage in ("charm_pending", "gun_pending"):
                pid = room.pending_lover or room.pending_gunner
                if room.roles[pid] == "wolf_beauty":
                    candidates = [p for p in room.players if room.alive[p] and p != pid]
                    if candidates:
                        room.submit_action(pid, "charm", candidates[0])
                    else:
                        room.submit_action(pid, "pass")
                else:
                    room.submit_action(pid, "pass")
                continue
            if room.stage == "last_words":
                skip_last_words(room)
                continue
            alive_ids = [p for p in room.players if room.alive[p]]
            wolf_ids = [p for p in alive_ids if room.roles[p] in WOLF_ROLES]
            vote_target = wolf_ids[0] if wolf_ids else alive_ids[0]
            for p in alive_ids:
                room.submit_action(p, "vote", vote_target)
            continue
        break
    assert room.status == "ended"
    assert room.winner in ("good", "werewolf")