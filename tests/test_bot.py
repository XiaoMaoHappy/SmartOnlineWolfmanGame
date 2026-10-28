# -*- coding: utf-8 -*-
"""AI 玩家与「再来一局」功能测试。"""
import json
import re
import threading

import pytest

import bot
from game import GameError, MAX_PLAYERS, MIN_PLAYERS, ROLE_NAMES, WOLF_ROLES, Room


# ---------- 工具 ----------
def make_room(total=6, ai=None, seed=7):
    ai = total - 1 if ai is None else ai
    import random
    random.seed(seed)
    room = Room("RAI", "host", "房主")
    room.add_player("p0", "房主")
    for i in range(ai):
        room.add_ai_player("AI玩家%d" % (i + 1))
    for i in range(1, total - ai):
        room.add_player("p%d" % i, "玩家%d" % i)
    room.start()
    return room


def run_ai_sync(room, lock=None):
    """同步驱动房间内所有需要行动的 AI（等价于 bot.drive 的后台循环）。

    Bug 6: 全 AI 环节保留 10s 等待，测试环境用 fast_forward 快进超时。
    """
    if lock is None:
        lock = threading.RLock()
    bot._RUNNING.add(room.room_id)
    try:
        bot._drive_loop(room, lock, fast_forward=True)
    finally:
        bot._RUNNING.discard(room.room_id)


def token_of(room, pid):
    for t, p in room.tokens.items():
        if p == pid:
            return t
    return None


def human_pass_if_needed(room):
    """让人类房主按行动能力提交 pass（若需要行动）。"""
    human = room.host_id
    if room.roles.get(human) is None:
        return
    acts = room._available_actions(human, room.roles[human])
    if acts:
        room.submit_action(human, "pass")


# ---------- 提示词 ----------
def test_build_prompt_covers_identity_rules_situation_chat():
    room = make_room(9, ai=8, seed=11)
    ai = next(iter(room.ai_players))
    view = room.state_for(token_of(room, ai))
    prompt = bot.build_prompt(view)
    assert "【身份】" in prompt
    assert ROLE_NAMES[view["my_role"]] in prompt
    assert "【局势" in prompt
    assert "存活：" in prompt
    assert "【近期发言】" in prompt
    assert "【可用行动】" in prompt
    assert '"action"' in prompt
    assert "只输出JSON" in prompt


def test_build_prompt_shows_all_dead_players():
    room = make_room(6, ai=5, seed=12)
    ai = next(iter(room.ai_players))
    for w in [p for p in room.players if room.roles[p] in WOLF_ROLES]:
        room.submit_action(w, "kill", w)
    seer = [p for p in room.players if room.roles[p] == "seer"]
    if seer:
        room.submit_action(seer[0], "verify", seer[0])
    if room.status == "night":  # 若尚未自动结算则手动结算
        room.resolve_night()
    view = room.state_for(token_of(room, ai))
    prompt = bot.build_prompt(view)
    assert "出局：" in prompt


# ---------- AI 决策 ----------
def test_ai_submits_action_from_llm_reply():
    room = make_room(9, ai=8, seed=13)
    human_pass_if_needed(room)

    def fake(prompt):
        names = "kill：" in prompt
        pool = re.search(r"可选目标：(.+)", prompt)
        if names and pool:
            first = pool.group(1).split("、")[0]
            return json.dumps({"action": "kill", "target": first})
        if "verify：" in prompt and pool:
            parts = pool.group(1).split("、")
            return json.dumps({"action": "verify", "target": parts[1 % len(parts)]})
        return json.dumps({"action": "pass", "target": None})

    bot.set_generator(fake)
    run_ai_sync(room)
    assert room.status in ("day", "ended")
    assert room.night["kill"] is not None or room.night["verify"] is not None
    bot.set_generator(None)


def test_ai_heuristic_fallback_without_model():
    """无模型 / 回复不可用 → 启发式兜底，游戏仍能推进。"""
    room = make_room(9, ai=8, seed=14)
    human_pass_if_needed(room)
    bot.set_generator(None)  # 触发重新探测 ai 模块（环境中不存在）
    run_ai_sync(room)
    assert room.status in ("day", "ended")
    bot.set_generator(None)


def test_ai_with_invalid_reply_falls_back():
    room = make_room(9, ai=8, seed=15)
    human_pass_if_needed(room)
    bot.set_generator(lambda prompt: "{这不是JSON}")  # 模型存在但回复无效
    run_ai_sync(room)
    assert room.status in ("day", "ended")
    bot.set_generator(None)


# ---------- AI 参与子阶段 ----------
def test_ai_hunter_gun_after_night_kill():
    room = make_room(12, ai=11, seed=16)
    hunter = next(p for p in room.players if room.roles[p] == "hunter")
    human_pass_if_needed(room)

    def fake(prompt):
        pool = re.search(r"可选目标：(.+)", prompt)
        if "gun：" in prompt and pool:
            name = pool.group(1).split("、")[0]
            return json.dumps({"action": "gun", "target": name})
        if "kill：" in prompt and pool:
            name = pool.group(1).split("、")[0]
            return json.dumps({"action": "kill", "target": name})
        return json.dumps({"action": "pass", "target": None})

    bot.set_generator(fake)
    # 先让狼刀死猎人
    for w in [p for p in room.players if room.roles[p] in WOLF_ROLES and room.alive[p]]:
        room.submit_action(w, "kill", hunter)
    run_ai_sync(room)
    assert room.stage != "gun_pending" or not room.alive[hunter]  # 开枪结算已过
    bot.set_generator(None)


# ---------- 再来一局（引擎） ----------
def play_simple_to_end(room):
    for _ in range(30):
        if room.status == "ended":
            return
        if room.status == "night":
            wolves = [p for p in room.players if room.alive[p] and room.roles[p] in WOLF_ROLES]
            good = [p for p in room.players if room.alive[p] and room.roles[p] not in WOLF_ROLES]
            if wolves:
                tgt = good[0] if good else wolves[0]
                for w in wolves:
                    room.submit_action(w, "kill", tgt)
            seers = [p for p in room.players if room.roles[p] == "seer" and room.alive[p]]
            if seers:
                pool = [p for p in room.players if room.alive[p] and p != seers[0]]
                room.submit_action(seers[0], "verify", pool[0])
            witch = [p for p in room.players if room.roles[p] == "witch" and room.alive[p]]
            if witch:
                room.submit_action(witch[0], "pass")
        elif room.status == "day":
            if room.stage == "speak":
                while room.status == "day" and room.stage == "speak":
                    room.submit_action(room.speak_current, "pass")
                continue
            if room.stage in ("charm_pending", "gun_pending"):
                pid = room.pending_lover or room.pending_gunner
                room.submit_action(pid, "pass")
                continue
            if room.stage == "last_words" and room.last_words_player:
                room.submit_action(room.last_words_player, "pass")
                continue
            wolves = [p for p in room.players if room.alive[p] and room.roles[p] in WOLF_ROLES]
            target = wolves[0] if wolves else room.players[0]
            for p in room.players:
                if room.alive[p]:
                    room.submit_action(p, "vote", target)
        else:
            raise AssertionError("未知状态 %s" % room.status)
    raise AssertionError("对局未在30轮内结束")


def test_restart_keeps_players_clears_game_state():
    room = make_room(6, ai=0, seed=17)  # make_room 内部已 start
    assert room.status == "night"
    play_simple_to_end(room)
    assert room.status == "ended"
    assert room.winner in ("good", "werewolf")
    players_before = list(room.players)
    room.restart()
    assert room.status == "waiting"
    assert room.roles == {}
    assert room.death_reasons == {}
    assert room.winner is None
    assert room.round == 0
    assert room.players == players_before  # 名单保留
    assert all(room.alive[p] for p in room.players)
    room.start()  # 可直接重开一局
    assert room.status == "night"
    assert room.round == 1


def test_restart_requires_ended():
    room = make_room(6, ai=0, seed=18)  # make_room 内部已 start
    with pytest.raises(GameError):
        room.restart()


# ---------- 再来一局（API） ----------
def test_restart_api_and_ai_creation(client):
    import app as app_mod

    code, data = post(client, "/api/rooms", {"name": "房主", "ai_count": 2})
    assert code == 200
    rid = data["data"]["room_id"]
    host_tok = data["data"]["token"]

    code, view = get(client, "/api/rooms/%s/state" % rid, token=host_tok)
    assert code == 200
    assert view["data"]["ai_count"] == 2
    assert len([p for p in view["data"]["players"] if p["is_ai"]]) == 2

    # 人数不足以开局时报错
    code, _ = post(client, "/api/rooms/%s/start" % rid, token=host_tok)
    assert code == 400

    # ai_count 非法
    code, _ = post(client, "/api/rooms", {"name": "x", "ai_count": 99})
    assert code == 400

    # 构造已结束房间直接验证 restart 接口
    with app_mod.LOCK:
        room = app_mod.ROOMS[rid]
        room.status = "ended"
        room.winner = "good"
        room.roles = {p: "villager" for p in room.players}
        room.names["p0"] = "房主"
    code, _ = post(client, "/api/rooms/%s/restart" % rid, token=host_tok)
    assert code == 200
    with app_mod.LOCK:
        assert app_mod.ROOMS[rid].status == "waiting"
        assert app_mod.ROOMS[rid].winner is None
    # 非房主无权重开
    code, data2 = post(client, "/api/rooms", {"name": "乙"})
    rid2 = data2["data"]["room_id"]
    code, _ = post(client, "/api/rooms/%s/restart" % rid2, token="fake")
    assert code == 401


def post(client, path, body=None, token=None):
    body = body or {}
    if token:
        body["token"] = token
    resp = client.post(path, json=body)
    return resp.status_code, resp.get_json()


def get(client, path, token=None):
    url = path + ("?token=%s" % token if token else "")
    return resp(client, url)


def resp(client, url):
    r = client.get(url)
    return r.status_code, r.get_json()


# ---------- AI 经由 HTTP API 驱动 ----------
def test_ai_players_driven_forward_after_start(client):
    import bot as bot_mod
    import app as app_mod
    import threading

    def fake(prompt):
        pool = re.search(r"可选目标：(.+)", prompt)
        names = pool.group(1).split("、") if pool else []
        if "kill：" in prompt and names:
            return json.dumps({"action": "kill", "target": names[0]})
        if "verify：" in prompt and names:
            return json.dumps({"action": "verify", "target": names[1 % len(names)]})
        return json.dumps({"action": "pass", "target": None})

    bot_mod.set_generator(fake)
    code, data = post(client, "/api/rooms", {"name": "房主", "ai_count": 5})
    assert code == 200
    rid = data["data"]["room_id"]
    host_tok = data["data"]["token"]
    code, _ = post(client, "/api/rooms/%s/start" % rid, token=host_tok)
    assert code == 200

    with app_mod.LOCK:
        room = app_mod.ROOMS[rid]
        human = room.host_id
        if room._available_actions(human, room.roles[human]):
            room.submit_action(human, "pass")
    bot_mod._RUNNING.add(rid)
    try:
        bot_mod._drive_loop(room, app_mod.LOCK, fast_forward=True)
    finally:
        bot_mod._RUNNING.discard(rid)
    assert room.status in ("day", "ended")
    bot_mod.set_generator(None)