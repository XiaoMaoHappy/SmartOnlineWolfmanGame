# -*- coding: utf-8 -*-
"""AI 玩家接入层。

职责：
 1. 把游戏规则、当前局势、场上发言与 AI 自身身份组装成中文提示词；
 2. 调用接入大模型 API 的 ai_generator(prompt) 获得决策 JSON；
 3. 解析失败或模型缺失时，使用内置启发式策略兜底（保证对局可推进）；
 4. 通过后台线程驱动房间内所有需要行动的 AI 玩家。

约定：ai.py 需提供 `ai_generator(prompt: str) -> str`，返回纯 JSON：
    {"action": "<action>", "target": "<玩家名 或 null>"}
把 ai.py 放到本文件同目录即可自动发现；不在时不影响游戏运行（AI 走兜底）。
"""
import json
import random
import threading
import time as _time_module

from game import GameError, ROLE_NAMES, WOLF_ROLES

TARGET_ACTIONS = {"guard", "kill", "verify", "poison", "vote", "charm", "gun"}


def _seat_name(name, seat):
    """格式化玩家显示：带编号如「3号 张三」，无编号时只返回名字。"""
    if seat:
        return "%d号 %s" % (seat, name)
    return name

DEAD_REASON_CN = {
    "wolf": "淘汰", "poison": "淘汰", "vote": "被放逐",
    "charm": "淘汰", "gun": "被猎人猎杀", None: None,
}

ROLE_DESC = {
    "werewolf": "狼人：夜晚与狼队友共同选定一名玩家击杀；白天伪装成好人，引导投票放逐好人。",
    "wolf_beauty": "狼美人：夜晚与狼人一起杀人；你被【投票放逐】时可以魅惑一名存活玩家与你同归于尽（夜晚死亡不可魅惑）。",
    "seer": "预言家（神职）：每晚查验一名存活玩家，得知其阵营是「狼人」还是「好人」。你属于好人阵营。",
    "witch": "女巫（神职）：拥有一瓶解药（可救当晚被狼刀者）与一瓶毒药（毒杀一名玩家）；同一晚只能用其中一瓶，每瓶全局限用一次。你属于好人阵营。",
    "hunter": "猎人（神职）：被狼刀或【投票放逐】时可以开枪带走一名玩家；被毒死或被魅惑带走时不能开枪。你属于好人阵营。",
    "guard": "守卫（神职）：每晚守护一名玩家，被守护者当晚免疫狼刀。你属于好人阵营。",
    "villager": "平民：没有夜间技能，靠发言和投票找出狼人。你属于好人阵营。",
}

CAMPAIGN_CN = {"werewolf": "狼人阵营", "good": "好人阵营"}

_RUNNING = set()          # 正在驱动的房间号集合，避免并发重复驱动
_BOOT_LOCK = threading.Lock()
_GENERATOR = None         # None=未加载；False=加载失败/缺失


def set_generator(fn):
    """供测试注入假模型：fn(prompt)->str。"""
    global _GENERATOR
    _GENERATOR = fn


def _get_generator():
    global _GENERATOR
    if _GENERATOR is None:
        try:
            import ai as _ai
            _GENERATOR = _ai.ai_generator
        except Exception:
            _GENERATOR = False
    return _GENERATOR if _GENERATOR else None


# ---------- 线程化 AI 调用（超时控制） ----------
def _call_ai_threaded(generator, prompt, timeout):
    """在新线程中调用 AI 模型，超时返回 None。

    每次询问 AI 都开启一个新线程，当接收到回答或超时后结束线程。
    """
    result = [None]
    done = [False]

    def worker():
        try:
            result[0] = generator(prompt)
        except Exception:
            result[0] = None
        finally:
            done[0] = True

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout)
    # 线程仍在运行（超时），daemon 线程会在主进程退出时自动结束
    if not done[0]:
        return None
    return result[0]


def _get_ai_timeout(room):
    """根据当前游戏阶段确定 AI 调用超时时间（秒）。"""
    now = _time_module.time()
    if room.status == "night":
        if room.night_step_deadline:
            remain = room.night_step_deadline - now
            return max(3, min(remain, 10))
        return 10
    if room.status == "day":
        if room.stage == "speak" and room.speak_deadline:
            remain = room.speak_deadline - now
            return max(3, min(remain, 30))
        if room.stage is None and room.vote_deadline:
            remain = room.vote_deadline - now
            return max(3, min(remain, 30))
        if room.stage == "last_words" and room.last_words_deadline:
            remain = room.last_words_deadline - now
            return max(3, min(remain, 30))
        if room.stage in ("gun_pending", "charm_pending"):
            return 10
    return 10


# ---------- 提示词构造 ----------
def build_prompt(view):
    role = view["my_role"] or "villager"
    lines = []
    lines.append("你是狼人杀对局中的一名玩家，请做出最合理的决策。")
    lines.append("你的编号：%s" % _seat_name(view["my_name"], view.get("my_seat")))
    lines.append("")
    lines.append("【身份】%s（%s）" % (ROLE_NAMES.get(role, role),
                                     "狼人阵营" if role in WOLF_ROLES else "好人阵营"))
    lines.append(ROLE_DESC.get(role, ""))
    if view.get("wolf_teammates"):
        lines.append("狼队友：%s" % "、".join(_seat_name(t["name"], t.get("seat")) for t in view["wolf_teammates"]))
    if view.get("wolf_kill_votes"):
        lines.append("已提交刀向：%s" % "；".join(
            "%s→%s" % (v, t) for v, t in view["wolf_kill_votes"].items()))
    if view.get("verify_history"):
        lines.append("查验记录：%s" % "；".join(
            "第%d晚 %s→%s" % (h["round"], h["target_name"],
                              "狼" if h["result"] == "werewolf" else "好")
            for h in view["verify_history"][-5:]))
    if view.get("witch"):
        w = view["witch"]
        if w.get("kill_target_name"):
            lines.append("今晚狼刀目标：%s（解药只能救此人）" % w["kill_target_name"])
        lines.append("药水：解药【%s】毒药【%s】"
                     % ("已用" if w["save_used"] else "可用",
                        "已用" if w["poison_used"] else "可用"))

    lines.append("")
    lines.append("【局势（第%s轮）】" % view["round"])
    if view.get("effective_role_config"):
        lines.append("角色配置：%s" % "、".join(
            "%s×%d" % (r, c) for r, c in view["effective_role_config"].items()))
    lines.append("阶段：%s" % ("黑夜" if view["status"] == "night" else "白天"))
    if view["status"] == "day" and view["night_death_names"]:
        lines.append("昨晚死讯：%s" % "、".join(view["night_death_names"]))
    alive_names = [_seat_name(p["name"], p.get("seat")) for p in view["players"] if p["alive"]]
    dead_names = [_seat_name(p["name"], p.get("seat")) + "（" + (DEAD_REASON_CN.get(p["dead_reason"]) or "出局") + "）"
                  for p in view["players"] if not p["alive"]]
    lines.append("存活：%s" % ("、".join(alive_names) or "无"))
    if dead_names:
        lines.append("出局：%s" % "；".join(dead_names))
    if view.get("elimination_log"):
        lines.append("出局历史：%s" % "；".join(
            "第%d回合 %s %s" % (e["round"], e["name"], e["reason"])
            for e in view["elimination_log"][-5:]))
    if view["status"] == "day" and view["day_votes"]:
        lines.append("当前票况：%s" % "；".join(
            "%s→%s" % (v, t) for v, t in view["day_votes"].items()))

    lines.append("")
    lines.append("【近期发言】")
    if view["chat"]:
        for c in view["chat"][-8:]:
            lines.append("  %s：%s" % (c.get("name", "?"), c.get("message", "")))
    else:
        lines.append("  （暂无）")

    lines.append("")
    lines.append("【可用行动】")
    acts = view.get("available_actions") or []
    act_desc = {
        "kill": "狼刀目标", "guard": "守护目标", "verify": "查验目标",
        "save": "使用解药", "poison": "毒杀目标", "vote": "投票目标",
        "charm": "魅惑目标", "gun": "开枪目标", "pass": "跳过/弃权",
    }
    for a in acts:
        lines.append("  %s：%s" % (a, act_desc.get(a, a)))
    candidates = [_seat_name(p["name"], p.get("seat")) for p in view["players"] if p["alive"] and p["player_id"] != view["me"]]
    if candidates:
        lines.append("可选目标：%s" % "、".join(candidates))
    lines.append("")
    lines.append('只输出JSON：{"action":"<action>","target":"<玩家名或null>"}')
    return "\n".join(lines)


# ---------- 白天发言提示词 ----------
def build_speak_prompt(view):
    """问题1：构造白天发言提示词，让大模型生成一段符合身份的讨论发言。"""
    role = view["my_role"] or "villager"
    is_wolf = role in WOLF_ROLES
    lines = []
    lines.append("你是狼人杀对局中的玩家，现在轮到你白天发言。")
    lines.append("你的编号：%s" % _seat_name(view["my_name"], view.get("my_seat")))
    lines.append("")
    lines.append("【身份】%s（%s）" % (ROLE_NAMES.get(role, role),
                                     "狼人阵营" if is_wolf else "好人阵营"))
    if view.get("wolf_teammates"):
        lines.append("狼队友：%s" % "、".join(_seat_name(t["name"], t.get("seat")) for t in view["wolf_teammates"]))
    if view.get("verify_history"):
        lines.append("查验记录：%s" % "；".join(
            "第%d晚 %s→%s" % (h["round"], h["target_name"],
                              "狼" if h["result"] == "werewolf" else "好")
            for h in view["verify_history"][-3:]))
    lines.append("")
    lines.append("【局势（第%s轮）】" % view["round"])
    if view.get("effective_role_config"):
        lines.append("角色配置：%s" % "、".join(
            "%s×%d" % (r, c) for r, c in view["effective_role_config"].items()))
    if view["night_death_names"]:
        lines.append("昨晚死讯：%s" % "、".join(view["night_death_names"]))
    alive_names = [_seat_name(p["name"], p.get("seat")) for p in view["players"] if p["alive"]]
    lines.append("存活：%s" % ("、".join(alive_names) or "无"))
    if view.get("elimination_log"):
        lines.append("出局历史：%s" % "；".join(
            "第%d回合 %s %s" % (e["round"], e["name"], e["reason"])
            for e in view["elimination_log"][-3:]))
    lines.append("")
    lines.append("【近期发言】")
    if view["chat"]:
        for c in view["chat"][-6:]:
            lines.append("  %s：%s" % (c.get("name", "?"), c.get("message", "")))
    else:
        lines.append("  （暂无）")
    lines.append("")
    lines.append("直接输出发言内容（纯文本，不超过100字）。")
    if is_wolf:
        lines.append("你是狼人，伪装好人、带节奏，不暴露狼队友。")
    else:
        lines.append("你是好人，分析死讯、指认可疑玩家。")
    return "\n".join(lines)


def _decide_speak(room, view, timeout=30):
    """问题1：AI 白天发言——调用大模型生成，失败时用模板兜底。"""
    generator = _get_generator()
    if generator:
        reply = _call_ai_threaded(generator, build_speak_prompt(view), timeout)
        if reply and isinstance(reply, str):
            text = reply.strip()
            # 兼容模型误输出 JSON 的情况
            if text.startswith("{") and text.endswith("}"):
                try:
                    data = json.loads(text)
                    if isinstance(data, dict) and "message" in data:
                        text = str(data["message"]).strip()
                    elif isinstance(data, dict) and "content" in data:
                        text = str(data["content"]).strip()
                except (ValueError, TypeError):
                    pass
            # 去除首尾引号/书名号
            text = text.strip('"').strip("'").strip("「」").strip("“”").strip()
            # 去除多余换行，压成一行
            text = " ".join(text.split())
            if len(text) > 200:
                text = text[:200]
            if text:
                return text
    return random.choice(_AI_SPEAK_TEMPLATES)


# ---------- 遗言提示词 ----------
def build_last_words_prompt(view):
    """任务2：被放逐者遗言提示词（尽量短，快速生成）。"""
    role = view["my_role"] or "villager"
    lines = []
    lines.append("你被投票放逐了，请发表一句遗言。")
    lines.append("你的编号：%s" % _seat_name(view["my_name"], view.get("my_seat")))
    lines.append("身份：%s" % ROLE_NAMES.get(role, role))
    if view.get("effective_role_config"):
        lines.append("角色配置：%s" % "、".join(
            "%s×%d" % (r, c) for r, c in view["effective_role_config"].items()))
    if view.get("verify_history"):
        lines.append("查验记录：%s" % "；".join(
            "第%d晚 %s→%s" % (h["round"], h["target_name"],
                              "狼" if h["result"] == "werewolf" else "好")
            for h in view["verify_history"][-3:]))
    if view.get("wolf_teammates"):
        lines.append("狼队友：%s" % "、".join(_seat_name(t["name"], t.get("seat")) for t in view["wolf_teammates"]))
    lines.append("直接输出遗言内容（纯文本，不超过50字）。")
    return "\n".join(lines)


def _decide_last_words(room, view, timeout=30):
    """任务2：AI 遗言——调用大模型生成，失败时用模板兜底。"""
    generator = _get_generator()
    if generator:
        reply = _call_ai_threaded(generator, build_last_words_prompt(view), timeout)
        if reply and isinstance(reply, str):
            text = reply.strip()
            if text.startswith("{") and text.endswith("}"):
                try:
                    data = json.loads(text)
                    if isinstance(data, dict) and "message" in data:
                        text = str(data["message"]).strip()
                except (ValueError, TypeError):
                    pass
            text = text.strip('"').strip("'").strip("「」").strip("“”").strip()
            text = " ".join(text.split())
            if len(text) > 200:
                text = text[:200]
            if text:
                return text
    return random.choice([
        "我是好人，大家要相信我。",
        "我走了，好人们加油。",
        "我冤枉啊，请大家明察。",
        "我是被狼人陷害的。",
    ])


# ---------- 决策 ----------
def _parse_reply(reply):
    if not reply or not isinstance(reply, str):
        return None
    start, end = reply.find("{"), reply.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(reply[start:end + 1])
    except (ValueError, TypeError):
        return None
    action = data.get("action")
    if not action:
        return None
    return action, data.get("target")


def _resolve_target(room, name):
    if not name:
        return None
    # 兼容 "3号 张三" 格式：取编号后面的名字部分
    if isinstance(name, str) and "号 " in name:
        name = name.split("号 ", 1)[1]
    for pid, nm in room.names.items():
        if nm == name:
            return pid
    return None


def _heuristic(view):
    """无模型或解析失败时的兜底：尽量推进游戏又不乱甩技能。"""
    acts = view.get("available_actions") or []
    me = view["me"]
    others = [p["player_id"] for p in view["players"]
              if p["alive"] and p["player_id"] != me]
    pick = {"kill", "verify", "guard", "poison", "vote", "charm", "gun"}
    for a in acts:
        if a in pick and others:
            return a, random.choice(others)
    return "pass", None


def _ai_kill_target(room, view):
    """问题6/追加：AI 狼人优先采用人类狼人已提交的合法存活刀目标。

    人类狼人先 submit_action(kill, target) 后 room.night["kill_votes"][pid] 即被记录。
    AI 狼人决策时若发现人类狼人集中某目标，直接采用，避免随机刀别人。
    """
    if room.status != "night":
        return None
    human_kills = []
    for pid in room.players:
        if not room.is_ai(pid) and room.alive.get(pid):
            if room.roles.get(pid) in WOLF_ROLES:
                target = room.night.get("kill_votes", {}).get(pid)
                if target and room.alive.get(target):
                    human_kills.append(target)
    if human_kills:
        from collections import Counter
        counts = Counter(human_kills)
        top_count = counts.most_common(1)[0][1]
        top_targets = [t for t, c in counts.items() if c == top_count]
        return random.choice(top_targets)
    return None


def _ai_vote_follow_human_wolf(room, view):
    """Bug 3: 同阵营 AI 狼人跟票人类狼人。

    白天投票时，若人类狼人已投某合法存活目标，AI 狼人直接跟投该目标。
    """
    if room.status != "day" or room.stage is not None:
        return None
    if view.get("my_role") not in WOLF_ROLES:
        return None
    for pid in room.players:
        if not room.is_ai(pid) and room.alive.get(pid):
            if room.roles.get(pid) in WOLF_ROLES:
                target = room.day_votes.get(pid)
                if target and room.alive.get(target):
                    return target
    return None


def _ai_vote_target(room, view):
    """Bug 4: 放逐环节多人类多 AI 的跟票规则。

    1. 统计所有人类已提交投票，按目标分组。
    2. 若存在唯一人类最高票目标，所有未投票 AI 直接投该目标。
    3. 若人类投票平票，AI 只在这些平票的人类目标中随机选一个，不得选无关玩家。
    4. 若没有任何人类投票，返回 None（交由 Bug 3 狼人跟票 / 兜底逻辑）。
    5. 人类自己的票原样保留，不被本函数覆盖（本函数只生成 AI 投票目标）。
    """
    if room.status != "day" or room.stage is not None:
        return None
    from collections import Counter
    human_votes = Counter()
    for pid in room.players:
        if not room.is_ai(pid) and room.alive.get(pid):
            target = room.day_votes.get(pid)
            if target and room.alive.get(target):
                human_votes[target] += 1
    if not human_votes:
        return None
    ranked = human_votes.most_common()
    top_count = ranked[0][1]
    top_targets = [t for t, c in ranked if c == top_count]
    if len(top_targets) == 1:
        return top_targets[0]
    return random.choice(top_targets)


def _decide(room, view, timeout=10):
    acts = view.get("available_actions") or []
    if not acts:
        return "pass", None
    if acts == ["pass"]:
        return "pass", None
    # AI 狼人优先采用人类狼人已提交的刀目标
    if "kill" in acts:
        human_kill = _ai_kill_target(room, view)
        if human_kill is not None:
            return "kill", human_kill
    # 问题3：AI 投票独立决策，不跟票人类玩家，由大模型/启发式自行决定
    prompt = build_prompt(view)
    generator = _get_generator()
    if generator:
        reply = _call_ai_threaded(generator, prompt, timeout)
        if reply:
            parsed = _parse_reply(reply)
            if parsed:
                action, target_name = parsed
                if action in acts:
                    if action == "pass":
                        return "pass", None
                    if action not in TARGET_ACTIONS:
                        return action, None
                    target = _resolve_target(room, target_name)
                    if target is not None and room.alive.get(target):
                        return action, target
    return _heuristic(view)


# ---------- 驱动 ----------
def _token_of(room, pid):
    for token, owner in room.tokens.items():
        if owner == pid:
            return token
    return None


def drive(room, lock):
    """检查并后台驱动房间内需要行动的 AI。调用方无需持有锁。"""
    with lock:
        if room.status in ("waiting", "ended"):
            return
        if room.room_id in _RUNNING:
            return
        _RUNNING.add(room.room_id)
    threading.Thread(target=_drive_loop, args=(room, lock), daemon=True).start()


_VOTE_TEMPLATES = [
    "%s，你真的很可疑啊？",
    "%s，你看起来不太对劲，我要票你。",
    "%s，我关注你很久了，你太有问题了。",
]

_AI_SPEAK_TEMPLATES = [
    "我是好人，请大家相信我。",
    "我有自己的判断，先听听大家怎么说。",
    "昨晚的情况我需要分析一下。",
    "我们要冷静分析，找出狼人。",
    "我暂时没有更多线索，先观望。",
]


def _maybe_vote_chat(room, pid, target, spoken_set):
    """AI 投票后在聊天频道发一条指认发言，每个 AI 每轮只发一次。"""
    key = (room.round, pid)
    if key in spoken_set:
        return
    spoken_set.add(key)
    target_name = room.player_name(target)
    msg = random.choice(_VOTE_TEMPLATES) % target_name
    try:
        room.add_chat(pid, msg)
    except Exception:
        pass


def _drive_loop(room, lock, fast_forward=False):
    import time as _time
    _spoken_vote = set()  # 记录本轮已发指认发言的 AI: (round, pid)
    try:
        for _ in range(400):
            with lock:
                if room.status in ("waiting", "ended"):
                    return
                # Bug 5/6: 服务端控制夜间环节超时推进
                room.check_night_step_timeout()
                # 问题3：白天发言超时自动跳过
                room.check_speak_timeout()
                # 任务2：投票超时自动弃票并结算
                room.check_vote_timeout()
                # 任务2：遗言超时自动结束
                room.check_last_words_timeout()
                # 闪退接管：检测真人玩家是否超时未活动，超时则由 AI 接管
                room.check_absent()
                if room.status in ("waiting", "ended"):
                    return
                ai_needed = []
                for pid in room.players:
                    if not room.is_ai_driven(pid):
                        continue
                    role = room.roles.get(pid)
                    if role is None:
                        continue
                    if not room.alive.get(pid):
                        pending_special = (
                            room.status == "day"
                            and room.stage in ("charm_pending", "gun_pending")
                            and pid in (room.pending_lover, room.pending_gunner)
                        )
                        if not pending_special:
                            continue
                    if room._available_actions(pid, role):
                        token = _token_of(room, pid)
                        if token:
                            ai_needed.append((pid, room.state_for(token)))
            if ai_needed:
                acted = False
                for pid, view in ai_needed:
                    # 先在锁内确定当前阶段和超时
                    with lock:
                        if room.status in ("waiting", "ended"):
                            return
                        is_speak = (room.status == "day" and room.stage == "speak"
                                    and pid == room.speak_current)
                        is_last_words = (room.status == "day" and room.stage == "last_words"
                                         and pid == room.last_words_player)
                        ai_timeout = _get_ai_timeout(room)
                    # AI 调用在锁外执行（线程化，带超时）
                    if is_speak:
                        text = _decide_speak(room, view, ai_timeout)
                        with lock:
                            if room.status in ("waiting", "ended"):
                                return
                            try:
                                room.add_chat(pid, text)
                                acted = True
                            except GameError:
                                try:
                                    room.submit_action(pid, "pass")
                                    acted = True
                                except GameError:
                                    pass
                        continue
                    if is_last_words:
                        text = _decide_last_words(room, view, ai_timeout)
                        with lock:
                            if room.status in ("waiting", "ended"):
                                return
                            try:
                                room.add_chat(pid, text)
                                acted = True
                            except GameError:
                                try:
                                    room.submit_action(pid, "pass")
                                    acted = True
                                except GameError:
                                    pass
                        continue
                    # 普通行动决策
                    action, target = _decide(room, view, ai_timeout)
                    with lock:
                        if room.status in ("waiting", "ended"):
                            return
                        try:
                            room.submit_action(pid, action, target)
                            acted = True
                            if action == "vote" and target is not None:
                                _maybe_vote_chat(room, pid, target, _spoken_vote)
                        except GameError:
                            try:
                                room.submit_action(pid, "pass")
                                acted = True
                            except GameError:
                                pass
                if not acted:
                    if fast_forward:
                        return
                    _time.sleep(0.2)
                    continue
            else:
                # 无 AI 需要行动时，若处于夜间环节等待期
                with lock:
                    in_night = (room.status == "night"
                                and room.night_step_deadline is not None)
                    if in_night:
                        wait = room.night_step_deadline - _time.time()
                        # 全 AI 环节且已行动完：随机拖延 5-20s 后推进
                        step_ai_done = (not room._step_has_human(room.night_step)
                                        and room._step_done(room.night_step))
                    else:
                        wait = 0
                        step_ai_done = False
                if wait > 0:
                    if fast_forward:
                        # 测试环境：AI 已行动完，快进全 AI 环节超时推进
                        with lock:
                            room.night_step_deadline = 0
                            room.check_night_step_timeout()
                        continue
                    if step_ai_done:
                        # 全 AI 环节完成：随机拖延 5-20s（不超过 deadline）再推进
                        delay = random.uniform(5, 20)
                        delay = min(delay, max(0, wait - 1))
                        if delay > 0:
                            _time.sleep(delay)
                        with lock:
                            room.night_step_deadline = 0
                            room.check_night_step_timeout()
                        continue
                    _time.sleep(min(wait, 1.0))
                    continue
                return
    finally:
        with lock:
            _RUNNING.discard(room.room_id)