# -*- coding: utf-8 -*-
"""冒烟测试：走一遍真实 HTTP 流程，验证页面渲染与写操作。"""
import http.cookiejar
import re
import sys
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8000"
fails = []


def client():
    cj = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def get(op, path):
    try:
        with op.open(BASE + path, timeout=10) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def post(op, path, data):
    body = urllib.parse.urlencode(data).encode()
    try:
        with op.open(BASE + path, body, timeout=10) as r:
            return r.status, r.read().decode("utf-8"), r.url
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8"), None


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  {extra}" if not cond else ""))
    if not cond:
        fails.append(name)


print("\n[1] 未登录访问")
op = client()
st, html = get(op, "/")
check("未登录访问 / 跳转登录页", "研究生管理系统" in html and "登 录" in html)

print("\n[2] 老师登录")
st, html, url = post(op, "/login", {"account": "t001", "password": "123456"})
check("老师登录成功", st == 200 and "工作台" in html, f"status={st}")
check("工作台显示三个名单卡片", html.count("stat-card") == 3)
check("未交周报名单含王五", "本周未交周报" in html)
check("逾期数字存在", "逾期任务" in html)
m = re.search(r'count count-danger">\s*(\d+)', html)
check("逾期任务数 > 0", bool(m and int(m.group(1)) > 0), m.group(1) if m else "no match")
check("学生概览表格有 3 行", html.count("查看详情") == 3)

print("\n[3] 老师任务页")
st, html = get(op, "/teacher/tasks")
check("任务管理页 200", st == 200 and "派发新任务" in html)

print("\n[4] 学生详情聚合页")
st, dash = get(op, "/teacher")
sid = None
m = re.search(r'/teacher/students/(\d+)', dash)
if m:
    sid = m.group(1)
check("工作台能提取到学生 id", sid is not None, f"sid={sid}")
st, html = get(op, f"/teacher/students/{sid}")
check("学生详情页 200", st == 200 and "近 14 天打卡" in html, f"status={st}")
check("有 14 个打卡格子", html.count("checkin-cell") == 14)
check("有 4 周周报折叠", html.count("week-item") >= 4)
check("周报评语表单存在", "feedback-form" in html)

print("\n[5] 派发任务")
st, html, _ = post(op, "/teacher/tasks", {
    "title": "冒烟测试任务", "detail": "由自动化测试创建",
    "student_id": sid, "due_date": "2026-11-01",
})
check("派发任务成功", "冒烟测试任务" in html)

print("\n[6] 越权：学生访问老师页")
op2 = client()
post(op2, "/login", {"account": "s001", "password": "123456"})
st, html = get(op2, "/teacher")
check("学生访问 /teacher 返回 403", st == 403, f"status={st}")

print("\n[7] 学生首页")
st, html = get(op2, "/student")
check("学生首页 200", st == 200 and "今日打卡" in html)
check("显示我的任务", "我的任务" in html)
check("显示本周周报状态", "本周周报" in html)

print("\n[8] 学生打卡（幂等）")
st, html, _ = post(op2, "/student/checkin", {"content": "冒烟测试打卡第一次", "hours": "3"})
check("第一次打卡成功", "冒烟测试打卡第一次" in html)
st, html, _ = post(op2, "/student/checkin", {"content": "冒烟测试打卡第二次", "hours": "4.5"})
check("第二次打卡覆盖", "冒烟测试打卡第二次" in html and "冒烟测试打卡第一次" not in html)

print("\n[9] 学生提交周报（幂等）")
st, html, _ = post(op2, "/student/report", {
    "this_week": "冒烟测试周报内容", "next_week": "下周计划", "blockers": "无",
})
check("提交周报成功", "冒烟测试周报内容" in html)
st, html, _ = post(op2, "/student/report", {
    "this_week": "冒烟测试周报内容v2", "next_week": "下周计划v2", "blockers": "",
})
check("重复提交覆盖", "冒烟测试周报内容v2" in html and "冒烟测试周报内容v2" in html)

print("\n[10] 越权：学生改别人的任务")
st, html = get(op2, "/student")
tids = re.findall(r'/student/tasks/(\d+)/status', html)
st, body, _ = post(op2, "/student/tasks/99999/status", {"status": "done"})
check("改不存在任务返回 404", st == 404, f"status={st}")

print("\n[11] 老师看学生补交后的变化")
op3 = client()
post(op3, "/login", {"account": "t001", "password": "123456"})
st, html = get(op3, "/teacher")
check("周报已交后学生概览更新", html.count("已交") >= 1)

print("\n[12] 任务状态流转")
st, html = get(op2, "/student")
m = re.search(r'/student/tasks/(\d+)/status', html)
if m:
    tid = m.group(1)
    st, html, _ = post(op2, f"/student/tasks/{tid}/status", {"status": "done"})
    check("任务标记完成", "已完成" in html)

print("\n[13] 静态资源与错误页")
st, html = get(op, "/static/style.css")
check("CSS 可访问", st == 200 and "--brand" in html)
st, html = get(op, "/nonexistent-page")
check("404 页面渲染", st == 404)

print("\n" + "=" * 50)
if fails:
    print(f"失败 {len(fails)} 项：")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("全部通过")
