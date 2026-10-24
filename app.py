# -*- coding: utf-8 -*-
"""研究生管理系统 Demo —— Flask + SQLite + Jinja2，单进程、零构建。"""

import os
import sqlite3
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   session, url_for)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 数据库路径：默认放在项目目录；部署到云平台时用 DATA_DIR 指向持久化磁盘
# 例：DATA_DIR=/var/data  →  /var/data/demo.db
DB_PATH = os.path.join(os.environ.get("DATA_DIR", BASE_DIR), "demo.db")

app = Flask(__name__)
# 部署到公网必须换成随机值，否则别人能伪造登录 cookie：
#   python -c "import secrets; print(secrets.token_hex(32))"
app.secret_key = os.environ.get("SECRET_KEY", "demo-secret-key-not-for-production")

# 公网部署时打开：cookie 只走 HTTPS
if os.environ.get("HTTPS_ONLY") == "1":
    app.config["SESSION_COOKIE_SECURE"] = True


# ---------------------------------------------------------------- 时间工具
def today():
    return date.today()


def week_key(d=None):
    """ISO 周，跨年安全：2027-01-01 属于 2026-W53。"""
    d = d or today()
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_monday(d=None):
    d = d or today()
    return d - timedelta(days=d.weekday())


def week_label(key):
    y, w = key.split("-W")
    return f"{y} 年第 {int(w)} 周"


def week_range_label(key):
    y, w = key.split("-W")
    monday = date.fromisocalendar(int(y), int(w), 1)
    sunday = monday + timedelta(days=6)
    return f"{monday.month}/{monday.day} - {sunday.month}/{sunday.day}"


WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _as_date(s):
    """把 'YYYY-MM-DD' 或 date/datetime 统一成 date；解析失败返回 None。"""
    if isinstance(s, datetime):
        return s.date()
    if isinstance(s, date):
        return s
    try:
        return date.fromisoformat(str(s)[:10])
    except (ValueError, TypeError):
        return None


@app.template_filter("short_date")
def short_date(s):
    """'2026-10-24' -> '10-24'"""
    d = _as_date(s)
    return f"{d.month:02d}-{d.day:02d}" if d else ""


@app.template_filter("weekday_cn")
def weekday_cn(s):
    d = _as_date(s)
    return WEEKDAY_CN[d.weekday()] if d else ""


@app.template_filter("long_date")
def long_date(s):
    d = _as_date(s)
    if not d:
        return s or ""
    return f"{d.month} 月 {d.day} 日 {WEEKDAY_CN[d.weekday()]}"


# ---------------------------------------------------------------- 数据库
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT NOT NULL,
  account     TEXT NOT NULL UNIQUE,
  password    TEXT NOT NULL,
  role        TEXT NOT NULL CHECK (role IN ('teacher','student')),
  advisor_id  INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS tasks (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  title       TEXT NOT NULL,
  detail      TEXT DEFAULT '',
  student_id  INTEGER NOT NULL REFERENCES users(id),
  teacher_id  INTEGER NOT NULL REFERENCES users(id),
  due_date    TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'todo' CHECK (status IN ('todo','doing','done')),
  progress    INTEGER NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
  created_at  TEXT NOT NULL,
  done_at     TEXT
);

CREATE TABLE IF NOT EXISTS reports (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  student_id   INTEGER NOT NULL REFERENCES users(id),
  week_key     TEXT NOT NULL,
  this_week    TEXT NOT NULL,
  next_week    TEXT NOT NULL DEFAULT '',
  blockers     TEXT NOT NULL DEFAULT '',
  submitted_at TEXT NOT NULL,
  comment      TEXT,
  rating       INTEGER,
  UNIQUE (student_id, week_key)
);

CREATE TABLE IF NOT EXISTS checkins (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  student_id INTEGER NOT NULL REFERENCES users(id),
  date       TEXT NOT NULL,
  content    TEXT NOT NULL,
  hours      REAL DEFAULT 0,
  created_at TEXT NOT NULL,
  UNIQUE (student_id, date)
);

CREATE INDEX IF NOT EXISTS idx_tasks_student ON tasks(student_id, status);
CREATE INDEX IF NOT EXISTS idx_checkins_date ON checkins(date);
"""


def get_db():
    if "db" not in g:
        # timeout：多线程下等锁最多 10 秒，避免直接抛 database is locked
        g.db = sqlite3.connect(DB_PATH, timeout=10)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        # WAL 让读写不互相阻塞，waitress 多线程下更稳
        g.db.execute("PRAGMA journal_mode = WAL")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(force=False):
    if force and os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"] == 0:
        seed(conn)
    conn.commit()
    conn.close()


def seed(conn):
    """种子数据全部相对今天生成，保证任何时候演示都有'缺口'。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO users(name,account,password,role,advisor_id) VALUES(?,?,?,?,?)",
        ("张老师", "t001", "123456", "teacher", None),
    )
    tid = cur.lastrowid

    sids = []
    for name, acc in [("张三", "s001"), ("李四", "s002"), ("王五", "s003")]:
        cur.execute(
            "INSERT INTO users(name,account,password,role,advisor_id) VALUES(?,?,?,?,?)",
            (name, acc, "123456", "student", tid),
        )
        sids.append(cur.lastrowid)
    zs, ls, ww = sids

    d = today()

    def dd(n):
        return (d + timedelta(days=n)).isoformat()

    tasks = [
        (zs, "精读 Transformer 论文并整理笔记", "输出 1 页笔记，周三组会讲。", dd(-6), "done", 100, dd(-6)),
        (zs, "复现 baseline 实验", "按论文设置跑通，记录显存占用。", dd(2), "doing", 60, None),
        (zs, "整理实验数据表格", "把 3 组对比实验的数据汇总成表。", dd(-1), "todo", 0, None),
        (ls, "跑通数据预处理脚本", "处理 2 万条样本，检查缺失值。", dd(3), "doing", 40, None),
        (ls, "撰写开题报告初稿", "重点写研究现状和技术路线。", dd(7), "todo", 0, None),
        (ww, "完成文献综述第一节", "10 篇近三年文献，按主题归类。", dd(-5), "doing", 30, None),
        (ww, "搭建实验环境", "装好 CUDA 和依赖，跑通 hello world。", dd(-2), "todo", 0, None),
    ]
    for sid, title, detail, due, st, pg, done_at in tasks:
        cur.execute(
            """INSERT INTO tasks(title,detail,student_id,teacher_id,due_date,status,progress,created_at,done_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (title, detail, sid, tid, due, st, pg, now, done_at),
        )

    checkins = [
        (zs, dd(0), "跑通 baseline 前两个实验组，显存占用 18G。", 7.5),
        (zs, dd(-1), "精读论文第 3-5 节，整理注意力机制笔记。", 6.0),
        (zs, dd(-2), "修复数据加载器的 bug，重跑消融实验。", 8.0),
        (zs, dd(-3), "组会汇报，整理老师给的修改意见。", 3.0),
        (zs, dd(-4), "写实验记录，画 loss 曲线。", 6.5),
        (ls, dd(0), "数据预处理脚本跑通 5000 条，缺失值比例 3.2%。", 5.0),
        (ww, dd(-3), "看了 3 篇文献，还没开始写。", 2.0),
    ]
    for sid, dt, content, hours in checkins:
        cur.execute(
            "INSERT INTO checkins(student_id,date,content,hours,created_at) VALUES(?,?,?,?,?)",
            (sid, dt, content, hours, now),
        )

    cur.execute(
        """INSERT INTO reports(student_id,week_key,this_week,next_week,blockers,submitted_at,comment,rating)
           VALUES(?,?,?,?,?,?,?,?)""",
        (
            zs,
            week_key(d - timedelta(days=7)),
            "完成 baseline 复现第一阶段，跑通 2 组对比实验；精读 Transformer 论文并整理笔记。",
            "完成剩余 1 组实验并整理成表格；开始写开题报告的研究现状部分。",
            "显存不够，batch size 只能开到 8，结果波动较大。",
            now,
            "进度正常。显存问题可以先试梯度累积，下周组会讲一下。",
            4,
        ),
    )
    cur.execute(
        """INSERT INTO reports(student_id,week_key,this_week,next_week,blockers,submitted_at)
           VALUES(?,?,?,?,?,?)""",
        (
            ls,
            week_key(d),
            "数据预处理脚本跑通 5000 条样本，缺失值比例 3.2%，已记录。",
            "扩展到全量 2 万条，开始写开题报告。",
            "部分字段编码不一致，需要确认数据字典。",
            now,
        ),
    )


if not os.path.exists(DB_PATH):
    init_db()


# ---------------------------------------------------------------- 认证
def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    return get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not current_user():
            return redirect(url_for("login"))
        return f(*a, **kw)

    return wrapper


def teacher_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        u = current_user()
        if not u:
            return redirect(url_for("login"))
        if u["role"] != "teacher":
            abort(403)
        return f(*a, **kw)

    return wrapper


def student_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        u = current_user()
        if not u:
            return redirect(url_for("login"))
        if u["role"] != "student":
            abort(403)
        return f(*a, **kw)

    return wrapper


@app.context_processor
def inject_globals():
    return {
        "user": current_user(),
        "today_str": today().isoformat(),
        "today_date": today(),
        "current_week": week_key(),
        "week_label": week_label,
        "week_range_label": week_range_label,
    }


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        account = request.form.get("account", "").strip()
        password = request.form.get("password", "").strip()
        row = get_db().execute(
            "SELECT * FROM users WHERE account=? AND password=?", (account, password)
        ).fetchone()
        if row:
            session["uid"] = row["id"]
            return redirect(url_for("index"))
        flash("账号或密码错误", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/healthz")
def healthz():
    """云平台健康检查用，不依赖登录态。"""
    try:
        get_db().execute("SELECT 1").fetchone()
        return {"status": "ok", "db": os.path.basename(DB_PATH)}, 200
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "detail": str(e)}, 500


@app.route("/")
def index():
    u = current_user()
    if not u:
        return redirect(url_for("login"))
    return redirect(url_for("teacher_dashboard" if u["role"] == "teacher" else "student_home"))


@app.errorhandler(403)
def forbidden(e):
    return render_template("error.html", code=403, msg="你没有权限访问这个页面"), 403


# ---------------------------------------------------------------- 老师端
@app.route("/teacher")
@teacher_required
def teacher_dashboard():
    db = get_db()
    u = current_user()
    cw = week_key()
    td = today().isoformat()
    monday = week_monday().isoformat()
    sunday = (week_monday() + timedelta(days=6)).isoformat()

    no_report = db.execute(
        """SELECT u.id, u.name FROM users u
           WHERE u.role='student' AND u.advisor_id=?
             AND NOT EXISTS (SELECT 1 FROM reports r WHERE r.student_id=u.id AND r.week_key=?)
           ORDER BY u.id""",
        (u["id"], cw),
    ).fetchall()

    no_checkin = db.execute(
        """SELECT u.id, u.name FROM users u
           WHERE u.role='student' AND u.advisor_id=?
             AND NOT EXISTS (SELECT 1 FROM checkins c WHERE c.student_id=u.id AND c.date=?)
           ORDER BY u.id""",
        (u["id"], td),
    ).fetchall()

    overdue = db.execute(
        """SELECT u.id, u.name, COUNT(*) cnt, MIN(t.due_date) earliest
           FROM tasks t JOIN users u ON u.id=t.student_id
           WHERE t.status<>'done' AND t.due_date<? AND u.advisor_id=?
           GROUP BY u.id, u.name
           ORDER BY cnt DESC, earliest""",
        (td, u["id"]),
    ).fetchall()

    overview = db.execute(
        """SELECT u.id, u.name,
             (SELECT COUNT(*) FROM tasks t WHERE t.student_id=u.id AND t.status<>'done') open_tasks,
             (SELECT COUNT(*) FROM tasks t WHERE t.student_id=u.id AND t.status<>'done' AND t.due_date<?) overdue,
             (SELECT COUNT(*) FROM checkins c WHERE c.student_id=u.id AND c.date BETWEEN ? AND ?) week_checkins,
             (SELECT COUNT(*) FROM reports r WHERE r.student_id=u.id AND r.week_key=?) has_report
           FROM users u
           WHERE u.role='student' AND u.advisor_id=?
           ORDER BY u.id""",
        (td, monday, sunday, cw, u["id"]),
    ).fetchall()

    return render_template(
        "teacher_dashboard.html",
        no_report=no_report,
        no_checkin=no_checkin,
        overdue=overdue,
        overview=overview,
        active="dashboard",
    )


@app.route("/teacher/students/<int:sid>")
@teacher_required
def teacher_student(sid):
    db = get_db()
    teacher = current_user()
    stu = db.execute(
        "SELECT * FROM users WHERE id=? AND role='student' AND advisor_id=?",
        (sid, teacher["id"]),
    ).fetchone()
    if not stu:
        abort(404)

    td = today().isoformat()
    monday = week_monday().isoformat()
    sunday = (week_monday() + timedelta(days=6)).isoformat()
    cw = week_key()

    tasks = db.execute(
        "SELECT * FROM tasks WHERE student_id=? ORDER BY (status='done'), due_date",
        (sid,),
    ).fetchall()

    days = []
    for i in range(13, -1, -1):
        d = today() - timedelta(days=i)
        c = db.execute(
            "SELECT * FROM checkins WHERE student_id=? AND date=?", (sid, d.isoformat())
        ).fetchone()
        days.append({"date": d, "checkin": c})

    weeks = []
    for i in range(4):
        wk = week_key(today() - timedelta(days=7 * i))
        rep = db.execute(
            "SELECT * FROM reports WHERE student_id=? AND week_key=?", (sid, wk)
        ).fetchone()
        weeks.append(
            {"key": wk, "label": week_label(wk), "range": week_range_label(wk),
             "report": rep, "current": i == 0}
        )

    stats = {
        "open_tasks": sum(1 for t in tasks if t["status"] != "done"),
        "overdue": sum(
            1 for t in tasks if t["status"] != "done" and t["due_date"] < td
        ),
        "week_checkins": db.execute(
            "SELECT COUNT(*) c FROM checkins WHERE student_id=? AND date BETWEEN ? AND ?",
            (sid, monday, sunday),
        ).fetchone()["c"],
        "has_report": bool(
            db.execute(
                "SELECT 1 FROM reports WHERE student_id=? AND week_key=?", (sid, cw)
            ).fetchone()
        ),
    }

    return render_template(
        "teacher_student.html",
        stu=stu,
        tasks=tasks,
        days=days,
        weeks=weeks,
        stats=stats,
        active="dashboard",
    )


@app.route("/teacher/tasks", methods=["GET", "POST"])
@teacher_required
def teacher_tasks():
    db = get_db()
    u = current_user()

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        detail = request.form.get("detail", "").strip()
        student_id = request.form.get("student_id", "")
        due = request.form.get("due_date", "").strip()
        if not title or not student_id or not due:
            flash("标题、学生、截止日期都不能为空", "error")
        else:
            owner = db.execute(
                "SELECT id FROM users WHERE id=? AND role='student' AND advisor_id=?",
                (student_id, u["id"]),
            ).fetchone()
            if not owner:
                flash("学生不存在", "error")
            else:
                db.execute(
                    """INSERT INTO tasks(title,detail,student_id,teacher_id,due_date,status,progress,created_at)
                       VALUES(?,?,?,?,?,'todo',0,?)""",
                    (title, detail, student_id, u["id"], due,
                     datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                )
                db.commit()
                flash("任务已派发", "success")
        return redirect(url_for("teacher_tasks"))

    students = db.execute(
        "SELECT * FROM users WHERE role='student' AND advisor_id=? ORDER BY id", (u["id"],)
    ).fetchall()

    tasks = db.execute(
        """SELECT t.*, s.name AS student_name FROM tasks t
           JOIN users s ON s.id=t.student_id
           WHERE t.teacher_id=?
           ORDER BY (t.status='done'), t.due_date""",
        (u["id"],),
    ).fetchall()

    grouped = {}
    for t in tasks:
        grouped.setdefault(t["student_name"], []).append(t)

    return render_template(
        "teacher_tasks.html",
        students=students,
        grouped=grouped,
        active="tasks",
        default_due=(today() + timedelta(days=7)).isoformat(),
    )


@app.route("/teacher/reports/<int:rid>/feedback", methods=["POST"])
@teacher_required
def teacher_feedback(rid):
    db = get_db()
    u = current_user()
    rep = db.execute(
        """SELECT r.id, r.student_id FROM reports r
           JOIN users s ON s.id=r.student_id
           WHERE r.id=? AND s.advisor_id=?""",
        (rid, u["id"]),
    ).fetchone()
    if not rep:
        abort(404)
    comment = request.form.get("comment", "").strip()
    rating = request.form.get("rating") or None
    db.execute(
        "UPDATE reports SET comment=?, rating=? WHERE id=?",
        (comment, rating, rid),
    )
    db.commit()
    flash("评语已保存", "success")
    return redirect(url_for("teacher_student", sid=rep["student_id"]))


@app.route("/teacher/reset", methods=["POST"])
@teacher_required
def teacher_reset():
    db = g.pop("db", None)
    if db is not None:
        db.close()
    init_db(force=True)
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------- 学生端
@app.route("/student")
@student_required
def student_home():
    db = get_db()
    u = current_user()
    td = today().isoformat()
    cw = week_key()

    tasks = db.execute(
        "SELECT * FROM tasks WHERE student_id=? ORDER BY (status='done'), due_date",
        (u["id"],),
    ).fetchall()

    today_checkin = db.execute(
        "SELECT * FROM checkins WHERE student_id=? AND date=?", (u["id"], td)
    ).fetchone()

    report = db.execute(
        "SELECT * FROM reports WHERE student_id=? AND week_key=?", (u["id"], cw)
    ).fetchone()

    monday = week_monday().isoformat()
    sunday = (week_monday() + timedelta(days=6)).isoformat()
    week_checkins = db.execute(
        "SELECT COUNT(*) c FROM checkins WHERE student_id=? AND date BETWEEN ? AND ?",
        (u["id"], monday, sunday),
    ).fetchone()["c"]

    recent = db.execute(
        "SELECT * FROM checkins WHERE student_id=? ORDER BY date DESC LIMIT 5", (u["id"],)
    ).fetchall()

    return render_template(
        "student_home.html",
        tasks=tasks,
        today_checkin=today_checkin,
        report=report,
        week_checkins=week_checkins,
        recent=recent,
        active="home",
    )


@app.route("/student/checkin", methods=["POST"])
@student_required
def student_checkin():
    db = get_db()
    u = current_user()
    content = request.form.get("content", "").strip()
    hours = request.form.get("hours", "").strip()
    if not content:
        flash("打卡内容不能为空", "error")
        return redirect(url_for("student_home"))
    try:
        hours_val = float(hours) if hours else 0.0
    except ValueError:
        hours_val = 0.0
    db.execute(
        """INSERT INTO checkins(student_id,date,content,hours,created_at) VALUES(?,?,?,?,?)
           ON CONFLICT(student_id,date) DO UPDATE SET content=excluded.content,
             hours=excluded.hours, created_at=excluded.created_at""",
        (u["id"], today().isoformat(), content, hours_val,
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    db.commit()
    flash("今日打卡已保存", "success")
    return redirect(url_for("student_home"))


@app.route("/student/tasks/<int:tid>/status", methods=["POST"])
@student_required
def student_task_status(tid):
    db = get_db()
    u = current_user()
    task = db.execute(
        "SELECT * FROM tasks WHERE id=? AND student_id=?", (tid, u["id"])
    ).fetchone()
    if not task:
        abort(404)

    status = request.form.get("status", task["status"])
    if status not in ("todo", "doing", "done"):
        abort(400)

    progress = request.form.get("progress")
    if progress is None:
        progress = {"todo": 0, "doing": max(task["progress"], 10), "done": 100}[status]
    try:
        progress = max(0, min(100, int(progress)))
    except ValueError:
        progress = task["progress"]

    if status == "done":
        progress = 100

    done_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if status == "done" else None
    db.execute(
        "UPDATE tasks SET status=?, progress=?, done_at=? WHERE id=?",
        (status, progress, done_at, tid),
    )
    db.commit()
    flash("任务已更新", "success")
    return redirect(url_for("student_home"))


@app.route("/student/report", methods=["GET", "POST"])
@student_required
def student_report():
    db = get_db()
    u = current_user()
    cw = week_key()

    if request.method == "POST":
        this_week = request.form.get("this_week", "").strip()
        if not this_week:
            flash("“本周做了什么”不能为空", "error")
            return redirect(url_for("student_report"))
        db.execute(
            """INSERT INTO reports(student_id,week_key,this_week,next_week,blockers,submitted_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(student_id,week_key) DO UPDATE SET
                 this_week=excluded.this_week, next_week=excluded.next_week,
                 blockers=excluded.blockers, submitted_at=excluded.submitted_at""",
            (
                u["id"], cw, this_week,
                request.form.get("next_week", "").strip(),
                request.form.get("blockers", "").strip(),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        db.commit()
        flash("本周周报已提交", "success")
        return redirect(url_for("student_home"))

    report = db.execute(
        "SELECT * FROM reports WHERE student_id=? AND week_key=?", (u["id"], cw)
    ).fetchone()

    history = db.execute(
        "SELECT * FROM reports WHERE student_id=? AND week_key<>? ORDER BY week_key DESC LIMIT 4",
        (u["id"], cw),
    ).fetchall()

    return render_template(
        "student_report.html", report=report, history=history, active="report"
    )


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 8000))
    print("=" * 56)
    print("  研究生管理系统 Demo")
    print("  老师：t001 / 123456    学生：s001 / s002 / s003")
    print(f"  打开 http://127.0.0.1:{port}")
    print("=" * 56)

    # 线上用 waitress（Windows 也能跑）；本地没装就退回 Flask 开发服务器
    try:
        from waitress import serve

        print("  生产模式：waitress")
        serve(app, host="0.0.0.0", port=port, threads=8)
    except ImportError:
        print("  开发模式：Flask（线上请先 pip install waitress）")
        app.run(host="0.0.0.0", port=port, debug=False)
