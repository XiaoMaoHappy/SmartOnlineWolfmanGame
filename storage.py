# -*- coding: utf-8 -*-
"""JSON 文件持久化：房间数据落盘，服务重启后恢复未结束房间。"""
import json
import os

from game import Room

DEFAULT_DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "werewolf_rooms.json")


def default_data_file():
    return os.environ.get("WEREWOLF_DATA_FILE") or DEFAULT_DATA_FILE


def load_rooms(data_file=None):
    """从 JSON 文件读取房间字典 {room_id: Room}，文件不存在时返回空字典。"""
    data_file = data_file or default_data_file()
    if not os.path.exists(data_file):
        return {}
    try:
        with open(data_file, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError):
        return {}
    rooms = {}
    for room_id, room_dict in payload.items():
        try:
            rooms[room_id] = Room.from_dict(room_dict)
        except (KeyError, TypeError, ValueError):
            continue
    return rooms


def save_rooms(rooms, data_file=None):
    """把房间字典序列化并写入 JSON 文件。"""
    data_file = data_file or default_data_file()
    payload = {}
    for room_id, room in rooms.items():
        try:
            payload[room_id] = room.to_dict()
        except Exception:
            continue
    tmp = data_file + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, data_file)
    except OSError:
        pass