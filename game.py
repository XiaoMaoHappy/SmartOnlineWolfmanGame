# -*- coding: utf-8 -*-
"""在线狼人杀 —— 核心游戏引擎（纯逻辑，不依赖 Flask，可独立单测）。"""
import random
import time
import uuid
from collections import Counter

WOLF_ROLES = {"werewolf", "wolf_beauty"}
GOD_ROLES = {"seer", "witch", "hunter", "guard"}

# 按人数自动分配角色
ROLE_CONFIGS = {
    6: {"werewolf": 2, "villager": 3, "seer": 1},
    7: {"werewolf": 2, "villager": 3, "seer": 1, "witch": 1},
    8: {"werewolf": 3, "villager": 3, "seer": 1, "witch": 1},
    9: {"werewolf": 2, "wolf_beauty": 1, "villager": 3, "seer": 1, "witch": 1, "hunter": 1},
    10: {"werewolf": 2, "wolf_beauty": 1, "villager": 4, "seer": 1, "witch": 1, "hunter": 1},
    11: {"werewolf": 2, "wolf_beauty": 1, "villager": 4, "seer": 1, "witch": 1, "hunter": 2},
    12: {"werewolf": 2, "wolf_beauty": 1, "villager": 5, "seer": 1, "witch": 1, "hunter": 2},
}

ROLE_NAMES = {
    "werewolf": "狼人",
    "wolf_beauty": "狼美人",
    "seer": "预言家",
    "witch": "女巫",
    "hunter": "猎人",
    "guard": "守卫",
    "villager": "平民",
}

MIN_PLAYERS = 6
MAX_PLAYERS = 12

# Bug 5/6: 夜间固定行动顺序；时长由 Bug 6 规则决定（全 AI 10s，含人类 60s）
NIGHT_STEPS = [
    ("wolf_kill", "狼人刀人"),
    ("beauty_charm", "狼美人魅惑"),
    ("seer_verify", "预言家查验"),
    ("witch", "女巫用药"),
    ("guard", "守卫守护"),
]
NIGHT_STEP_TIMEOUT_AI = 10     # Bug 6: 当前环节需要行动的玩家全部是 AI
NIGHT_STEP_TIMEOUT_HUMAN = 60  # Bug 6: 当前环节包含至少一个人类玩家

# 问题3：白天发言时限（人类/秒），AI 由 bot 驱动，时限主要防止人类卡死
SPEAK_TIMEOUT_HUMAN = 60
SPEAK_TIMEOUT_AI = 30  # 任务1：从15s提高到30s，给大模型更多响应时间

# 任务2：被放逐者遗言时限（秒）
LAST_WORDS_TIMEOUT = 60

# 任务2：投票时限（秒），超时未投票算弃票
VOTE_TIMEOUT = 60

# 问题5：狼人胜利条件
#   default: 屠边 + 屠城 同时判定（默认规则）
#   side:    屠边 —— 神职全灭 或 平民全灭 → 狼胜
#   city:    屠城 —— 狼人数 ≥ 好人数 → 狼胜
WIN_CONDITIONS = {"default", "side", "city"}

# 闪退接管：真人玩家超过该秒数未活动则由 AI 临时接管
ABSENT_TIMEOUT = 30.0

# 任务3：死亡原因简化显示（夜晚死亡/魅惑=淘汰，放逐=被放逐，开枪=被猎人猎杀）
DEAD_REASON_SIMPLE = {
    "wolf": "淘汰", "poison": "淘汰", "vote": "被放逐",
    "charm": "淘汰", "gun": "被猎人猎杀", None: None,
}


class GameError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code
        self.msg = msg


class Room:
    """一个游戏房间。所有方法假定调用方已持有房间锁与全局锁。"""

    def __init__(self, room_id, host_id, host_name):
        self.room_id = room_id
        self.host_id = host_id
        self.status = "waiting"        # waiting / night / day / ended
        self.stage = None              # None / charm_pending / gun_pending
        self.pending_gunner = None     # 待开枪的猎人
        self.pending_lover = None      # 待魅惑的狼美人
        self.pending_voted_out = None  # 白天被放逐（待技能结算）的玩家
        self.round = 0
        self.winner = None
        self.death_reasons = {}        # player_id -> 出局原因
        self.elimination_log = []      # [{player_id, round, reason}] 出局历史
        self.night_action_log = []     # 任务5：每回合夜间操作记录
        self.vote_log = []             # 任务5：每回合投票记录
        self.chat = []
        self.created_at = int(time.time())

        self.players = []              # 有序 player_id
        self.names = {}                # player_id -> name
        self.tokens = {}               # token -> player_id
        self.roles = {}                # player_id -> role
        self.alive = {}                # player_id -> bool

        self.ai_players = set()        # AI 玩家 player_id
        self.ai_count = 0              # 本房间配置的 AI 玩家数量

        self.night = {
            "guard": None,             # 守卫守护目标
            "kill": None,              # 狼刀目标
            "kill_votes": {},          # 问题6：每只狼的刀目标 {pid: target}
            "verify": None,            # 预言家查验目标
            "save": False,             # 女巫是否本晚使用解药
            "poison": None,            # 女巫毒药目标
            "witch_save_used": False,
            "witch_poison_used": False,
            "charm_target": None,      # 问题5：狼美人夜晚指定的魅惑目标
            "done": {},                # 问题5：{step_key: set(player_ids)} 按环节标记已行动
            "verify_history": [],      # [{round, target, result, target_name}]
        }
        self.night_deaths = []         # 当晚死亡 player_id
        self.day_votes = {}            # voter -> target (None 表示弃权)

        self.version = 0               # 状态版本号，每次变更 +1，用于 SSE 推送
        self.on_change = None          # 变更回调，由服务端注入，签名: () -> None
        self.pending_human_input = False  # 当前阶段是否有存活人类待行动，用于阻塞 AI 推进
        self.night_step = 0            # Bug 5: 当前夜间环节索引
        self.night_step_name = None    # 当前夜间环节名称（用于前端展示）
        self.night_step_deadline = None  # 当前环节截止时间戳，超时自动推进
        self.speak_queue = []            # 问题1：待发言玩家列表（按座位号顺序）
        self.speak_current = None        # 问题1：当前发言者 player_id
        self.speak_deadline = None       # 问题3：当前发言截止时间戳，超时自动跳过
        self.vote_deadline = None        # 任务2：投票截止时间戳，超时未投票算弃票
        self.last_words_player = None    # 任务2：待发表遗言的玩家
        self.last_words_deadline = None  # 任务2：遗言截止时间戳

        # 问题2：上一轮投票结算后的票型 {voter_name: target_name}，投票进行中不展示
        self.last_vote_result = None

        # 问题5：房主可自定义的游戏设置
        self.role_config = None          # 自定义角色配置 {role: count}，None 表示按人数默认
        self.win_condition = "default"   # 狼人胜利条件: default / side / city

        # 编号1-12：每局开始时打乱，发言/UI/AI 提示词均按编号顺序；房主权限不变
        self.seats = {}                  # {player_id: seat_number(1..n)}

        # 闪退接管：真人玩家断线超时由 AI 临时接管，返回后恢复玩家控制
        self.ai_controlled = set()       # 被 AI 接管的真人玩家 player_id
        self.last_seen = {}              # {player_id: timestamp} 真人玩家最后在线时间

    def _changed(self):
        """状态变更通知：递增版本号并触发服务端回调（SSE 广播）。"""
        self.version += 1
        if self.on_change:
            try:
                self.on_change()
            except Exception:
                pass

    # ---------- 房间 / 玩家 ----------
    def add_player(self, player_id, name):
        self.players.append(player_id)
        self.names[player_id] = name
        self.alive[player_id] = True
        token = uuid.uuid4().hex
        self.tokens[token] = player_id
        return token

    def add_ai_player(self, name):
        """注册一名 AI 玩家，返回 (player_id, token)。"""
        pid = "ai_" + uuid.uuid4().hex[:8]
        token = self.add_player(pid, name)
        self.ai_players.add(pid)
        self.ai_count += 1
        return pid, token

    def is_ai(self, pid):
        return pid in self.ai_players

    def is_ai_driven(self, pid):
        """该玩家是否由 AI 驱动：原生 AI 或闪退被接管的真人。"""
        return pid in self.ai_players or pid in self.ai_controlled

    def seat_of(self, pid):
        """返回玩家编号（1..n），未分配时为 None。"""
        return self.seats.get(pid)

    def _display_name(self, pid):
        """带编号的显示名，如「3号 张三」；未分配编号时只返回名字。"""
        seat = self.seats.get(pid)
        if seat:
            return "%d号 %s" % (seat, self.player_name(pid))
        return self.player_name(pid)

    def mark_seen(self, pid):
        """玩家在线活动时调用：更新最后在线时间，并恢复其玩家控制（移除 AI 接管）。"""
        if pid in self.players and not self.is_ai(pid):
            self.last_seen[pid] = time.time()
            if pid in self.ai_controlled:
                self.ai_controlled.discard(pid)
                self._changed()

    def check_absent(self, timeout=None):
        """检查真人玩家是否超时未活动，超时则由 AI 接管。返回是否有新接管。"""
        if self.status in ("waiting", "ended"):
            return False
        if timeout is None:
            timeout = ABSENT_TIMEOUT
        now = time.time()
        changed = False
        for pid in list(self.players):
            if self.is_ai(pid) or not self.alive.get(pid):
                continue
            last = self.last_seen.get(pid)
            if last is None or now - last > timeout:
                if pid not in self.ai_controlled:
                    self.ai_controlled.add(pid)
                    changed = True
        if changed:
            self._changed()
        return changed

    def player_name(self, pid):
        return self.names.get(pid, pid)

    def token_owner(self, token):
        return self.tokens.get(token)

    def remove_player(self, pid):
        """问题4：将玩家从房间除名。
        若房主退出，则把房主移交给随机一位真人玩家；若无真人玩家则标记关闭。
        返回 {"removed": pid, "new_host": new_host_id 或 None, "closed": bool}。
        """
        if pid not in self.players:
            raise GameError(400, "该玩家不在房间中")
        is_host = (pid == self.host_id)
        for t in [t for t, o in self.tokens.items() if o == pid]:
            del self.tokens[t]
        self.players = [p for p in self.players if p != pid]
        self.names.pop(pid, None)
        self.roles.pop(pid, None)
        self.alive.pop(pid, None)
        self.death_reasons.pop(pid, None)
        if pid in self.ai_players:
            self.ai_players.discard(pid)
            self.ai_count = max(0, self.ai_count - 1)
        self.ai_controlled.discard(pid)
        self.last_seen.pop(pid, None)
        self.seats.pop(pid, None)
        self.day_votes.pop(pid, None)
        for s in self.night.get("done", {}).values():
            if isinstance(s, set):
                s.discard(pid)
        self.night.get("kill_votes", {}).pop(pid, None)
        self.speak_queue = [p for p in self.speak_queue if p != pid]
        if self.speak_current == pid:
            self.speak_current = self.speak_queue[0] if self.speak_queue else None
            if not self.speak_current:
                self.stage = None
                self.speak_deadline = None
            else:
                self._set_speak_deadline()
        if self.last_words_player == pid:
            self.last_words_player = None
            self.last_words_deadline = None
        new_host = None
        closed = False
        if is_host:
            human_players = [p for p in self.players if not self.is_ai(p)]
            if human_players:
                new_host = random.choice(human_players)
                self.host_id = new_host
            else:
                closed = True
        self._changed()
        return {"removed": pid, "new_host": new_host, "closed": closed}

    def set_config(self, ai_count=None, role_config=None, win_condition=None):
        """问题5：房主在等候室修改游戏设置（AI 数量 / 角色配置 / 胜利条件）。"""
        if self.status != "waiting":
            raise GameError(403, "游戏已开始，无法修改设置")
        if ai_count is not None:
            try:
                ai_count = int(ai_count)
            except (TypeError, ValueError):
                raise GameError(400, "ai_count 必须是数字")
            if ai_count < 0 or ai_count > MAX_PLAYERS - 1:
                raise GameError(400, "AI 玩家数量应在 0 ~ %d 之间" % (MAX_PLAYERS - 1))
            current_ai = [p for p in self.players if self.is_ai(p)]
            if ai_count > len(current_ai):
                for i in range(ai_count - len(current_ai)):
                    self.add_ai_player("AI玩家%d" % (len(current_ai) + i + 1))
            elif ai_count < len(current_ai):
                for p in current_ai[ai_count:]:
                    self.remove_player(p)
        if role_config is not None:
            valid_roles = set(ROLE_NAMES.keys())
            cfg = {}
            for r, c in role_config.items():
                if r not in valid_roles:
                    raise GameError(400, "未知角色: %s" % r)
                try:
                    c = int(c)
                except (TypeError, ValueError):
                    raise GameError(400, "角色数量必须为整数")
                if c < 0:
                    raise GameError(400, "角色数量必须为非负整数")
                if c > 0:
                    cfg[r] = c
            self.role_config = cfg or None
        if win_condition is not None:
            if win_condition not in WIN_CONDITIONS:
                raise GameError(400, "胜利条件应为 default/side/city")
            self.win_condition = win_condition
        self._changed()

    def reset_night(self):
        """把夜晚行动数据恢复为初始状态。"""
        self.night = {
            "guard": None,
            "kill": None,
            "kill_votes": {},
            "verify": None,
            "save": False,
            "poison": None,
            "witch_save_used": False,
            "witch_poison_used": False,
            "charm_target": None,
            "done": {},
            "verify_history": [],
            "kill_finalized": False,
        }

    # ---------- 发牌 / 回合 ----------
    def start(self):
        if self.status != "waiting":
            raise GameError(403, "游戏已开始")
        n = len(self.players)
        if n < MIN_PLAYERS:
            raise GameError(400, "至少需要 %d 名玩家才能开始" % MIN_PLAYERS)
        if n > MAX_PLAYERS:
            raise GameError(400, "最多支持 %d 名玩家" % MAX_PLAYERS)
        if self.ai_count >= n:
            raise GameError(400, "AI 玩家数量不能占满全场")
        # 问题5：优先使用房主自定义角色配置，否则按人数默认
        config = self.role_config or ROLE_CONFIGS.get(n)
        if config is None:
            raise GameError(400, "暂不支持 %d 人局" % n)
        total = sum(config.values())
        if total != n:
            raise GameError(400, "角色总数(%d)与玩家数(%d)不匹配" % (total, n))
        deck = []
        for role, count in config.items():
            deck.extend([role] * count)
        # 问题7：优先给人类玩家分配有身份角色（非平民）
        human_players = [p for p in self.players if not self.is_ai(p)]
        ai_players_list = [p for p in self.players if self.is_ai(p)]
        role_deck = [r for r in deck if r != "villager"]
        villager_deck = [r for r in deck if r == "villager"]
        random.shuffle(role_deck)
        random.shuffle(villager_deck)
        self.roles = {}
        for p in human_players:
            if role_deck:
                self.roles[p] = role_deck.pop()
            else:
                self.roles[p] = villager_deck.pop()
        remaining = role_deck + villager_deck
        random.shuffle(remaining)
        for p in ai_players_list:
            self.roles[p] = remaining.pop()
        # 编号1-12：每局开始时打乱编号，发言/UI/AI 提示词均按编号顺序；房主权限不变
        shuffled = list(self.players)
        random.shuffle(shuffled)
        self.seats = {pid: i + 1 for i, pid in enumerate(shuffled)}
        # 初始化真人玩家在线时间，避免开局即被判定为闪退接管
        _now = time.time()
        for pid in self.players:
            if not self.is_ai(pid):
                self.last_seen[pid] = _now
        self.ai_controlled = set()
        self._enter_night()
        self._changed()

    def restart(self):
        """结束后重开一局：保留房间、玩家名单与 AI 配置，清空局内状态。"""
        if self.status != "ended":
            raise GameError(403, "对局尚未结束，不能重开")
        self.status = "waiting"
        self.stage = None
        self.pending_gunner = None
        self.pending_lover = None
        self.pending_voted_out = None
        self.round = 0
        self.winner = None
        self.death_reasons = {}
        self.elimination_log = []
        self.chat = []
        self.roles = {}
        self.night_deaths = []
        self.day_votes = {}
        self.speak_queue = []
        self.speak_current = None
        self.speak_deadline = None
        self.vote_deadline = None
        self.last_words_player = None
        self.last_words_deadline = None
        self.last_vote_result = None
        self.night_action_log = []
        self.vote_log = []
        self.seats = {}
        self.ai_controlled = set()
        self.last_seen = {}
        for pid in self.players:
            self.alive[pid] = True
        self.reset_night()
        self._changed()

    def _enter_night(self):
        self.status = "night"
        self.stage = None
        self.round += 1
        if self.round > 1:
            self._add_system_chat("第%d回合开始" % self.round)
        self.night_deaths = []
        self.day_votes = {}
        n = self.night
        n["guard"] = None
        n["kill"] = None
        n["kill_votes"] = {}
        n["verify"] = None
        n["save"] = False
        n["poison"] = None
        n["charm_target"] = None
        n["done"] = {}
        n["kill_finalized"] = False
        for step_idx, (step_key, _) in enumerate(NIGHT_STEPS):
            roles = self._step_role_set(step_idx)
            for pid in self.players:
                if self.alive.get(pid) and self.roles.get(pid) in roles:
                    if not self._needs_night_action(pid):
                        n["done"].setdefault(step_key, set()).add(pid)
        # Bug 5: 进入夜间第一个环节
        self.night_step = 0
        self.night_step_name = None
        self.night_step_deadline = None
        self._enter_night_step(0)

    def _enter_night_step(self, i):
        """推进到第 i 个夜间环节；跳过无效/已完成环节，全部结束则结算夜晚。"""
        if self.status != "night":
            return
        # 狼人环节刚完成，确定最终刀口（优先真人狼人，真人平票随机）
        if i > 0 and NIGHT_STEPS[i - 1][0] == "wolf_kill" and not self.night.get("kill_finalized"):
            self.night["kill"] = self._resolve_wolf_kill()
            self.night["kill_finalized"] = True
        if i >= len(NIGHT_STEPS):
            self.resolve_night()
            return
        self.night_step = i
        self.night_step_name = NIGHT_STEPS[i][1]

        # 环节无效（角色不存在/已死亡）或已完成 → 跳过，不占时
        if not self._step_needs_action(i) or self._step_done(i):
            self._enter_night_step(i + 1)
            return
        # Bug 6: 全 AI 环节 10s，含人类环节 60s
        timeout = NIGHT_STEP_TIMEOUT_HUMAN if self._step_has_human(i) else NIGHT_STEP_TIMEOUT_AI
        self.night_step_deadline = time.time() + timeout

    def _step_has_human(self, i):
        """Bug 6: 该环节是否存在存活人类玩家需要行动。"""
        if i >= len(NIGHT_STEPS):
            return False
        key = NIGHT_STEPS[i][0]

        roles = self._step_role_set(i)
        for pid in self.players:
            if (not self.is_ai_driven(pid) and self.alive.get(pid)
                    and self.roles.get(pid) in roles):
                if key == "witch" and not self._needs_night_action(pid):
                    continue
                return True
        return False

    def _step_role_set_by_key(self, key):
        if key == "wolf_kill":
            return WOLF_ROLES
        if key == "beauty_charm":
            return {"wolf_beauty"}
        if key == "seer_verify":
            return {"seer"}
        if key == "witch":
            return {"witch"}
        if key == "guard":
            return {"guard"}
        return set()

    def _step_role_set(self, i):
        key = NIGHT_STEPS[i][0] if i < len(NIGHT_STEPS) else ""
        return self._step_role_set_by_key(key)

    def _step_needs_action(self, i):
        """该环节是否存在存活角色需要行动。"""
        if i >= len(NIGHT_STEPS):
            return False
        key = NIGHT_STEPS[i][0]
        roles = self._step_role_set(i)
        for pid in self.players:
            if self.alive.get(pid) and self.roles.get(pid) in roles:
                if key == "witch" and not self._needs_night_action(pid):
                    continue
                return True
        return False

    def _step_done(self, i):
        """该环节所有需要行动的角色是否都已行动。"""
        if i >= len(NIGHT_STEPS):
            return True
        key = NIGHT_STEPS[i][0]
        roles = self._step_role_set(i)
        done_set = self.night["done"].get(key, set())
        for pid in self.players:
            if self.alive.get(pid) and self.roles.get(pid) in roles:
                if key == "witch" and not self._needs_night_action(pid):
                    continue
                if pid not in done_set:
                    return False
        return True
        key = NIGHT_STEPS[i][0]

        roles = self._step_role_set(i)
        for pid in self.players:
            if self.alive.get(pid) and self.roles.get(pid) in roles:
                if key == "witch" and not self._needs_night_action(pid):
                    continue
                if pid not in self.night["done"]:
                    return False
        return True

    def _force_step_pass(self, i):
        """超时强制放弃当前环节：把未行动的存活角色标记为已行动。"""
        if i >= len(NIGHT_STEPS):
            return
        key = NIGHT_STEPS[i][0]
        roles = self._step_role_set(i)
        for pid in self.players:
            if self.alive.get(pid) and self.roles.get(pid) in roles:
                self.night["done"].setdefault(key, set()).add(pid)

    def check_night_step_timeout(self):
        """Bug 5: 检查当前夜间环节是否超时，超时视为放弃并自动推进。返回是否推进。"""
        if self.status != "night":
            return False
        if self.night_step_deadline is None:
            return False
        if time.time() < self.night_step_deadline:
            return False
        self._force_step_pass(self.night_step)
        self._enter_night_step(self.night_step + 1)
        self._changed()
        return True

    def _enter_day(self):
        self.status = "day"
        self.day_votes = {}
        self.last_vote_result = None  # 问题2：新一轮清空上轮票型
        # 问题1：按座位号顺序依次发言，全部发言完进入投票阶段
        alive_players = [p for p in self.players if self.alive.get(p)]
        alive_players.sort(key=lambda p: self.seats.get(p, 0))
        self.speak_queue = alive_players
        if self.speak_queue:
            self.stage = "speak"
            self.speak_current = self.speak_queue[0]
            self._set_speak_deadline()
        else:
            self._enter_vote()

    def _set_speak_deadline(self):
        """问题3：为当前发言者设定截止时间，超时自动跳过。"""
        if self.speak_current is None:
            self.speak_deadline = None
            return
        timeout = SPEAK_TIMEOUT_AI if self.is_ai_driven(self.speak_current) else SPEAK_TIMEOUT_HUMAN
        self.speak_deadline = time.time() + timeout

    def _enter_vote(self):
        """任务2：进入投票阶段，设置投票时限。"""
        self.stage = None
        self.speak_current = None
        self.speak_deadline = None
        self.vote_deadline = time.time() + VOTE_TIMEOUT

    def check_vote_timeout(self):
        """任务2：投票超时，未投票存活玩家弃权并尝试结算。返回是否推进。"""
        if self.status != "day" or self.stage is not None:
            return False
        if self.vote_deadline is None:
            return False
        if time.time() < self.vote_deadline:
            return False
        for pid in self.players:
            if self.alive.get(pid) and pid not in self.day_votes:
                self.day_votes[pid] = None
        self._maybe_resolve_day()
        self._changed()
        return True

    def check_speak_timeout(self):
        """问题3：检查当前发言是否超时，超时自动跳过该发言者。返回是否推进。"""
        if self.status != "day" or self.stage != "speak":
            return False
        if self.speak_deadline is None:
            return False
        if time.time() < self.speak_deadline:
            return False
        self._advance_speak()
        self._changed()
        return True

    def _enter_last_words(self, pid):
        """任务2：被放逐者进入遗言阶段。"""
        self.stage = "last_words"
        self.last_words_player = pid
        self.last_words_deadline = time.time() + LAST_WORDS_TIMEOUT
        self.pending_human_input = self._has_pending_human_action()

    def _finish_last_words(self):
        """任务2：遗言结束，进入技能结算（狼美人魅惑/猎人开枪/直接出局）。"""
        voted_out = self.last_words_player
        self.last_words_player = None
        self.last_words_deadline = None
        if voted_out is None or voted_out not in self.players:
            return
        role = self.roles.get(voted_out)
        if role == "wolf_beauty":
            charm_target = self.night.get("charm_target")
            if charm_target and self.alive.get(charm_target):
                self._kill_player(charm_target, "charm")
            self._kill_player(voted_out, "vote")
            self._finish_elimination(voted_out)
            return
        if role == "hunter":
            self.stage = "gun_pending"
            self.pending_gunner = voted_out
            self.pending_human_input = self._has_pending_human_action()
            self._changed()
            return
        self._kill_player(voted_out, "vote")
        self._finish_elimination(voted_out)

    def check_last_words_timeout(self):
        """任务2：遗言超时自动结束。返回是否推进。"""
        if self.status != "day" or self.stage != "last_words":
            return False
        if self.last_words_deadline is None:
            return False
        if time.time() < self.last_words_deadline:
            return False
        self._finish_last_words()
        self._changed()
        return True

    # ---------- 行动 ----------
    def submit_action(self, player_id, action_type, target=None):
        result = self._do_submit_action(player_id, action_type, target)
        self._changed()
        return result

    def _do_submit_action(self, player_id, action_type, target=None):
        if self.status == "ended":
            raise GameError(403, "游戏已结束")
        if self.roles.get(player_id) is None:
            raise GameError(403, "游戏尚未开始")
        # 已出局的猎人/狼美人被允许提交被动技能（开枪 / 魅惑 / 弃权）
        special = (
            self.status == "day"
            and self.stage in ("charm_pending", "gun_pending")
            and action_type in ("charm", "gun", "pass")
            and player_id in (self.pending_lover, self.pending_gunner)
        )
        if not special and not self.alive.get(player_id):
            raise GameError(403, "你已出局，无法行动")

        if action_type == "pass":
            self._action_pass(player_id)
            return {"skip": True}
        if action_type == "save":
            return self._action_save(player_id)
        target = self._check_target(target)

        if action_type == "guard":
            return self._action_guard(player_id, target)
        if action_type == "kill":
            return self._action_kill(player_id, target)
        if action_type == "verify":
            return self._action_verify(player_id, target)
        if action_type == "save":
            return self._action_save(player_id)
        if action_type == "poison":
            return self._action_poison(player_id, target)
        if action_type == "vote":
            return self._action_vote(player_id, target)
        if action_type == "charm":
            self._action_charm(player_id, target)
            return {"charm": target}
        if action_type == "gun":
            self._action_gun(player_id, target)
            return {"gun": target}
        raise GameError(400, "未知行动类型: %s" % action_type)

    def _check_target(self, target):
        if not target or target not in self.players or not self.alive.get(target):
            raise GameError(400, "目标玩家不存在或已出局")
        return target

    def _action_guard(self, pid, target):
        if self.status != "night" or self.roles[pid] != "guard":
            raise GameError(403, "只有夜晚的守卫可以此行动")
        self.night["guard"] = target
        self.night["done"].setdefault("guard", set()).add(pid)
        self._maybe_resolve_night()
        return {"guard": target}

    def _action_kill(self, pid, target):
        if self.status != "night" or self.roles[pid] not in WOLF_ROLES:
            raise GameError(403, "只有夜晚的狼人可以此行动")
        self.night["kill"] = target
        self.night["kill_votes"][pid] = target  # 问题6：记录每只狼的刀目标
        # 每只狼各自确认刀向，全部确认后才自动结算
        self.night["done"].setdefault("wolf_kill", set()).add(pid)
        self._maybe_resolve_night()
        return {"kill": target}

    def _action_verify(self, pid, target):
        if self.status != "night" or self.roles[pid] != "seer":
            raise GameError(403, "只有夜晚的预言家可以此行动")
        result = "werewolf" if self.roles[target] in WOLF_ROLES else "good"
        self.night["verify"] = target
        self.night["verify_history"].append({
            "round": self.round,
            "target": target,
            "target_name": self._display_name(target),
            "result": result,
        })
        self.night["done"].setdefault("seer_verify", set()).add(pid)
        self._maybe_resolve_night()
        return {"verify": target, "result": result}

    def _action_save(self, pid):
        if self.status != "night" or self.roles[pid] != "witch":
            raise GameError(403, "只有夜晚的女巫可以此行动")
        if self.night["witch_save_used"]:
            raise GameError(403, "解药已使用")
        if self.night["poison"] is not None:
            raise GameError(403, "本晚已使用毒药，不可再使用解药")
        self.night["save"] = True
        self.night["witch_save_used"] = True
        self._maybe_resolve_night()
        return {"save": True}

    def _action_poison(self, pid, target):
        if self.status != "night" or self.roles[pid] != "witch":
            raise GameError(403, "只有夜晚的女巫可以此行动")
        if self.night["witch_poison_used"]:
            raise GameError(403, "毒药已使用")
        if self.night["save"]:
            raise GameError(403, "本晚已使用解药，不可再使用毒药")
        self.night["poison"] = target
        self.night["witch_poison_used"] = True
        self._maybe_resolve_night()
        return {"poison": target}

    def _action_vote(self, pid, target):
        if self.status != "day" or self.stage is not None:
            raise GameError(403, "当前不是投票阶段")
        self.day_votes[pid] = target
        self._maybe_resolve_day()
        return {"vote": target}

    def _action_charm(self, pid, target):
        # 问题5：夜晚提前指定魅惑目标
        if self.status == "night" and self.roles.get(pid) == "wolf_beauty":
            self.night["charm_target"] = target
            self.night["done"].setdefault("beauty_charm", set()).add(pid)
            self.night["done"].setdefault("wolf_kill", set()).add(pid)
            self._maybe_resolve_night()
            return
        if self.status != "day" or self.stage != "charm_pending" or pid != self.pending_lover:
            raise GameError(403, "当前没有可触发的魅惑")
        if target:
            self._kill_player(target, "charm")      # 被魅惑者先出局，猎人不可开枪
        self._kill_player(pid, "vote")              # 狼美人被放逐
        self._finish_elimination(pid)

    def _action_gun(self, pid, target):
        if self.status != "day" or self.stage != "gun_pending" or pid != self.pending_gunner:
            raise GameError(403, "当前没有可触发的开枪")
        if target:
            self._kill_player(target, "gun")
        reason = "vote" if self.pending_voted_out == pid else "wolf"
        self._kill_player(pid, reason)
        self._finish_elimination(pid)

    def _action_pass(self, pid):
        if self.status == "night":
            # pass 跳过该玩家在所有需行动环节的行动
            role = self.roles.get(pid)
            for step_key, _ in NIGHT_STEPS:
                step_roles = self._step_role_set_by_key(step_key)
                if role in step_roles:
                    self.night["done"].setdefault(step_key, set()).add(pid)
            self._maybe_resolve_night()
            return
        if self.status == "day":
            if self.stage == "speak" and pid == self.speak_current:
                self._advance_speak()
                return
            if self.stage == "last_words" and pid == self.last_words_player:
                self._finish_last_words()
                return
            if self.stage == "charm_pending" and pid == self.pending_lover:
                self._kill_player(pid, "vote")
                self._finish_elimination(pid)
                return
            if self.stage == "gun_pending" and pid == self.pending_gunner:
                reason = "vote" if self.pending_voted_out == pid else "wolf"
                self._kill_player(pid, reason)
                self._finish_elimination(pid)
                return
            if self.stage is None:
                self.day_votes[pid] = None
                self._maybe_resolve_day()
                return
        raise GameError(403, "当前阶段没有可跳过的行动")

    # ---------- 夜晚结算 ----------
    def _needs_night_action(self, pid):
        role = self.roles.get(pid)
        if not self.alive.get(pid):
            return False
        if role in ("guard", "seer") or role in WOLF_ROLES:
            return True
        if role == "witch":
            return (not self.night["witch_save_used"]) or (not self.night["witch_poison_used"])
        return False

    def _has_pending_human_action(self):
        """当前阶段是否存在存活人类玩家尚未行动。用于阻塞 AI 自动推进结算。

        闪退被 AI 接管的真人（ai_controlled）视为 AI，不阻塞推进。
        """
        if self.status == "night":
            # Bug 6: 只检查当前夜间环节是否有人类待行动
            if self.night_step is None or self.night_step >= len(NIGHT_STEPS):
                return False
            roles = self._step_role_set(self.night_step)
            key = NIGHT_STEPS[self.night_step][0]
            done_set = self.night["done"].get(key, set())
            for pid in self.players:
                if (not self.is_ai_driven(pid) and self.alive.get(pid)
                        and self.roles.get(pid) in roles):
                    if key == "witch" and not self._needs_night_action(pid):
                        continue
                    if pid not in done_set:
                        return True
            return False
        if self.status == "day":
            if self.stage == "speak" and self.speak_current is not None:
                return not self.is_ai_driven(self.speak_current)
            if self.stage == "last_words" and self.last_words_player is not None:
                return not self.is_ai_driven(self.last_words_player)
            if self.stage is None:
                for pid in self.players:
                    if (not self.is_ai_driven(pid) and self.alive.get(pid)
                            and pid not in self.day_votes):
                        return True
                return False
            if self.stage == "charm_pending" and self.pending_lover is not None:
                return not self.is_ai_driven(self.pending_lover)
            if self.stage == "gun_pending" and self.pending_gunner is not None:
                return not self.is_ai_driven(self.pending_gunner)
        return False

    def _maybe_resolve_night(self):
        if self.status != "night":
            return
        if self._has_pending_human_action():
            self.pending_human_input = True
            return
        self.pending_human_input = False
        # Bug 6: 当前环节完成后推进规则
        if not self._step_done(self.night_step):
            return  # 环节未完成，等待
        # 含人类环节：人类已行动完，立即推进（不等 deadline）
        if self._step_has_human(self.night_step):
            self._enter_night_step(self.night_step + 1)
            return
        # 全 AI 环节：即使 AI 已行动完，也保留等待界面，等 deadline 到再推进

    def _resolve_wolf_kill(self):
        """问题6/追加：从所有狼的刀目标中选一个作为最终刀口。

        优先级：
        1. 人类狼人最高票 >= AI狼人最高票 → 选人类狼人最高票目标（平票优先人类）
        2. 否则选 AI 狼人最高票目标
        3. 无投票 → 返回 night["kill"]
        """
        votes = self.night.get("kill_votes", {})
        human_counter = Counter()
        ai_counter = Counter()
        for pid, target in votes.items():
            if not target or not self.alive.get(target):
                continue
            if self.is_ai(pid):
                ai_counter[target] += 1
            else:
                human_counter[target] += 1
        human_top_count = human_counter.most_common(1)[0][1] if human_counter else 0
        ai_top_count = ai_counter.most_common(1)[0][1] if ai_counter else 0
        if human_top_count >= ai_top_count and human_counter:
            top_targets = [t for t, c in human_counter.items() if c == human_top_count]
            return random.choice(top_targets)
        if ai_counter:
            top_targets = [t for t, c in ai_counter.items() if c == ai_top_count]
            return random.choice(top_targets)
        return self.night["kill"]

    def resolve_night(self):
        """夜晚结算：全部行动后自动触发，或房主手动触发。"""
        if self.status != "night":
            raise GameError(403, "当前不是夜晚阶段")
        n = self.night
        poison = n["poison"]
        # 狼刀最终刀口：若尚未确定则现在确定
        if not n.get("kill_finalized"):
            n["kill"] = self._resolve_wolf_kill()
            n["kill_finalized"] = True
        kill = n["kill"]
        deaths = []
        if kill and n["guard"] != kill and not n["save"]:
            deaths.append(kill)
        if poison:
            deaths.append(poison)
        deaths = list(dict.fromkeys(deaths))
        self.night_deaths = deaths
        # 任务5：记录夜间操作历史
        self.night_action_log.append({
            "round": self.round,
            "kill_votes": {self._display_name(pid): self._display_name(t) for pid, t in n.get("kill_votes", {}).items() if t},
            "verify": [{"target": h["target_name"], "result": h["result"]} for h in n.get("verify_history", []) if h["round"] == self.round],
            "poison": self._display_name(poison) if poison else None,
            "save": n["save"],
            "guard": self._display_name(n["guard"]) if n["guard"] else None,
            "charm": self._display_name(n.get("charm_target")) if n.get("charm_target") else None,
            "deaths": [self._display_name(p) for p in deaths],
        })
        for pid in deaths:
            reason = "poison" if pid == poison and pid != kill else "wolf"
            self._kill_player(pid, reason)
        winner = self.check_winner()
        if winner:
            self.status = "ended"
            self.winner = winner
            self._changed()
            return {"deaths": deaths, "winner": winner}
        # 仅被狼刀死的猎人可触发开枪子阶段（毒死等不可）
        hunters = [
            p for p in deaths
            if self.roles.get(p) == "hunter" and self.death_reasons.get(p) == "wolf"
        ]
        if hunters:
            self._enter_day_gun(hunters[0])
        else:
            self._enter_day()
        self._changed()
        return {"deaths": deaths, "phase": self.status, "stage": self.stage}

    def _kill_player(self, pid, reason):
        if self.alive.get(pid):
            self.alive[pid] = False
            self.death_reasons[pid] = reason
            self.elimination_log.append({
                "player_id": pid,
                "round": self.round,
                "reason": reason,
            })

    def _enter_day_gun(self, gunner_id):
        """夜晚被刀死的猎人：先进入开枪子阶段，再进入正常白天。"""
        self.status = "day"
        self.stage = "gun_pending"
        self.day_votes = {}
        self.pending_gunner = gunner_id
        self.pending_voted_out = None
        self.pending_human_input = self._has_pending_human_action()

    # ---------- 白天结算 ----------
    def _maybe_resolve_day(self):
        if self.status != "day" or self.stage is not None:
            return
        if self._has_pending_human_action():
            self.pending_human_input = True
            return
        self.pending_human_input = False
        for pid in self.players:
            if self.alive.get(pid) and pid not in self.day_votes:
                return
        self.resolve_day()

    def resolve_day(self):
        """白天投票结算：全部投完自动触发，或房主手动触发。"""
        if self.status != "day":
            raise GameError(403, "当前不是白天阶段")
        # 问题1：房主强制跳过发言阶段，直接进入投票
        if self.stage == "speak":
            self.speak_queue = []
            self._enter_vote()
            self._changed()
            return {"forced": "speak_skip"}
        # 房主强制跳过猎人开枪 / 狼美人魅惑子阶段，避免卡死
        if self.stage == "gun_pending" and self.pending_gunner is not None:
            self._action_pass(self.pending_gunner)
            self._changed()
            return {"forced": "gun_pass"}
        if self.stage == "charm_pending" and self.pending_lover is not None:
            self._action_pass(self.pending_lover)
            self._changed()
            return {"forced": "charm_pass"}
        if self.stage == "last_words" and self.last_words_player is not None:
            self._action_pass(self.last_words_player)
            self._changed()
            return {"forced": "last_words_pass"}
        if self.stage is not None:
            raise GameError(403, "当前不是投票阶段")
        # 问题2：投票结算时记录票型，投票进行中不展示，结算后公开
        self.last_vote_result = {
            self._display_name(v): (self._display_name(t) if t else "弃权")
            for v, t in self.day_votes.items()
        }
        counts = Counter()
        for pid, target in self.day_votes.items():
            if target and self.alive.get(target):
                counts[target] += 1
        voted_out = None
        if counts:
            ranked = counts.most_common()
            if len(ranked) == 1 or ranked[0][1] > ranked[1][1]:
                voted_out = ranked[0][0]
            else:
                # 追加：人类狼集中 vs AI狼集中票数相同时优先人类狼目标
                top_count = ranked[0][1]
                top_targets = [t for t, c in counts.items() if c == top_count]
                if len(top_targets) >= 2:
                    human_wolf_votes = Counter()
                    ai_wolf_votes = Counter()
                    for pid in self.players:
                        if self.alive.get(pid) and self.roles.get(pid) in WOLF_ROLES:
                            t = self.day_votes.get(pid)
                            if t and self.alive.get(t) and t in top_targets:
                                if self.is_ai(pid):
                                    ai_wolf_votes[t] += 1
                                else:
                                    human_wolf_votes[t] += 1
                    if (len(human_wolf_votes) == 1 and len(ai_wolf_votes) == 1):
                        human_target = list(human_wolf_votes.keys())[0]
                        ai_target = list(ai_wolf_votes.keys())[0]
                        if human_target != ai_target:
                            voted_out = human_target
        # 任务5：记录投票历史
        self.vote_log.append({
            "round": self.round,
            "votes": dict(self.last_vote_result),
            "voted_out": self._display_name(voted_out) if voted_out else None,
        })
        if voted_out is None:
            winner = self.check_winner()
            if winner:
                self.status = "ended"
                self.winner = winner
                self._changed()
                return {"winner": winner}
            self._enter_night()
            self._changed()
            return {"next": "night", "round": self.round}

        self.pending_voted_out = voted_out
        # 任务2：被放逐者先发表遗言，遗言结束后再进入技能结算
        self._enter_last_words(voted_out)
        self._changed()
        return {"voted_out": voted_out, "stage": "last_words"}

    def _finish_elimination(self, voted_out):
        """放逐 / 技能连锁出局完毕，清理子阶段并进入胜负判定。

        推进目标取决于来源：
        - pending_voted_out is None → 猎人夜晚被狼杀后开枪完毕，进入白天投票
        - pending_voted_out is not None → 白天放逐结算完毕，进入下一夜
        """
        self.stage = None
        self.pending_gunner = None
        self.pending_lover = None
        from_night = self.pending_voted_out is None
        self.pending_voted_out = None
        self.day_votes = {}
        winner = self.check_winner()
        if winner:
            self.status = "ended"
            self.winner = winner
            self._changed()
            return {"winner": winner}
        if from_night:
            self._enter_day()
        else:
            self._enter_night()
        self._changed()
        return {"next": self.status, "round": self.round}

    # ---------- 胜负判定 ----------
    def check_winner(self):
        alive_ids = [p for p in self.players if self.alive.get(p)]
        wolf_count = sum(1 for p in alive_ids if self.roles.get(p) in WOLF_ROLES)
        good_count = len(alive_ids) - wolf_count
        if wolf_count == 0:
            return "good"
        god_count = sum(1 for p in alive_ids if self.roles.get(p) in GOD_ROLES)
        villager_count = sum(1 for p in alive_ids if self.roles.get(p) == "villager")
        wc = self.win_condition
        # 屠城：狼人数 ≥ 好人数 → 狼胜
        if wc == "city":
            if wolf_count >= good_count:
                return "werewolf"
            return None
        # 屠边：神职全灭 或 平民全灭 → 狼胜
        if wc == "side":
            if god_count == 0 or villager_count == 0:
                return "werewolf"
            return None
        # default：屠边 + 屠城 同时判定
        if wolf_count >= good_count:
            return "werewolf"
        if god_count == 0 or villager_count == 0:
            return "werewolf"
        return None

    # ---------- 发言 ----------
    def _advance_speak(self):
        """问题1：推进发言顺序，全部发言完毕进入投票阶段。"""
        if self.speak_queue:
            self.speak_queue.pop(0)
        self.speak_current = self.speak_queue[0] if self.speak_queue else None
        if not self.speak_current:
            self._enter_vote()
        else:
            self._set_speak_deadline()

    def _add_system_chat(self, message):
        """任务4：追加系统消息到发言板（不受阶段限制）。"""
        self.chat.append({
            "round": self.round,
            "player_id": "__system__",
            "name": "系统消息",
            "message": message,
            "time": int(time.time()),
        })

    def add_chat(self, player_id, message):
        message = (message or "").strip()
        if not message:
            raise GameError(400, "发言不能为空")
        if len(message) > 200:
            message = message[:200]
        # 问题2：夜晚禁止所有人发言
        if self.status == "night":
            raise GameError(403, "夜晚禁止发言")
        # 问题2：投票阶段禁止发言，票型在投票结束后才公开
        if self.status == "day" and self.stage is None:
            raise GameError(403, "投票阶段禁止发言")
        # 问题1：发言阶段只允许当前发言者发言
        if self.status == "day" and self.stage == "speak":
            if player_id != self.speak_current:
                raise GameError(403, "当前轮到 %s 发言" % self.player_name(self.speak_current))
        # 任务2：遗言阶段只允许被放逐者发言
        if self.status == "day" and self.stage == "last_words":
            if player_id != self.last_words_player:
                raise GameError(403, "当前是 %s 的遗言时间" % self.player_name(self.last_words_player))
        is_last_words = (self.status == "day" and self.stage == "last_words")
        chat_name = self._display_name(player_id) + ("（遗言）" if is_last_words else "")
        self.chat.append({
            "round": self.round,
            "player_id": player_id,
            "name": chat_name,
            "message": message,
            "time": int(time.time()),
        })
        # 问题1：发言后推进到下一个
        if self.status == "day" and self.stage == "speak":
            self._advance_speak()
        # 任务2：遗言结束后进入技能结算
        elif self.status == "day" and self.stage == "last_words":
            self._finish_last_words()
        self._changed()

    # ---------- 状态视图 ----------
    def _available_actions(self, me, role):
        if not self.alive.get(me):
            # 已出局：仅当自己是待结算的狼美人/猎人（被动技能）时可行动
            if self.status == "day" and self.stage == "charm_pending" and me == self.pending_lover:
                return ["charm", "pass"]
            if self.status == "day" and self.stage == "gun_pending" and me == self.pending_gunner:
                return ["gun", "pass"]
            return []
        if self.status == "night":
            key = NIGHT_STEPS[self.night_step][0] if self.night_step is not None else None
            if me in self.night["done"].get(key, set()):
                return []
            if key == "wolf_kill" and role in WOLF_ROLES:
                return ["kill", "pass"]
            if key == "beauty_charm" and role == "wolf_beauty":
                return ["charm", "pass"]
            if key == "seer_verify" and role == "seer":
                return ["verify", "pass"]
            if key == "witch" and role == "witch":
                acts = []
                # 问题3：同晚用了毒药则不显示解药，用了解药则不显示毒药
                if not self.night["witch_save_used"] and self.night["poison"] is None:
                    acts.append("save")
                if not self.night["witch_poison_used"] and not self.night["save"]:
                    acts.append("poison")
                acts.append("pass")
                return acts
            if key == "guard" and role == "guard":
                return ["guard", "pass"]
            return []
        if self.status == "day":
            if self.stage == "speak":
                if me == self.speak_current:
                    return ["pass"]
                return []
            if self.stage == "last_words" and me == self.last_words_player:
                return ["pass"]
            if self.stage == "charm_pending" and me == self.pending_lover:
                return ["charm", "pass"]
            if self.stage == "gun_pending" and me == self.pending_gunner:
                return ["gun", "pass"]
            if self.stage is None:
                if me in self.day_votes:
                    return []
                return ["vote", "pass"]
        return []

    def state_for(self, token):
        me = self.tokens.get(token)
        if me is None:
            return None
        role = self.roles.get(me)
        dead_in_night = [p for p in self.night_deaths if not self.alive.get(p)]
        data = {
            "room_id": self.room_id,
            "status": self.status,
            "stage": self.stage,
            "round": self.round,
            "winner": self.winner,
            "host_id": self.host_id,
            "me": me,
            "my_name": self.player_name(me),
            "my_seat": self.seats.get(me),
            "my_alive": self.alive.get(me, False),
            "my_role": role,
            "role_name": ROLE_NAMES.get(role),
            "night_step_name": self.night_step_name if self.status == "night" else None,
            "night_step_has_human": (
                self._step_has_human(self.night_step)
                if self.status == "night" and self.night_step is not None else False
            ),
            "night_step_remaining": (
                max(0, int(self.night_step_deadline - time.time()))
                if self.status == "night" and self.night_step_deadline else None
            ),
            "players": [
                {
                    "player_id": p,
                    "name": self.player_name(p),
                    "seat": self.seats.get(p),
                    "alive": self.alive.get(p, False),
                    "dead_reason": self.death_reasons.get(p),
                    "is_ai": self.is_ai(p),
                    "ai_controlled": p in self.ai_controlled,
                }
                for p in sorted(self.players, key=lambda x: self.seats.get(x, 0))
            ],
            "ai_count": self.ai_count,
            "is_host": me == self.host_id,
            "can_start": (
                self.status == "waiting"
                and me == self.host_id
                and len(self.players) >= MIN_PLAYERS
            ),
            "player_count": len(self.players),
            "night_deaths": [p for p in self.night_deaths],
            "night_death_names": [self._display_name(p) for p in self.night_deaths],
            "day_votes": {},  # 问题2：投票进行中不展示票型，结算后用 vote_result 公开
            "vote_in_progress": (self.status == "day" and self.stage is None),
            "vote_result": self.last_vote_result,
            "elimination_log": [
                {
                    "name": self._display_name(e["player_id"]),
                    "round": e["round"],
                    "reason": DEAD_REASON_SIMPLE.get(e["reason"], "淘汰"),
                }
                for e in self.elimination_log
            ],
            "chat": self.chat[-50:],
            "speak_current_name": (
                self._display_name(self.speak_current)
                if self.status == "day" and self.stage == "speak" and self.speak_current else None
            ),
            "speak_current": (
                self.speak_current
                if self.status == "day" and self.stage == "speak" else None
            ),
            "speak_order": (
                [self._display_name(p) for p in self.speak_queue]
                if self.status == "day" and self.stage == "speak" else []
            ),
            "speak_remaining": (
                max(0, int(self.speak_deadline - time.time()))
                if self.status == "day" and self.stage == "speak" and self.speak_deadline else None
            ),
            "vote_remaining": (
                max(0, int(self.vote_deadline - time.time()))
                if self.status == "day" and self.stage is None and self.vote_deadline else None
            ),
            "last_words_player": (
                self.last_words_player
                if self.status == "day" and self.stage == "last_words" else None
            ),
            "last_words_player_name": (
                self._display_name(self.last_words_player)
                if self.status == "day" and self.stage == "last_words" and self.last_words_player else None
            ),
            "last_words_remaining": (
                max(0, int(self.last_words_deadline - time.time()))
                if self.status == "day" and self.stage == "last_words" and self.last_words_deadline else None
            ),
            "role_config": self.role_config,
            "effective_role_config": {
                ROLE_NAMES.get(r, r): c
                for r, c in (self.role_config or ROLE_CONFIGS.get(len(self.players), {})).items()
            },
            "win_condition": self.win_condition,
        }
        # 任务5：游戏结束暴露复盘数据（所有玩家角色 + 夜间操作 + 投票记录）
        if self.status == "ended":
            data["all_roles"] = [
                {
                    "name": self._display_name(p),
                    "seat": self.seats.get(p),
                    "role": self.roles.get(p),
                    "role_name": ROLE_NAMES.get(self.roles.get(p)),
                    "alive": self.alive.get(p, False),
                    "dead_reason": DEAD_REASON_SIMPLE.get(self.death_reasons.get(p)),
                }
                for p in sorted(self.players, key=lambda x: self.seats.get(x, 0))
            ]
            data["night_action_log"] = self.night_action_log
            data["vote_log"] = self.vote_log
        if role in WOLF_ROLES:
            data["wolf_teammates"] = [
                {"player_id": p, "name": self.player_name(p), "seat": self.seats.get(p)}
                for p in self.players
                if self.alive.get(p) and self.roles[p] in WOLF_ROLES and p != me
            ]
            if self.status == "night":
                # 显示所有狼人已提交的刀目标，供 AI 狼人征询意见
                data["wolf_kill_votes"] = {
                    self._display_name(pid): self._display_name(t)
                    for pid, t in self.night.get("kill_votes", {}).items()
                    if t
                }
                if self.night["kill"]:
                    data["wolf_kill_confirm"] = self._display_name(self.night["kill"])
        if role == "seer":
            data["verify_history"] = self.night["verify_history"]
        if role == "witch":
            # 女巫看到最终确定的刀口（狼人环节完成后）
            witch_kill = self.night["kill"] if self.night.get("kill_finalized") else None
            data["witch"] = {
                "save_used": self.night["witch_save_used"],
                "poison_used": self.night["witch_poison_used"],
                # 问题4：女巫每晚看到狼刀目标（仅女巫可见）
                "kill_target_name": (
                    self._display_name(witch_kill)
                    if self.status == "night" and witch_kill else None
                ),
            }
        data["available_actions"] = self._available_actions(me, role)
        return data

    # ---------- 序列化 ----------
    def to_dict(self):
        d = dict(self.__dict__)
        d.pop("on_change", None)
        night = dict(d["night"])
        night["done"] = {k: list(v) for k, v in night["done"].items()}
        d["night"] = night
        d["ai_players"] = list(d["ai_players"])
        d["ai_controlled"] = list(d.get("ai_controlled", set()))
        return d

    @classmethod
    def from_dict(cls, d):
        room = cls.__new__(cls)
        d = dict(d)
        night = dict(d["night"])
        done = night["done"]
        if isinstance(done, list):
            night["done"] = {}
        else:
            night["done"] = {k: set(v) for k, v in done.items()}
        night.setdefault("kill_finalized", False)
        d["night"] = night
        d["ai_players"] = set(d.get("ai_players", []))
        d["ai_controlled"] = set(d.get("ai_controlled", []))
        d.setdefault("on_change", None)
        d.setdefault("speak_deadline", None)
        d.setdefault("last_vote_result", None)
        d.setdefault("role_config", None)
        d.setdefault("win_condition", "default")
        d.setdefault("seats", {})
        d.setdefault("last_seen", {})
        d.setdefault("elimination_log", [])
        d.setdefault("vote_deadline", None)
        d.setdefault("night_action_log", [])
        d.setdefault("vote_log", [])
        d.setdefault("last_words_player", None)
        d.setdefault("last_words_deadline", None)
        room.__dict__.update(d)
        return room