# -*- coding: utf-8 -*-
"""db.py — SQLite 数据层：建库、示例数据、CRUD、提醒计算。

约定：
- 每请求新建连接（sqlite3 线程安全模式下 check_same_thread=False）。
- 日期统一存 'YYYY-MM-DD' 字符串。
"""
import calendar
import os
import sqlite3
from datetime import date, datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pets.db")

# 记录类型 / 宠物状态的中文映射（工具与报告共用）
RECORD_TYPES = {
    "vaccine": "疫苗",
    "checkup": "体检",
    "deworm": "驱虫",
    "medication": "喂药",
    "clinic": "就诊",
}
PET_TYPES = {"cat": "猫", "dog": "狗", "bird": "鸟", "fish": "鱼", "other": "其他"}
PET_STATUS = {"healthy": "健康", "attention": "需关注", "ill": "治疗中"}
GENDERS = {"male": "公", "female": "母", "unknown": "未知"}
# 提醒重复周期：'' 表示不重复；完成一轮后按周期滚动生成下一轮记录
REPEAT_RULES = {"": "不重复", "daily": "每天", "weekly": "每周", "monthly": "每月", "yearly": "每年"}
# 用药状态
MED_STATUS = {"active": "在用", "finished": "已结束"}
# 花费分类（记账页与 AI 工具共用）
EXPENSE_CATEGORIES = {"medical": "医疗", "food": "粮食", "supply": "用品", "grooming": "洗护", "other": "其他"}
# 饮食类型（饮食日志页签与 AI 工具共用）
FEEDING_TYPES = {"kibble": "干粮", "wet": "湿粮", "treat": "零食", "raw": "生骨肉", "other": "其他"}


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS pets(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL, type TEXT, breed TEXT, gender TEXT,
  birthday TEXT, weight REAL, status TEXT DEFAULT 'healthy',
  personality TEXT, avatar TEXT,
  created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS health_records(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pet_id INTEGER, type TEXT, date TEXT, title TEXT, note TEXT, next_date TEXT,
  repeat_rule TEXT DEFAULT '',
  FOREIGN KEY(pet_id) REFERENCES pets(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS weight_logs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pet_id INTEGER, date TEXT, weight REAL,
  FOREIGN KEY(pet_id) REFERENCES pets(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS medications(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pet_id INTEGER, name TEXT NOT NULL,
  dosage TEXT DEFAULT '', frequency TEXT DEFAULT '',
  start_date TEXT, end_date TEXT,
  status TEXT DEFAULT 'active',
  note TEXT DEFAULT '',
  created_at TEXT DEFAULT (datetime('now','localtime')),
  FOREIGN KEY(pet_id) REFERENCES pets(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS memories(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pet_id INTEGER,
  date TEXT NOT NULL, title TEXT NOT NULL,
  text TEXT DEFAULT '', image TEXT DEFAULT '',
  created_at TEXT DEFAULT (datetime('now','localtime')),
  FOREIGN KEY(pet_id) REFERENCES pets(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS chat_sessions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  created_at TEXT DEFAULT (datetime('now','localtime')),
  updated_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS chat_history(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  is_draft INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now','localtime')),
  FOREIGN KEY(session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS app_kv(
  key TEXT PRIMARY KEY,
  value TEXT,
  updated_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS expenses(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pet_id INTEGER,
  date TEXT NOT NULL,
  category TEXT NOT NULL DEFAULT 'other',
  amount REAL NOT NULL,
  note TEXT DEFAULT '',
  created_at TEXT DEFAULT (datetime('now','localtime')),
  FOREIGN KEY(pet_id) REFERENCES pets(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS feeding_logs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pet_id INTEGER NOT NULL,
  date TEXT NOT NULL,
  food_type TEXT NOT NULL DEFAULT 'kibble',
  amount TEXT DEFAULT '',
  note TEXT DEFAULT '',
  created_at TEXT DEFAULT (datetime('now','localtime')),
  FOREIGN KEY(pet_id) REFERENCES pets(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_records_pet ON health_records(pet_id, date);
CREATE INDEX IF NOT EXISTS idx_records_next ON health_records(next_date);
CREATE INDEX IF NOT EXISTS idx_weights_pet ON weight_logs(pet_id, date);
CREATE INDEX IF NOT EXISTS idx_meds_pet ON medications(pet_id, status);
CREATE INDEX IF NOT EXISTS idx_memories_pet ON memories(pet_id, date);
CREATE INDEX IF NOT EXISTS idx_chat_session ON chat_history(session_id);
CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses(date);
CREATE INDEX IF NOT EXISTS idx_expenses_pet ON expenses(pet_id, date);
CREATE INDEX IF NOT EXISTS idx_feeding_pet ON feeding_logs(pet_id, date);
"""


def _placeholder_image(emoji: str, c1: str, c2: str) -> str:
    """生成纯色渐变 SVG 占位图（data URI），供示例回忆缩略图使用。"""
    import base64
    svg = (f"<svg xmlns='http://www.w3.org/2000/svg' width='640' height='420'>"
           f"<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>"
           f"<stop offset='0' stop-color='{c1}'/><stop offset='1' stop-color='{c2}'/>"
           f"</linearGradient></defs>"
           f"<rect width='640' height='420' fill='url(#g)'/>"
           f"<text x='320' y='250' font-size='120' text-anchor='middle'>{emoji}</text></svg>")
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


def init_db() -> None:
    conn = get_conn()
    try:
        # WAL：简报后台线程与请求并发读写不互相阻塞
        conn.execute("PRAGMA journal_mode=WAL")
        # 旧版 chat_history 无会话维度 → 重建（历史对话不迁移）
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(chat_history)").fetchall()]
        if cols and "session_id" not in cols:
            conn.execute("DROP TABLE chat_history")
        conn.executescript(SCHEMA)
        # 增量迁移：老库 health_records 补 repeat_rule 列（保留数据）
        rec_cols = [r["name"] for r in conn.execute("PRAGMA table_info(health_records)").fetchall()]
        if rec_cols and "repeat_rule" not in rec_cols:
            conn.execute("ALTER TABLE health_records ADD COLUMN repeat_rule TEXT DEFAULT ''")
        conn.commit()
    finally:
        conn.close()


def _d(offset_days: int) -> str:
    """相对今天的日期字符串，用于生成始终'新鲜'的示例数据。"""
    return (date.today() + timedelta(days=offset_days)).isoformat()


def seed() -> None:
    """首次运行（pets 表为空）时插入示例数据。"""
    conn = get_conn()
    try:
        if conn.execute("SELECT COUNT(*) FROM pets").fetchone()[0] > 0:
            return
        pets = [
            ("可乐", "dog", "柯基", "male", _d(-1100), 11.2, "healthy",
             "活泼贪吃，见到球就走不动路", "🐶"),
            ("布丁", "cat", "英短蓝猫", "female", _d(-760), 4.6, "attention",
             "胆小粘人，喜欢窗台晒太阳", "🐱"),
            ("翠翠", "bird", "虎皮鹦鹉", "female", _d(-420), 0.04, "healthy",
             "话痨，会吹口哨", "🦜"),
        ]
        pet_ids = []
        for p in pets:
            cur = conn.execute(
                "INSERT INTO pets(name,type,breed,gender,birthday,weight,status,personality,avatar)"
                " VALUES(?,?,?,?,?,?,?,?,?)", p)
            pet_ids.append(cur.lastrowid)

    # 健康记录：混合逾期(-)、临期(+within7)、正常(+)与无下次日期
        可乐, 布丁, 翠翠 = pet_ids
        records = [
            # (pet_id, type, date, title, note, next_date, repeat_rule)
            (可乐, "vaccine",    _d(-350), "狂犬疫苗（第1年）", "已按时接种，无不良反应", _d(5), "yearly"),
            (可乐, "deworm",     _d(-60),  "体内外驱虫（大宠爱）", "滴剂一支", _d(2), "monthly"),
            (可乐, "checkup",    _d(-200), "年度体检", "各项指标正常", _d(165), "yearly"),
            (可乐, "medication", _d(-3),   "肠胃调理（益生菌）", "每日一次，拌粮", None, ""),
            (布丁, "vaccine",    _d(-400), "猫三联（第三针）", "接种后观察30分钟", _d(-2), "yearly"),
            (布丁, "deworm",     _d(-30),  "体内驱虫（拜耳）", "空腹喂药", _d(60), ""),
            (布丁, "clinic",     _d(-8),   "外耳炎就诊", "左耳轻微发红，开耳药水滴7天", _d(1), ""),
            (布丁, "checkup",    _d(-150), "生化检查", "肾指标正常，注意饮水量", None, ""),
            (翠翠, "checkup",    _d(-90),  "羽毛与喙部检查", "状态良好", _d(90), ""),
            (翠翠, "medication", _d(-5),   "电解质水补充", "换羽期补充营养", None, ""),
        ]
        conn.executemany(
            "INSERT INTO health_records(pet_id,type,date,title,note,next_date,repeat_rule)"
            " VALUES(?,?,?,?,?,?,?)", records)

        # 体重历史（趋势）
        weights = [
            (可乐, _d(-365), 9.8), (可乐, _d(-270), 10.4), (可乐, _d(-180), 10.9),
            (可乐, _d(-90), 11.0), (可乐, _d(-15), 11.2),
            (布丁, _d(-365), 4.1), (布丁, _d(-240), 4.9), (布丁, _d(-120), 5.2),
            (布丁, _d(-30), 4.8), (布丁, _d(-5), 4.6),
            (翠翠, _d(-200), 0.038), (翠翠, _d(-60), 0.041), (翠翠, _d(-10), 0.040),
        ]
        conn.executemany(
            "INSERT INTO weight_logs(pet_id,date,weight) VALUES(?,?,?)", weights)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- 工具函数

def today_str() -> str:
    return date.today().isoformat()


def days_until(d: str) -> int:
    """距目标日期的天数；负数表示已逾期。"""
    return (datetime.strptime(d, "%Y-%m-%d").date() - date.today()).days


def age_from_birthday(birthday: str) -> str:
    """生日 → 'X岁Y个月' 人类可读年龄。"""
    if not birthday:
        return "未知"
    try:
        b = datetime.strptime(birthday, "%Y-%m-%d").date()
    except ValueError:
        return "未知"
    today = date.today()
    years = today.year - b.year
    months = today.month - b.month
    if today.day < b.day:
        months -= 1
    if months < 0:
        years -= 1
        months += 12
    if years <= 0:
        return f"{months}个月"
    return f"{years}岁{months}个月" if months else f"{years}岁"


def compute_reminders(within_days: int = 7) -> list[dict]:
    """临期（0<=剩<=within_days）与逾期（已过下次日期）列表。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT r.*, p.name AS pet_name FROM health_records r"
            " JOIN pets p ON p.id = r.pet_id"
            " WHERE r.next_date IS NOT NULL AND r.next_date != ''"
            " ORDER BY r.next_date ASC").fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = days_until(r["next_date"])
        if d <= within_days:
            rule = r["repeat_rule"] or ""
            out.append({
                "record_id": r["id"], "pet_id": r["pet_id"], "pet_name": r["pet_name"],
                "type": r["type"], "type_label": RECORD_TYPES.get(r["type"], r["type"]),
                "title": r["title"], "next_date": r["next_date"],
                "days_left": d, "overdue": d < 0,
                "repeat_rule": rule, "repeat_label": REPEAT_RULES.get(rule, "") if rule else "",
            })
    return out


def _rec_labels(d: dict) -> dict:
    """健康记录字典补充展示字段：类型标签、重复标签、剩余天数。"""
    d["type_label"] = RECORD_TYPES.get(d["type"], d["type"])
    d["repeat_rule"] = d.get("repeat_rule") or ""
    d["repeat_label"] = REPEAT_RULES.get(d["repeat_rule"], "") if d["repeat_rule"] else ""
    if d.get("next_date"):
        d["days_left"] = days_until(d["next_date"])
    return d


def roll_date(base: str, rule: str) -> str:
    """按重复周期从 base 滚动到下一次日期。
    月/年滚动钳制到目标月最后一天（1月31日→2月28日）；钳制后按钳制日继续滚动（2/28→3/28），
    这是有意的产品决策：宠物护理节奏以"大约每月"为准，不追求回到原日号。"""
    d = datetime.strptime(base, "%Y-%m-%d").date()
    if rule == "daily":
        return (d + timedelta(days=1)).isoformat()
    if rule == "weekly":
        return (d + timedelta(days=7)).isoformat()
    if rule in ("monthly", "yearly"):
        months = 1 if rule == "monthly" else 12
        idx = d.month - 1 + months
        y, m = d.year + idx // 12, idx % 12 + 1
        return date(y, m, min(d.day, calendar.monthrange(y, m)[1])).isoformat()
    return base


# ---------------------------------------------------------------- CRUD

def _pet_row_to_dict(row, conn) -> dict:
    d = dict(row)
    d["age"] = age_from_birthday(d.get("birthday"))
    latest_w = conn.execute(
        "SELECT weight, date FROM weight_logs WHERE pet_id=? ORDER BY date DESC, id DESC LIMIT 1",
        (d["id"],)).fetchone()
    d["latest_weight"] = latest_w["weight"] if latest_w else d.get("weight")
    d["record_count"] = conn.execute(
        "SELECT COUNT(*) FROM health_records WHERE pet_id=?", (d["id"],)).fetchone()[0]
    rem = compute_reminders()
    d["upcoming"] = [r for r in rem if r["pet_id"] == d["id"]]
    return d


def list_pets() -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute("SELECT * FROM pets ORDER BY id ASC").fetchall()
        return [_pet_row_to_dict(r, conn) for r in rows]
    finally:
        conn.close()


def get_pet(pet_id: int) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM pets WHERE id=?", (pet_id,)).fetchone()
        return _pet_row_to_dict(row, conn) if row else None
    finally:
        conn.close()


def fetch_pet_by_name(name: str) -> dict | None:
    """按名字查宠物：先精确，再 LIKE 模糊，支持 Agent 容错匹配。"""
    name = (name or "").strip()
    if not name:
        return None
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM pets WHERE name=?", (name,)).fetchone()
        if not row:
            row = conn.execute(
                "SELECT * FROM pets WHERE name LIKE ? ORDER BY id LIMIT 1",
                (f"%{name}%",)).fetchone()
        return _pet_row_to_dict(row, conn) if row else None
    finally:
        conn.close()


PET_FIELDS = ("name", "type", "breed", "gender", "birthday",
              "weight", "status", "personality", "avatar")


def add_pet(data: dict) -> dict:
    conn = get_conn()
    try:
        vals = [data.get(f) for f in PET_FIELDS]
        cur = conn.execute(
            f"INSERT INTO pets({','.join(PET_FIELDS)})"
            f" VALUES({','.join('?' * len(PET_FIELDS))})", vals)
        conn.commit()
        pid = cur.lastrowid
        w = data.get("weight")
        if w:
            conn.execute("INSERT INTO weight_logs(pet_id,date,weight) VALUES(?,?,?)",
                         (pid, today_str(), float(w)))
            conn.commit()
        return get_pet(pid)
    finally:
        conn.close()


def update_pet(pet_id: int, data: dict) -> dict | None:
    if get_pet(pet_id) is None:
        return None
    conn = get_conn()
    try:
        fields = {k: v for k, v in data.items() if k in PET_FIELDS and v is not None}
        if fields:
            conn.execute(
                "UPDATE pets SET " + ",".join(f"{k}=?" for k in fields) + " WHERE id=?",
                list(fields.values()) + [pet_id])
            conn.commit()
        if "weight" in fields and fields["weight"]:
            conn.execute("INSERT INTO weight_logs(pet_id,date,weight) VALUES(?,?,?)",
                         (pet_id, today_str(), float(fields["weight"])))
            conn.commit()
        return get_pet(pet_id)
    finally:
        conn.close()


def delete_pet(pet_id: int) -> bool:
    conn = get_conn()
    try:
        cur = conn.execute("DELETE FROM pets WHERE id=?", (pet_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def list_records(pet_id: int) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM health_records WHERE pet_id=? ORDER BY date DESC, id DESC",
            (pet_id,)).fetchall()
        return [_rec_labels(dict(r)) for r in rows]
    finally:
        conn.close()


RECORD_FIELDS = ("type", "date", "title", "note", "next_date", "weight", "repeat_rule")


def add_record(pet_id: int, data: dict) -> dict | None:
    if get_pet(pet_id) is None:
        return None
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO health_records(pet_id,type,date,title,note,next_date,repeat_rule)"
            " VALUES(?,?,?,?,?,?,?)",
            (pet_id, data.get("type"), data.get("date") or today_str(),
             data.get("title"), data.get("note"), data.get("next_date") or None,
             data.get("repeat_rule") or ""))
        # 带体重的记录同步写入体重表（供趋势图与 Agent 分析）
        if data.get("weight"):
            conn.execute("INSERT INTO weight_logs(pet_id,date,weight) VALUES(?,?,?)",
                         (pet_id, data.get("date") or today_str(), float(data["weight"])))
            conn.execute("UPDATE pets SET weight=? WHERE id=?",
                         (float(data["weight"]), pet_id))
        conn.commit()
        row = conn.execute("SELECT * FROM health_records WHERE id=?",
                           (cur.lastrowid,)).fetchone()
        return _rec_labels(dict(row))
    finally:
        conn.close()


def get_record(record_id: int) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM health_records WHERE id=?", (record_id,)).fetchone()
        return _rec_labels(dict(row)) if row else None
    finally:
        conn.close()


def update_record(record_id: int, data: dict) -> dict | None:
    """编辑健康记录；带体重时与新增记录同样同步体重表与宠物当前体重。"""
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM health_records WHERE id=?", (record_id,)).fetchone()
        if row is None:
            return None
        cur_type = data.get("type") or row["type"]
        cur_date = data.get("date") or row["date"]
        repeat = data["repeat_rule"] if data.get("repeat_rule") is not None else (row["repeat_rule"] or "")
        conn.execute(
            "UPDATE health_records SET type=?, date=?, title=?, note=?, next_date=?, repeat_rule=? WHERE id=?",
            (cur_type, cur_date or today_str(),
             data.get("title") if data.get("title") is not None else row["title"],
             data.get("note") if data.get("note") is not None else row["note"],
             data.get("next_date") or None, repeat, record_id))
        if data.get("weight"):
            conn.execute("INSERT INTO weight_logs(pet_id,date,weight) VALUES(?,?,?)",
                         (row["pet_id"], cur_date or today_str(), float(data["weight"])))
            conn.execute("UPDATE pets SET weight=? WHERE id=?",
                         (float(data["weight"]), row["pet_id"]))
        conn.commit()
        return _rec_labels(dict(conn.execute("SELECT * FROM health_records WHERE id=?", (record_id,)).fetchone()))
    finally:
        conn.close()


def complete_record(record_id: int) -> dict | None:
    """把带下次日期的记录标记为「本轮已做」：当前记录 next_date 置空退出提醒（历史保留）；
    若有重复周期，则以今天为 date 生成下一轮记录，next_date 按周期滚动。
    滚动基准 = max(今天, 原到期日)：逾期完成从今天重置节奏，提前完成保持原节奏。
    并发安全：用条件 UPDATE（next_date IS NOT NULL）抢占本轮，rowcount 为 0 说明已被处理，不重复生成。"""
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM health_records WHERE id=?", (record_id,)).fetchone()
        if row is None:
            return None
        if not row["next_date"]:
            return {"record": _rec_labels(dict(row)), "next": None}
        claimed = conn.execute(
            "UPDATE health_records SET next_date=NULL WHERE id=? AND next_date IS NOT NULL",
            (record_id,)).rowcount
        next_id = None
        rule = row["repeat_rule"] or ""
        if claimed and rule in REPEAT_RULES and rule:
            today = today_str()
            next_date = roll_date(max(today, row["next_date"]), rule)
            cur = conn.execute(
                "INSERT INTO health_records(pet_id,type,date,title,note,next_date,repeat_rule)"
                " VALUES(?,?,?,?,?,?,?)",
                (row["pet_id"], row["type"], today, row["title"], row["note"], next_date, rule))
            next_id = cur.lastrowid
        conn.commit()
        record = _rec_labels(dict(conn.execute("SELECT * FROM health_records WHERE id=?", (record_id,)).fetchone()))
        nxt = _rec_labels(dict(conn.execute("SELECT * FROM health_records WHERE id=?", (next_id,)).fetchone())) if next_id else None
        return {"record": record, "next": nxt}
    finally:
        conn.close()


def add_weight_log(pet_id: int, data: dict) -> dict | None:
    """独立的体重补录：写入体重表并更新宠物当前体重。"""
    if get_pet(pet_id) is None:
        return None
    conn = get_conn()
    try:
        conn.execute("INSERT INTO weight_logs(pet_id,date,weight) VALUES(?,?,?)",
                     (pet_id, data.get("date") or today_str(), float(data["weight"])))
        conn.execute("UPDATE pets SET weight=? WHERE id=?", (float(data["weight"]), pet_id))
        conn.commit()
        return {"pet_id": pet_id, "date": data.get("date") or today_str(),
                "weight": float(data["weight"])}
    finally:
        conn.close()


def delete_record(record_id: int) -> bool:
    conn = get_conn()
    try:
        cur = conn.execute("DELETE FROM health_records WHERE id=?", (record_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def list_weight_logs(pet_id: int) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT date, weight FROM weight_logs WHERE pet_id=? ORDER BY date ASC",
            (pet_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def stats() -> dict:
    """仪表盘总览统计。"""
    conn = get_conn()
    try:
        pet_count = conn.execute("SELECT COUNT(*) FROM pets").fetchone()[0]
        record_count = conn.execute("SELECT COUNT(*) FROM health_records").fetchone()[0]
    finally:
        conn.close()
    reminders = compute_reminders()
    return {
        "pet_count": pet_count,
        "record_count": record_count,
        "due_count": len(reminders),
    }


def list_all_records(limit: int | None = None, pet_id: int | None = None,
                     rtype: str | None = None, offset: int = 0) -> list[dict]:
    """全部健康记录（带宠物名/头像），按日期倒序 — 供"健康记录"页使用；可按宠物/类型筛选并分页。"""
    where, args = [], []
    if pet_id:
        where.append("r.pet_id=?"); args.append(pet_id)
    if rtype and rtype in RECORD_TYPES:
        where.append("r.type=?"); args.append(rtype)
    sql = ("SELECT r.*, p.name AS pet_name, p.avatar AS pet_avatar, p.type AS pet_type"
           " FROM health_records r JOIN pets p ON p.id = r.pet_id"
           + (" WHERE " + " AND ".join(where) if where else "")
           + " ORDER BY r.date DESC, r.id DESC")
    if limit:
        sql += " LIMIT ? OFFSET ?"; args += [limit, max(0, offset)]
    conn = get_conn()
    try:
        rows = conn.execute(sql, args).fetchall()
    finally:
        conn.close()
    return [_rec_labels(dict(r)) for r in rows]


def count_records(pet_id: int | None = None, rtype: str | None = None) -> int:
    where, args = [], []
    if pet_id:
        where.append("pet_id=?"); args.append(pet_id)
    if rtype and rtype in RECORD_TYPES:
        where.append("type=?"); args.append(rtype)
    conn = get_conn()
    try:
        return conn.execute("SELECT COUNT(*) FROM health_records"
                            + (" WHERE " + " AND ".join(where) if where else ""), args).fetchone()[0]
    finally:
        conn.close()


def recent_activity(limit: int = 5) -> list[dict]:
    """最近动态：健康记录（带宠物名）按日期倒序。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT r.id, r.pet_id, r.type, r.title, r.date, p.name AS pet_name"
            " FROM health_records r JOIN pets p ON p.id = r.pet_id"
            " ORDER BY r.date DESC, r.id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["type_label"] = RECORD_TYPES.get(d["type"], d["type"])
            out.append(d)
        return out
    finally:
        conn.close()


MEMORY_FIELDS = ("pet_id", "date", "title", "text", "image")


def _mem_row_to_dict(row, name_map: dict[int, dict]) -> dict:
    d = dict(row)
    pet = name_map.get(d.get("pet_id"))
    d["pet_name"] = pet["name"] if pet else None
    d["pet_avatar"] = (pet.get("avatar") if pet else None) or (
        {"cat": "🐱", "dog": "🐶", "bird": "🦜"}.get(pet.get("type") if pet else None, "🐾") if pet else None)
    return d


def list_memories(pet_id: int | None = None) -> list[dict]:
    conn = get_conn()
    try:
        name_map = {r["id"]: dict(r) for r in conn.execute("SELECT id,name,type,avatar FROM pets")}
        sql = ("SELECT * FROM memories" + (" WHERE pet_id=?" if pet_id else "")
               + " ORDER BY date DESC, id DESC")
        rows = conn.execute(sql, ((pet_id,) if pet_id else ())).fetchall()
        return [_mem_row_to_dict(r, name_map) for r in rows]
    finally:
        conn.close()


def get_memory(mem_id: int) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM memories WHERE id=?", (mem_id,)).fetchone()
        if not row:
            return None
        name_map = {r["id"]: dict(r) for r in conn.execute("SELECT id,name,type,avatar FROM pets")}
        return _mem_row_to_dict(row, name_map)
    finally:
        conn.close()


def add_memory(data: dict) -> dict | None:
    pid = data.get("pet_id")
    if pid and get_pet(pid) is None:
        return None
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO memories(pet_id,date,title,text,image) VALUES(?,?,?,?,?)",
            (pid, data.get("date"), data.get("title"),
             data.get("text") or "", data.get("image") or ""))
        conn.commit()
        return get_memory(cur.lastrowid)
    finally:
        conn.close()


def update_memory(mem_id: int, data: dict) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute("SELECT id FROM memories WHERE id=?", (mem_id,)).fetchone()
        if not row:
            return None
    finally:
        conn.close()
    if data.get("pet_id") and get_pet(data["pet_id"]) is None:
        return None
    conn = get_conn()
    try:
        fields = {k: v for k, v in data.items() if k in MEMORY_FIELDS and v is not None}
        if fields:
            conn.execute("UPDATE memories SET " + ",".join(f"{k}=?" for k in fields) + " WHERE id=?",
                         list(fields.values()) + [mem_id])
            conn.commit()
        return get_memory(mem_id)
    finally:
        conn.close()


def delete_memory(mem_id: int) -> bool:
    conn = get_conn()
    try:
        cur = conn.execute("DELETE FROM memories WHERE id=?", (mem_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def days_together(pet: dict) -> int | None:
    """陪伴天数：优先按生日（接回家日期），否则按建档日期。"""
    base = pet.get("birthday") or (pet.get("created_at") or "")[:10]
    if not base:
        return None
    try:
        return -days_until(base)
    except ValueError:
        return None


def seed_memories() -> None:
    """回忆集首次运行时注入示例回忆（仅当 memories 表为空；按名字匹配现有宠物，匹配不到则跳过）。"""
    conn = get_conn()
    try:
        if conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] > 0:
            return
        pet_of = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM pets")}
        samples = [
            # (宠物名, 相对天数偏移, 标题, 文字, 占位图emoji/配色)
            ("可乐", -1080, "回家第一天", "纸箱里缩着一小团，半夜偷偷爬上了我的床，从此床上再没有我的位置。", ("🐶", "#E9D5B8", "#C2703D")),
            ("可乐", -520, "第一次郊游", "带到河边它死活不下水，回家路上倒是把鞋叼走了。", ("🌊", "#BFD8C8", "#3E7C4F")),
            ("可乐", -95, "学会了新把戏", "转圈换零食，如今一转就是三圈，刹不住车。", ("🎾", "#E9D5B8", "#B7811B")),
            ("布丁", -700, "到家第一夜", "躲在沙发底下发出低沉的飞机耳警告，第三天自己走了出来。", ("🐱", "#C9CFE0", "#6B6358")),
            ("布丁", -430, "第一次打疫苗", "进诊室前威风凛凛，针还没扎先嚎出了声，护士都笑了。", ("💉", "#BFD8C8", "#3E7C4F")),
            ("布丁", -110, "发现体重超标，开始减肥", "医生推了减肥粮，如今每天监督它少舔两口罐头。", ("⚖️", "#E9C9C2", "#B94A3F")),
            ("翠翠", -400, "学会第一句口哨", "清晨对着鸟笼吹了三遍，它居然回了一段走调的。", ("🦜", "#D8E3C9", "#8BA870")),
            ("翠翠", -60, "站在肩头散步", "在小区走了一圈，回头率百分百，邻居都喊它'翠老板'。", ("🌳", "#E9D5B8", "#C2703D")),
        ]
        for name, off, title, text, (emoji, c1, c2) in samples:
            pid = pet_of.get(name)
            if pid is None:
                continue
            conn.execute(
                "INSERT INTO memories(pet_id,date,title,text,image) VALUES(?,?,?,?,?)",
                (pid, _d(off), title, text, _placeholder_image(emoji, c1, c2)))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- 对话会话 / 键值缓存

def create_session(title: str) -> int:
    conn = get_conn()
    try:
        cur = conn.execute("INSERT INTO chat_sessions(title) VALUES(?)", (title,))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_session(session_id: int) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM chat_sessions WHERE id=?", (session_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_sessions() -> list[dict]:
    """会话列表（按最近更新倒序），附消息条数。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT s.id, s.title, s.updated_at, COUNT(h.id) AS message_count"
            " FROM chat_sessions s LEFT JOIN chat_history h ON h.session_id = s.id"
            " GROUP BY s.id ORDER BY s.updated_at DESC, s.id DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def touch_session(session_id: int) -> None:
    conn = get_conn()
    try:
        conn.execute("UPDATE chat_sessions SET updated_at=datetime('now','localtime') WHERE id=?",
                     (session_id,))
        conn.commit()
    finally:
        conn.close()


def delete_session(session_id: int) -> bool:
    conn = get_conn()
    try:
        cur = conn.execute("DELETE FROM chat_sessions WHERE id=?", (session_id,))
        conn.execute("DELETE FROM chat_history WHERE session_id=?", (session_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def session_messages(session_id: int, limit: int = 200) -> list[dict]:
    """某会话的全部消息（时间正序），content 为原始显示文本。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT role, content, is_draft FROM (SELECT * FROM chat_history"
            " WHERE session_id=? ORDER BY id DESC LIMIT ?) ORDER BY id ASC",
            (session_id, limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def chat_history(session_id: int, limit: int = 6) -> list[dict]:
    """某会话最近 N 条消息（供 Agent 上下文）；草稿消息替换为占位文本防止模仿。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT role, content, is_draft FROM (SELECT * FROM chat_history"
            " WHERE session_id=? ORDER BY id DESC LIMIT ?) ORDER BY id ASC",
            (session_id, limit)).fetchall()
        out = []
        for r in rows:
            content = "[已为用户起草记录草稿，等待确认]" if r["is_draft"] else r["content"]
            out.append({"role": r["role"], "content": content})
        return out
    finally:
        conn.close()


def add_chat_message(role: str, content: str, session_id: int | None = None,
                     is_draft: bool = False) -> None:
    conn = get_conn()
    try:
        conn.execute("INSERT INTO chat_history(role, content, session_id, is_draft) VALUES(?,?,?,?)",
                     (role, content, session_id, 1 if is_draft else 0))
        conn.commit()
    finally:
        conn.close()


def rename_session(session_id: int, title: str) -> bool:
    conn = get_conn()
    try:
        cur = conn.execute("UPDATE chat_sessions SET title=? WHERE id=?", (title, session_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def reset_demo_data() -> dict:
    """清空全部业务数据并重建示例（用于演示重置）。"""
    conn = get_conn()
    try:
        for t in ("chat_history", "chat_sessions", "app_kv", "weight_logs",
                  "health_records", "medications", "memories", "expenses", "feeding_logs", "pets"):
            conn.execute(f"DELETE FROM {t}")
        conn.commit()
    finally:
        conn.close()
    init_and_seed()
    return {"ok": True}


def kv_get(key: str) -> str | None:
    conn = get_conn()
    try:
        row = conn.execute("SELECT value FROM app_kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None
    finally:
        conn.close()


def kv_get_meta(key: str) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute("SELECT value, updated_at FROM app_kv WHERE key=?", (key,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def kv_set(key: str, value: str) -> None:
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO app_kv(key, value, updated_at) VALUES(?, ?, datetime('now','localtime'))"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, value))
        conn.commit()
    finally:
        conn.close()


def init_and_seed() -> None:
    init_db()
    seed()
    seed_memories()
    seed_medications()
    seed_expenses()
    seed_feeding()


# ---------------------------------------------------------------- 用药记录

MED_FIELDS = ("name", "dosage", "frequency", "start_date", "end_date", "status", "note")


def _med_dict(row) -> dict:
    d = dict(row)
    d["status"] = d.get("status") or "active"
    d["status_label"] = MED_STATUS.get(d["status"], d["status"])
    d["days_left"] = days_until(d["end_date"]) if d.get("end_date") else None
    return d


def list_medications(pet_id: int) -> list[dict]:
    """某宠物的用药：在用优先，其余按开始日期倒序。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM medications WHERE pet_id=?"
            " ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, start_date DESC, id DESC",
            (pet_id,)).fetchall()
        return [_med_dict(r) for r in rows]
    finally:
        conn.close()


def get_medication(med_id: int) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM medications WHERE id=?", (med_id,)).fetchone()
        return _med_dict(row) if row else None
    finally:
        conn.close()


def add_medication(pet_id: int, data: dict) -> dict | None:
    if get_pet(pet_id) is None:
        return None
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO medications(pet_id,name,dosage,frequency,start_date,end_date,status,note)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (pet_id, (data.get("name") or "").strip(), data.get("dosage") or "",
             data.get("frequency") or "", data.get("start_date") or today_str(),
             data.get("end_date") or None,
             data.get("status") if data.get("status") in MED_STATUS else "active",
             data.get("note") or ""))
        conn.commit()
        return get_medication(cur.lastrowid)
    finally:
        conn.close()


def update_medication(med_id: int, data: dict) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute("SELECT id FROM medications WHERE id=?", (med_id,)).fetchone()
        if row is None:
            return None
        fields = {k: v for k, v in data.items() if k in MED_FIELDS and v is not None}
        if "status" in fields and fields["status"] not in MED_STATUS:
            fields.pop("status")
        if "end_date" in fields and not fields["end_date"]:
            fields["end_date"] = None
        if fields:
            conn.execute("UPDATE medications SET " + ",".join(f"{k}=?" for k in fields) + " WHERE id=?",
                         list(fields.values()) + [med_id])
            conn.commit()
        return get_medication(med_id)
    finally:
        conn.close()


def delete_medication(med_id: int) -> bool:
    conn = get_conn()
    try:
        cur = conn.execute("DELETE FROM medications WHERE id=?", (med_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def active_medications_all() -> list[dict]:
    """全部在用药物（带宠物名），供简报/关注排序使用。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT m.*, p.name AS pet_name FROM medications m JOIN pets p ON p.id = m.pet_id"
            " WHERE m.status='active' ORDER BY m.pet_id, m.start_date DESC").fetchall()
        return [_med_dict(r) for r in rows]
    finally:
        conn.close()


def seed_medications() -> None:
    """用药示例（仅当 medications 表为空；按名字匹配现有宠物，与健康记录中的就诊/喂药呼应）。"""
    conn = get_conn()
    try:
        if conn.execute("SELECT COUNT(*) FROM medications").fetchone()[0] > 0:
            return
        pet_of = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM pets")}
        samples = [
            # (宠物名, 药名, 剂量, 频次, 开始偏移, 结束偏移, 状态, 备注)
            ("布丁", "耳药水（外耳炎）", "每侧 2 滴", "每日两次", -8, -1, "active", "外耳炎就诊开具，滴完复查左耳"),
            ("可乐", "益生菌粉", "1 袋", "每日一次", -3, 11, "active", "拌在早餐粮里，肠胃调理两周"),
            ("可乐", "体内外驱虫滴剂（大宠爱）", "1 支", "每月一次", -60, None, "active", "颈后皮肤滴用，滴后 24 小时不洗澡"),
            ("翠翠", "电解质水", "兑水 1:10", "每日一次", -30, -5, "finished", "换羽期营养补充，已结束"),
        ]
        for name, med, dosage, freq, s_off, e_off, status, note in samples:
            pid = pet_of.get(name)
            if pid is None:
                continue
            conn.execute(
                "INSERT INTO medications(pet_id,name,dosage,frequency,start_date,end_date,status,note)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (pid, med, dosage, freq, _d(s_off), _d(e_off) if e_off is not None else None, status, note))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- 花费记账

def _exp_rows(sql: str, args: tuple) -> list[dict]:
    """expenses 行 → 字典（带分类标签与宠物名/头像；pet_id 可空=家庭共同）。"""
    conn = get_conn()
    try:
        rows = conn.execute(sql, args).fetchall()
        name_map = {r["id"]: dict(r) for r in conn.execute("SELECT id,name,type,avatar FROM pets")}
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["category_label"] = EXPENSE_CATEGORIES.get(d["category"], d["category"])
        pet = name_map.get(d.get("pet_id"))
        d["pet_name"] = pet["name"] if pet else None
        d["pet_avatar"] = (pet.get("avatar") if pet else None) or None
        d["amount"] = round(d["amount"], 2)
        out.append(d)
    return out


def list_expenses(year: int | None = None, month: int | None = None,
                  pet_id: int | None = None, category: str | None = None,
                  limit: int | None = None) -> list[dict]:
    """花费流水：年/月（本地时区）、宠物、分类可组合筛选，日期倒序。"""
    where, args = [], []
    if year:
        where.append("substr(e.date,1,4)=?"); args.append(str(year))
    if month:
        where.append("substr(e.date,6,2)=?"); args.append(f"{month:02d}")
    if pet_id:
        where.append("e.pet_id=?"); args.append(pet_id)
    if category and category in EXPENSE_CATEGORIES:
        where.append("e.category=?"); args.append(category)
    sql = ("SELECT e.* FROM expenses e"
           + (" WHERE " + " AND ".join(where) if where else "")
           + " ORDER BY e.date DESC, e.id DESC")
    if limit:
        sql += f" LIMIT {int(limit)}"
    return _exp_rows(sql, tuple(args))


def expense_summary(year: int | None = None, month: int | None = None,
                    pet_id: int | None = None, category: str | None = None) -> dict:
    """聚合：合计金额/笔数 + 分类合计 + 按宠物合计（浮点统一 round(,2)）。
    三条查询共用一个 WHERE，列名统一带 e. 前缀，避免 by_pet 的 JOIN 将来撞上 pets 同名列。"""
    where, args = [], []
    if year:
        where.append("substr(e.date,1,4)=?"); args.append(str(year))
    if month:
        where.append("substr(e.date,6,2)=?"); args.append(f"{month:02d}")
    if pet_id:
        where.append("e.pet_id=?"); args.append(pet_id)
    if category and category in EXPENSE_CATEGORIES:
        where.append("e.category=?"); args.append(category)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    conn = get_conn()
    try:
        total, count = conn.execute(
            f"SELECT COALESCE(SUM(e.amount),0), COUNT(*) FROM expenses e{clause}", args).fetchone()
        by_cat = conn.execute(
            f"SELECT e.category, SUM(e.amount) AS s FROM expenses e{clause} GROUP BY e.category", args).fetchall()
        by_pet = conn.execute(
            f"SELECT e.pet_id, COALESCE(p.name,'家庭共同') AS pet_name, SUM(e.amount) AS s"
            f" FROM expenses e LEFT JOIN pets p ON p.id = e.pet_id{clause}"
            f" GROUP BY e.pet_id ORDER BY s DESC", args).fetchall()
    finally:
        conn.close()
    return {
        "total": round(total or 0, 2),
        "count": count,
        "by_category": {r["category"]: round(r["s"] or 0, 2) for r in by_cat},
        "by_pet": [{"pet_id": r["pet_id"], "pet_name": r["pet_name"],
                    "total": round(r["s"] or 0, 2)} for r in by_pet],
    }


def get_expense(exp_id: int) -> dict | None:
    rows = _exp_rows("SELECT e.* FROM expenses e WHERE e.id=?", (exp_id,))
    return rows[0] if rows else None


def add_expense(data: dict) -> dict | None:
    """pet_id 为空/0 表示家庭共同支出；宠物 id 非空时必须存在（外键防孤儿）。"""
    pid = data.get("pet_id") or None
    if pid and get_pet(pid) is None:
        return None
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO expenses(pet_id,date,category,amount,note) VALUES(?,?,?,?,?)",
            (pid, data.get("date") or today_str(), data.get("category") or "other",
             round(float(data["amount"]), 2), (data.get("note") or "").strip()))
        conn.commit()
        return get_expense(cur.lastrowid)
    finally:
        conn.close()


def update_expense(exp_id: int, data: dict) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM expenses WHERE id=?", (exp_id,)).fetchone()
        if row is None:
            return None
        # pet_id 约定：字段缺省=保持；0/None=转家庭共同（Pydantic int|None 不接受空串，前端清空传 0）
        if "pet_id" in data:
            pid = data["pet_id"] or None
            if pid and get_pet(pid) is None:
                return None
        else:
            pid = row["pet_id"]
        fields = {
            "pet_id": pid,
            "date": data.get("date") or row["date"],
            "category": data.get("category") if data.get("category") in EXPENSE_CATEGORIES else row["category"],
            "amount": round(float(data["amount"]), 2) if data.get("amount") is not None else round(row["amount"], 2),
            "note": (data.get("note") if data.get("note") is not None else row["note"]) or "",
        }
        conn.execute(
            "UPDATE expenses SET pet_id=?, date=?, category=?, amount=?, note=? WHERE id=?",
            (fields["pet_id"], fields["date"], fields["category"], fields["amount"], fields["note"], exp_id))
        conn.commit()
        return get_expense(exp_id)
    finally:
        conn.close()


def delete_expense(exp_id: int) -> bool:
    conn = get_conn()
    try:
        cur = conn.execute("DELETE FROM expenses WHERE id=?", (exp_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def seed_expenses() -> None:
    """花费示例（仅当 expenses 表为空；与健康记录/用药呼应：疫苗¥120、外耳炎¥260、耳药水¥45…）。"""
    conn = get_conn()
    try:
        if conn.execute("SELECT COUNT(*) FROM expenses").fetchone()[0] > 0:
            return
        pet_of = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM pets")}
        samples = [
            # (宠物名/None=家庭共同, 相对天数偏移, 分类, 金额, 备注)
            ("可乐", -350, "medical", 120.0, "狂犬疫苗接种"),
            ("可乐", -200, "medical", 480.0, "年度体检套餐"),
            ("可乐", -60, "medical", 85.0, "体内外驱虫滴剂（大宠爱）"),
            ("可乐", -3, "medical", 68.0, "益生菌粉，肠胃调理两周量"),
            ("可乐", -12, "food", 299.0, "狗粮一袋"),
            ("可乐", -2, "supply", 35.0, "磨牙玩具球"),
            ("布丁", -400, "medical", 150.0, "猫三联第三针"),
            ("布丁", -40, "grooming", 120.0, "药浴洗护"),
            ("布丁", -20, "food", 380.0, "减肥猫粮（医嘱限量）"),
            ("布丁", -8, "medical", 260.0, "外耳炎就诊：挂号+上药"),
            ("布丁", -8, "medical", 45.0, "耳药水（就诊开具，滴 7 天）"),
            ("布丁", -5, "food", 46.0, "罐头一周量"),
            ("翠翠", -15, "food", 56.0, "换羽期营养粮+墨鱼骨"),
            ("翠翠", -45, "supply", 42.0, "鸟笼栖木配件"),
            (None, -6, "supply", 89.0, "猫砂+尿垫（可乐布丁共用）"),
        ]
        for name, off, cat, amount, note in samples:
            pid = pet_of.get(name)
            conn.execute(
                "INSERT INTO expenses(pet_id,date,category,amount,note) VALUES(?,?,?,?,?)",
                (pid, _d(off), cat, amount, note))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- 健康日历（零新表，三源聚合）

def calendar_month(year: int, month: int) -> dict:
    """某月每天的健康事项：待办提醒（health_records.next_date，分级 overdue/soon/todo）、
    已做记录（health_records.date）、进行中的用药（medications active 且当天落在 start~end 区间，end 空视为长期）。
    只返回有事项的日期；分级基准是"今天"而非所查月份，翻到未来月份时逾期项仍按今天判定。"""
    last_day = calendar.monthrange(year, month)[1]
    lo, hi = date(year, month, 1).isoformat(), date(year, month, last_day).isoformat()
    conn = get_conn()
    try:
        recs = conn.execute(
            "SELECT r.*, p.name AS pet_name, p.avatar AS pet_avatar, p.type AS pet_type"
            " FROM health_records r JOIN pets p ON p.id = r.pet_id"
            " WHERE (r.next_date BETWEEN ? AND ?) OR (r.date BETWEEN ? AND ?)"
            " ORDER BY r.date DESC, r.id DESC", (lo, hi, lo, hi)).fetchall()
        meds = conn.execute(
            "SELECT m.*, p.name AS pet_name FROM medications m JOIN pets p ON p.id = m.pet_id"
            " WHERE m.status='active'"
            " AND (m.start_date IS NULL OR m.start_date = '' OR m.start_date <= ?)"
            " AND (m.end_date IS NULL OR m.end_date = '' OR m.end_date >= ?)"
            " ORDER BY m.pet_id, m.start_date", (hi, lo)).fetchall()
    finally:
        conn.close()

    days: dict[str, dict] = {}
    def bucket(d: str) -> dict:
        return days.setdefault(d, {"reminders": [], "records": [], "meds": []})

    for r in recs:
        r = dict(r)
        base = {"record_id": r["id"], "pet_id": r["pet_id"], "pet_name": r["pet_name"],
                "pet_avatar": r["pet_avatar"], "type": r["type"],
                "type_label": RECORD_TYPES.get(r["type"], r["type"]), "title": r["title"]}
        if r["next_date"] and lo <= r["next_date"] <= hi:
            left = days_until(r["next_date"])
            rule = r["repeat_rule"] or ""
            bucket(r["next_date"])["reminders"].append({
                **base, "next_date": r["next_date"], "days_left": left, "overdue": left < 0,
                "level": "overdue" if left < 0 else ("soon" if left <= 7 else "todo"),
                "repeat_rule": rule, "repeat_label": REPEAT_RULES.get(rule, "") if rule else ""})
        if r["date"] and lo <= r["date"] <= hi:
            bucket(r["date"])["records"].append({**base, "date": r["date"], "note": r.get("note") or ""})

    for m in meds:
        m = dict(m)
        start = max(m["start_date"] or lo, lo)
        end = min(m["end_date"] or hi, hi)
        try:
            cur = datetime.strptime(start, "%Y-%m-%d").date()
            stop = datetime.strptime(end, "%Y-%m-%d").date()
        except ValueError:
            continue          # 老库脏日期：跳过这一条，不让整月日历 500
        item = {"id": m["id"], "pet_id": m["pet_id"], "pet_name": m["pet_name"], "name": m["name"],
                "dosage": m.get("dosage") or "", "frequency": m.get("frequency") or "",
                "start_date": m["start_date"], "end_date": m.get("end_date")}
        while cur <= stop:
            bucket(cur.isoformat())["meds"].append(item)
            cur += timedelta(days=1)

    level_rank = {"overdue": 0, "soon": 1, "todo": 2}
    for d in days.values():
        d["reminders"].sort(key=lambda x: (level_rank[x["level"]], x["days_left"]))
    return {
        "year": year, "month": month, "first": lo, "last": hi,
        "days": dict(sorted(days.items())),
        "summary": {
            "reminders": sum(len(d["reminders"]) for d in days.values()),
            "records": sum(len(d["records"]) for d in days.values()),
            "med_days": sum(1 for d in days.values() if d["meds"]),
        },
    }


# ---------------------------------------------------------------- 饮食日志

FEEDING_FIELDS = ("date", "food_type", "amount", "note")


def _feed_dict(row) -> dict:
    d = dict(row)
    d["food_type"] = d.get("food_type") or "other"
    d["type_label"] = FEEDING_TYPES.get(d["food_type"], d["food_type"])
    return d


def list_feeding_logs(pet_id: int, limit: int | None = None) -> list[dict]:
    """某宠物饮食流水，日期倒序（同日按录入顺序倒序）。"""
    conn = get_conn()
    try:
        sql = "SELECT * FROM feeding_logs WHERE pet_id=? ORDER BY date DESC, id DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [_feed_dict(r) for r in conn.execute(sql, (pet_id,)).fetchall()]
    finally:
        conn.close()


def feeding_summary(pet_id: int) -> dict:
    """今日顿数 + 本周（周一起算）次数 + 近 30 天类型分布，供页签小结卡与 AI 分析。"""
    today = date.today()
    week_start = (today - timedelta(days=today.weekday())).isoformat()
    month_ago = (today - timedelta(days=30)).isoformat()
    conn = get_conn()
    try:
        today_n = conn.execute("SELECT COUNT(*) FROM feeding_logs WHERE pet_id=? AND date=?",
                               (pet_id, today.isoformat())).fetchone()[0]
        week_n = conn.execute("SELECT COUNT(*) FROM feeding_logs WHERE pet_id=? AND date>=?",
                              (pet_id, week_start)).fetchone()[0]
        by_type = conn.execute(
            "SELECT food_type, COUNT(*) AS n FROM feeding_logs WHERE pet_id=? AND date>=? GROUP BY food_type",
            (pet_id, month_ago)).fetchall()
    finally:
        conn.close()
    return {"today": today_n, "week": week_n,
            "by_type_30d": {r["food_type"]: r["n"] for r in by_type}}


def get_feeding_log(log_id: int) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM feeding_logs WHERE id=?", (log_id,)).fetchone()
        return _feed_dict(row) if row else None
    finally:
        conn.close()


def add_feeding_log(pet_id: int, data: dict) -> dict | None:
    if get_pet(pet_id) is None:
        return None
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO feeding_logs(pet_id,date,food_type,amount,note) VALUES(?,?,?,?,?)",
            (pet_id, data.get("date") or today_str(),
             data.get("food_type") if data.get("food_type") in FEEDING_TYPES else "other",
             (data.get("amount") or "").strip(), (data.get("note") or "").strip()))
        conn.commit()
        return get_feeding_log(cur.lastrowid)
    finally:
        conn.close()


def update_feeding_log(log_id: int, data: dict) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM feeding_logs WHERE id=?", (log_id,)).fetchone()
        if row is None:
            return None
        fields = {k: v for k, v in data.items() if k in FEEDING_FIELDS and v is not None}
        if "food_type" in fields and fields["food_type"] not in FEEDING_TYPES:
            fields.pop("food_type")
        if "date" in fields and not fields["date"]:
            fields.pop("date")
        if fields:
            conn.execute("UPDATE feeding_logs SET " + ",".join(f"{k}=?" for k in fields) + " WHERE id=?",
                         list(fields.values()) + [log_id])
            conn.commit()
        return get_feeding_log(log_id)
    finally:
        conn.close()


def delete_feeding_log(log_id: int) -> bool:
    conn = get_conn()
    try:
        cur = conn.execute("DELETE FROM feeding_logs WHERE id=?", (log_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def seed_feeding() -> None:
    """饮食示例（仅当表为空；与体重/用药呼应：可乐减肥粮过渡、布丁罐头限量、翠翠换羽期营养粮）。"""
    conn = get_conn()
    try:
        if conn.execute("SELECT COUNT(*) FROM feeding_logs").fetchone()[0] > 0:
            return
        pet_of = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM pets")}
        samples = [
            # (宠物名, 相对天数, 类型, 份量, 备注)
            ("可乐", 0, "kibble", "70 g", "减肥粮过渡第 5 天，早餐吃完"),
            ("可乐", -1, "kibble", "70 g", "晚餐剩了一点"),
            ("可乐", -1, "treat", "2 块", "训练奖励，冻干鸡肉"),
            ("可乐", -2, "kibble", "80 g", "拌益生菌粉一起吃"),
            ("可乐", -3, "kibble", "80 g", ""),
            ("可乐", -4, "raw", "1 块", "生骨肉试吃，排便正常"),
            ("布丁", 0, "wet", "半罐", "罐头限量，医嘱减肥"),
            ("布丁", -1, "kibble", "40 g", "减肥猫粮"),
            ("布丁", -1, "wet", "半罐", ""),
            ("布丁", -3, "kibble", "40 g", "食欲一般，外耳炎滴药后有点闹"),
            ("布丁", -5, "treat", "1 条", "猫条，安抚"),
            ("翠翠", 0, "kibble", "1 小勺", "换羽期营养粮"),
            ("翠翠", -2, "other", "少量", "墨鱼骨补钙"),
        ]
        for name, off, ft, amt, note in samples:
            pid = pet_of.get(name)
            if pid is None:
                continue
            conn.execute("INSERT INTO feeding_logs(pet_id,date,food_type,amount,note) VALUES(?,?,?,?,?)",
                         (pid, _d(off), ft, amt, note))
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    init_and_seed()
    print("DB ready:", DB_PATH)
    print("pets:", len(list_pets()), "| reminders:", len(compute_reminders()))
