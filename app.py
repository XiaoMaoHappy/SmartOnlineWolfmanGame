# -*- coding: utf-8 -*-
"""在线狼人杀 —— Flask REST API 入口。

运行：pip install flask && python app.py
前端：浏览器打开 http://localhost:60081/
"""
import threading
import uuid
import json
import queue
import os, sys

from flask import Flask, jsonify, request, send_from_directory, Response

import bot
from game import GameError, MAX_PLAYERS, MIN_PLAYERS, Room
from storage import load_rooms, save_rooms

# app = Flask(__name__, static_folder="static")

def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

app = Flask(__name__,
            template_folder=resource_path('templates'),
            static_folder=resource_path('static'))

LOCK = threading.RLock()
ROOMS = {}
SUBSCRIBERS = {}          # room_id -> [queue.Queue, ...]  SSE 订阅者

# 启动时恢复未结束房间
ROOMS.update(load_rooms())


def ok(data=None):
    return jsonify({"code": 0, "data": data or {}, "msg": "ok"})


def notify(room_id):
    """状态变更时向该房间所有 SSE 订阅者推送事件。"""
    for q in list(SUBSCRIBERS.get(room_id, [])):
        try:
            q.put_nowait(time_marker())
        except queue.Full:
            pass


def time_marker():
    return int(__import__("time").time() * 1000)


def _bind_room(room):
    """为房间绑定状态变更回调（SSE 广播）。"""
    rid = room.room_id
    room.on_change = lambda: notify(rid)
    return room


# 恢复的房间绑定 SSE 回调
for _r in ROOMS.values():
    _bind_room(_r)


def gen_room_id():
    while True:
        rid = uuid.uuid4().hex[:6].upper()
        if rid not in ROOMS:
            return rid


def gen_player_id(room):
    while True:
        pid = "p" + uuid.uuid4().hex[:8]
        if pid not in room.players:
            return pid


def get_token():
    """从 query / body / header 中获取 token。"""
    token = request.args.get("token")
    if not token:
        token = (request.get_json(silent=True) or {}).get("token")
    if not token:
        token = request.headers.get("X-Token")
    return token


def need_room(room_id):
    room = ROOMS.get(room_id)
    if room is None:
        raise GameError(404, "房间不存在")
    return room


def need_auth(room, role_check=None):
    token = get_token()
    if not token:
        raise GameError(401, "缺少 token")
    pid = room.token_owner(token)
    if pid is None:
        raise GameError(401, "无效 token")
    if role_check and not role_check(room, pid):
        raise GameError(403, "无权执行此操作")
    return pid


def _save_locked():
    save_rooms(ROOMS)


# ---------- 房间 ----------
@app.route("/api/rooms", methods=["POST"])
def create_room():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        raise GameError(400, "请填写昵称")
    if len(name) > 20:
        name = name[:20]
    raw_ai = data.get("ai_count") or 0
    try:
        ai_count = int(raw_ai)
    except (TypeError, ValueError):
        raise GameError(400, "ai_count 必须是数字")
    if ai_count < 0 or ai_count > MAX_PLAYERS - 1:
        raise GameError(400, "AI 玩家数量应在 0 ~ %d 之间" % (MAX_PLAYERS - 1))
    with LOCK:
        room_id = gen_room_id()
        room = Room(room_id, "", name)
        host_id = gen_player_id(room)
        room.host_id = host_id
        token = room.add_player(host_id, name)
        for i in range(ai_count):
            room.add_ai_player("AI玩家%d" % (i + 1))
        _bind_room(room)
        ROOMS[room_id] = room
        _save_locked()
    return ok({
        "room_id": room_id,
        "player_id": host_id,
        "token": token,
        "host": True,
        "ai_count": ai_count,
    })


@app.route("/api/rooms", methods=["GET"])
def list_rooms():
    with LOCK:
        items = [
            {
                "room_id": r.room_id,
                "player_count": len(r.players),
                "status": r.status,
                "host": r.player_name(r.host_id),
                "created_at": r.created_at,
            }
            for r in ROOMS.values()
        ]
    return ok({"rooms": items})


@app.route("/api/rooms/<room_id>/join", methods=["POST"])
def join_room(room_id):
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        raise GameError(400, "请填写昵称")
    if len(name) > 20:
        name = name[:20]
    with LOCK:
        room = need_room(room_id)
        if room.status != "waiting":
            raise GameError(403, "游戏已开始，无法加入")
        if len(room.players) >= MAX_PLAYERS:
            raise GameError(400, "房间已满")
        for existing in room.players:
            if room.names[existing] == name:
                raise GameError(400, "该昵称已被使用")
        pid = gen_player_id(room)
        token = room.add_player(pid, name)
        _save_locked()
    return ok({"room_id": room_id, "player_id": pid, "token": token, "host": False})


@app.route("/api/rooms/<room_id>/start", methods=["POST"])
def start_game(room_id):
    with LOCK:
        room = need_room(room_id)
        pid = need_auth(room, lambda r, p: p == r.host_id)
        room.start()
        _save_locked()
    bot.drive(room, LOCK)
    return ok({"started": True, "round": room.round})


@app.route("/api/rooms/<room_id>/restart", methods=["POST"])
def restart_game(room_id):
    """房主在一局结束后重开一局（回到大厅等待再次开始）。"""
    with LOCK:
        room = need_room(room_id)
        need_auth(room, lambda r, p: p == r.host_id)
        room.restart()
        _save_locked()
    return ok({"restarted": True, "status": room.status})


@app.route("/api/rooms/<room_id>/leave", methods=["POST"])
def leave_room(room_id):
    """问题4：玩家退出房间并将其除名。
    房主退出则移交给随机真人玩家；若无真人玩家则关闭房间。
    """
    with LOCK:
        room = need_room(room_id)
        pid = need_auth(room)
        result = room.remove_player(pid)
        if result.get("closed"):
            ROOMS.pop(room_id, None)
            _save_locked()
            return ok({"closed": True, "removed": pid})
        _save_locked()
    return ok(result)


@app.route("/api/rooms/<room_id>/config", methods=["POST"])
def config_room(room_id):
    """问题5：房主在等候室修改游戏设置（AI 数量 / 角色配置 / 胜利条件）。"""
    data = request.get_json(silent=True) or {}
    with LOCK:
        room = need_room(room_id)
        need_auth(room, lambda r, p: p == r.host_id)
        room.set_config(
            ai_count=data.get("ai_count"),
            role_config=data.get("role_config"),
            win_condition=data.get("win_condition"),
        )
        _save_locked()
    return ok({
        "ai_count": room.ai_count,
        "role_config": room.role_config,
        "win_condition": room.win_condition,
    })


@app.route("/api/rooms/<room_id>/state", methods=["GET"])
def room_state(room_id):
    with LOCK:
        room = need_room(room_id)
        pid = need_auth(room)
        room.mark_seen(pid)
        # Bug 5: 查询时顺带检查夜间环节超时，覆盖无 AI 的纯人类房间
        room.check_night_step_timeout()
        # 问题3：查询时顺带检查白天发言超时
        room.check_speak_timeout()
        # 任务2：查询时顺带检查投票超时
        room.check_vote_timeout()
        # 任务2：查询时顺带检查遗言超时
        room.check_last_words_timeout()
        state = room.state_for(get_token())
    return ok(state)


@app.route("/api/rooms/<room_id>/stream", methods=["GET"])
def room_stream(room_id):
    """SSE 推送端点：房间状态变更时主动向订阅者推送事件。

    前端用 EventSource 监听，收到 update 事件后调用 /state 拉取最新状态重绘。
    鉴权通过 query 参数 token 完成（EventSource 不支持自定义 header）。
    """
    room = need_room(room_id)
    pid = need_auth(room)
    with LOCK:
        room.mark_seen(pid)
    rid = room.room_id
    hello_version = room.version

    def gen():
        q = queue.Queue()
        with LOCK:
            SUBSCRIBERS.setdefault(rid, []).append(q)
        try:
            yield "data: %s\n\n" % json.dumps({"type": "hello", "version": hello_version})
            while True:
                try:
                    marker = q.get(timeout=15)
                    yield "data: %s\n\n" % json.dumps({"type": "update", "ts": marker})
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            with LOCK:
                subs = SUBSCRIBERS.get(rid, [])
                if q in subs:
                    subs.remove(q)

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/rooms/<room_id>/action", methods=["POST"])
def do_action(room_id):
    data = request.get_json(silent=True) or {}
    action_type = data.get("type")
    target = data.get("target")
    with LOCK:
        room = need_room(room_id)
        pid = need_auth(room)
        room.mark_seen(pid)
        result = room.submit_action(pid, action_type, target)
        _save_locked()
    bot.drive(room, LOCK)
    return ok({"action": action_type, **result})


@app.route("/api/rooms/<room_id>/resolve_night", methods=["POST"])
def force_resolve_night(room_id):
    with LOCK:
        room = need_room(room_id)
        need_auth(room, lambda r, p: p == r.host_id)
        result = room.resolve_night()
        _save_locked()
    bot.drive(room, LOCK)
    return ok(result)


@app.route("/api/rooms/<room_id>/resolve_day", methods=["POST"])
def force_resolve_day(room_id):
    with LOCK:
        room = need_room(room_id)
        need_auth(room, lambda r, p: p == r.host_id)
        result = room.resolve_day()
        _save_locked()
    bot.drive(room, LOCK)
    return ok(result)


@app.route("/api/rooms/<room_id>/chat", methods=["POST"])
def send_chat(room_id):
    data = request.get_json(silent=True) or {}
    message = data.get("message", "")
    with LOCK:
        room = need_room(room_id)
        pid = need_auth(room)
        room.mark_seen(pid)
        room.add_chat(pid, message)
        _save_locked()
    bot.drive(room, LOCK)
    return ok({"ok": True})


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.errorhandler(GameError)
def handle_game_error(err):
    return jsonify({"code": err.code, "data": {}, "msg": err.msg}), err.code


@app.errorhandler(404)
def handle_404(_err):
    return jsonify({"code": 404, "data": {}, "msg": "接口不存在"}), 404


@app.errorhandler(405)
def handle_405(_err):
    return jsonify({"code": 405, "data": {}, "msg": "方法不允许"}), 405


@app.errorhandler(Exception)
def handle_500(_err):
    return jsonify({"code": 500, "data": {}, "msg": "服务器内部错误"}), 500


if __name__ == "__main__":
    print("房间数下限:", MIN_PLAYERS, " 上限:", MAX_PLAYERS)
    app.run(host="::", port=60081, debug=False)