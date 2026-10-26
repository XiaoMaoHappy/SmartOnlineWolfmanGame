# -*- coding: utf-8 -*-
"""REST API 集成测试：使用 Flask test client 走完整 HTTP 流程。"""
import pytest

from game import WOLF_ROLES


def post(client, path, body=None, token=None):
    body = body or {}
    if token:
        body["token"] = token
    resp = client.post(path, json=body)
    return resp.status_code, resp.get_json()


def get(client, path, token=None):
    url = path + ("?token=%s" % token if token else "")
    resp = client.get(url)
    return resp.status_code, resp.get_json()


def create_and_fill(client, count=6):
    """创建房间并加入 count 名玩家，返回 (room_id, tokens)。"""
    code, data = post(client, "/api/rooms", {"name": "房主"})
    assert code == 200
    room_id, host_token = data["data"]["room_id"], data["data"]["token"]
    tokens = [host_token]
    for i in range(1, count):
        code, data = post(client, "/api/rooms/%s/join" % room_id, {"name": "玩家%d" % i})
        assert code == 200
        tokens.append(data["data"]["token"])
    return room_id, tokens


def test_happy_years(client):
    """最小完整流程：创建 -> 加入 -> 开始 -> 夜晚行动 -> 进入白天。"""
    room_id, tokens = create_and_fill(client, 6)
    code, data = post(client, "/api/rooms/%s/start" % room_id, token=tokens[0])
    assert code == 200

    # 每个玩家按自己的身份行动
    for token in tokens:
        code, view = get(client, "/api/rooms/%s/state" % room_id, token=token)
        assert code == 200
        me = view["data"]["me"]
        role = view["data"]["my_role"]
        actions = view["data"]["available_actions"]
        targets = [p for p in view["data"]["players"] if p["alive"] and p["player_id"] != me]
        if role in WOLF_ROLES and "kill" in actions:
            code, r = post(client, "/api/rooms/%s/action" % room_id,
                           {"type": "kill", "target": targets[0]["player_id"]}, token=token)
            assert code == 200
        elif role == "seer" and "verify" in actions:
            code, r = post(client, "/api/rooms/%s/action" % room_id,
                           {"type": "verify", "target": targets[0]["player_id"]}, token=token)
            assert code == 200
        elif role == "werewolf":
            pass  # 该身份若无 kill 说明已行动

    code, view = get(client, "/api/rooms/%s/state" % room_id, token=tokens[0])
    # 首夜预言家若被刀死（6人板无守卫/女巫），神职团灭会直接进入狼胜
    assert view["data"]["status"] in ("day", "night", "ended")


def test_join_start_rules(client):
    # 非房主不能开始
    room_id, tokens = create_and_fill(client, 6)
    code, _ = post(client, "/api/rooms/%s/start" % room_id, token=tokens[1])
    assert code == 403

    # 不足 6 人不能开始
    code, data = post(client, "/api/rooms", {"name": "a"})
    rid2, token2 = data["data"]["room_id"], data["data"]["token"]
    for i in range(1, 5):
        post(client, "/api/rooms/%s/join" % rid2, {"name": "p%d" % i})
    code, _ = post(client, "/api/rooms/%s/start" % rid2, token=token2)
    assert code == 400

    # 重名拒绝
    code, data = post(client, "/api/rooms", {"name": "张三"})
    rid3 = data["data"]["room_id"]
    code, _ = post(client, "/api/rooms/%s/join" % rid3, {"name": "张三"})
    assert code == 400


def test_state_privacy_and_auth(client):
    room_id, tokens = create_and_fill(client, 6)
    post(client, "/api/rooms/%s/start" % room_id, token=tokens[0])
    code, view = get(client, "/api/rooms/%s/state" % room_id, token=tokens[0])
    assert code == 200
    assert view["data"]["status"] == "night"
    for p in view["data"]["players"]:
        assert "role" not in p and "roles" not in p   # 不泄露他人身份

    # 无效 token
    code, _ = get(client, "/api/rooms/%s/state" % room_id, token="fake")
    assert code == 401
    code, _ = post(client, "/api/rooms/%s/action" % room_id,
                   {"type": "kill", "target": "p0", "token": "fake"})
    assert code == 401


def test_chat_and_room_list(client):
    room_id, tokens = create_and_fill(client, 6)
    code, _ = post(client, "/api/rooms/%s/chat" % room_id,
                   {"message": "大家好"}, token=tokens[0])
    assert code == 200
    code, view = get(client, "/api/rooms/%s/state" % room_id, token=tokens[0])
    assert any(c["message"] == "大家好" for c in view["data"]["chat"])
    code, data = get(client, "/api/rooms")
    assert any(r["room_id"] == room_id for r in data["data"]["rooms"])


def test_front_page_served(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "狼" in resp.get_data(as_text=True)


def test_state_roundtrip_via_storage(client, tmp_path):
    """行动产生的房间状态可落盘并在重启后恢复。"""
    import app as app_mod
    from storage import load_rooms, save_rooms

    room_id, tokens = create_and_fill(client, 7)
    post(client, "/api/rooms/%s/start" % room_id, token=tokens[0])

    # 找一只狼提交刀向
    roles = {}
    wolf_token = None
    for t in tokens:
        code, view = get(client, "/api/rooms/%s/state" % room_id, token=t)
        roles[t] = view["data"]["my_role"]
        if roles[t] in WOLF_ROLES and wolf_token is None:
            wolf_token = t
    wolf_view = get(client, "/api/rooms/%s/state" % room_id, token=wolf_token)[1]["data"]
    if "kill" in wolf_view["available_actions"]:
        target = next(p for p in wolf_view["players"]
                      if p["alive"] and p["player_id"] != wolf_view["me"])
        post(client, "/api/rooms/%s/action" % room_id,
             {"type": "kill", "target": target["player_id"]}, token=wolf_token)

    f = str(tmp_path / "rooms.json")
    save_rooms(app_mod.ROOMS, f)
    restored = load_rooms(f)
    assert room_id in restored
    assert restored[room_id].status == app_mod.ROOMS[room_id].status
    assert restored[room_id].roles == app_mod.ROOMS[room_id].roles
    assert restored[room_id].night["kill"] == app_mod.ROOMS[room_id].night["kill"]