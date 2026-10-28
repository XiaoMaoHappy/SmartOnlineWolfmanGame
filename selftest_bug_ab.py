# -*- coding: utf-8 -*-
"""问题 A/B 自测：验证前端推送和确认弹窗改动。"""
import re

HTML_PATH = r"D:\Users\honor\IDEProjects\demo\workspace_extracted\workspace\static\index.html"
with open(HTML_PATH, encoding="utf-8") as f:
    html = f.read()

passed = 0
failed = 0

def check(name, condition, detail=""):
    global passed, failed
    if condition:
        print("[PASS] %s %s" % (name, detail))
        passed += 1
    else:
        print("[FAIL] %s %s" % (name, detail))
        failed += 1

print("问题 A/B 自测开始")
print("=" * 60)

# --- 问题 A: SSE 推送 + 增量渲染 ---

# A1: 轮询间隔 500ms（不是 5000）
check("A1 轮询间隔500ms",
      "setInterval(poll, 500)" in html and "setInterval(poll, 5000)" not in html,
      "(500ms 兜底)")

# A2: 防堆积 inFlight
check("A2 请求防堆积(inFlight)",
      "inFlight" in html and "if (inFlight) return" in html,
      "(请求未返回不发起下一次)")

# A3: 增量渲染 setArea + lastSig
check("A3 增量渲染机制(setArea)",
      "function setArea" in html and "lastSig" in html,
      "(签名比较，只更新变化区域)")

# A4: SSE EventSource 存在
check("A4 SSE主动推送(EventSource)",
      "EventSource" in html and "evtSource.onmessage" in html,
      "(服务端推送前端监听)")

# A5: SSE 收到事件后立即 poll
check("A5 SSE事件即时触发poll",
      re.search(r"ev\.type\s*===\s*'update'.*poll\(\)", html, re.DOTALL) is not None,
      "(收到update立即拉取)")

# A6: 各区域用 setArea 增量更新
areas = ["role-area", "wolf-area", "witch-area", "verify-area", "players-area", "action-area", "vote-area"]
for area in areas:
    check("A6 增量更新 %s" % area,
          "setArea('%s'" % area in html,
          "(签名比较)")

# A7: chat-box 增量更新
check("A7 chat-box增量更新",
      "lastSig['chat-box']" in html and "chatSig" in html,
      "(聊天只在内容变化时更新)")

# A8: 视图切换增量
check("A8 视图切换增量(__view签名)",
      "__view" in html,
      "(lobby/game切换不重复)")

# --- 问题 B: 统一二次确认弹窗 ---

# B1: 确认弹窗 HTML 存在
check("B1 确认弹窗HTML",
      'id="confirm-modal"' in html and 'id="confirm-msg"' in html
      and 'id="confirm-yes"' in html and 'id="confirm-no"' in html,
      "(modal + 是/否按钮)")

# B2: showConfirm 统一函数
check("B2 showConfirm统一函数",
      "function showConfirm" in html,
      "(所有角色共用)")

# B3: onAction 调用 showConfirm
check("B3 onAction走确认流程",
      "showConfirm(" in html and "doAction(type, selectedTarget)" in html,
      "(确认后才提交)")

# B4: 点否取消选择
check("B4 点否取消选择",
      "cancelBack" in html and "pendingAction = null" in html,
      "(回到未选择状态)")

# B5: 有目标确认消息格式
check("B5 有目标确认消息",
      "是否确认对玩家" in html and "使用" in html,
      '("是否确认对玩家X使用<技能名>？")')

# B6: 无目标确认消息格式
check("B6 无目标确认消息",
      "是否确认执行" in html,
      '("是否确认执行<行动名>？")')

# B7: 弹窗不自动确认
check("B7 弹窗不自动确认",
      "confirm-modal" in html and "classList.add('hidden')" in html,
      "(需手动点击是/否)")

# B8: ACTION_CONFIRM_NAME 覆盖所有主动行动
confirm_names = ["kill", "guard", "verify", "save", "poison", "vote", "charm", "gun", "pass"]
for act in confirm_names:
    check("B8 确认名称覆盖 %s" % act,
          re.search(r"ACTION_CONFIRM_NAME\s*=\s*\{[^}]*\b%s\b" % act, html, re.DOTALL) is not None,
          "(有确认名称)")

# B9: 确认弹窗 CSS 样式
check("B9 弹窗CSS样式",
      "modal-overlay" in html and "modal-box" in html and "modal-btns" in html,
      "(overlay+box+btns)")

print("=" * 60)
print("结果: %d 通过, %d 失败" % (passed, failed))
if failed == 0:
    print("自测全部通过")
else:
    print("有失败项，请检查")