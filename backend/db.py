# -*- coding: utf-8 -*-
"""db.py — SQLite 数据层：建库、示例数据、CRUD、提醒计算。

约定：
- 每请求新建连接（sqlite3 线程安全模式下 check_same_thread=False）。
- 日期统一存 'YYYY-MM-DD' 字符串。
"""
import calendar
import json
import os
import pathlib
import re
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
# 药品剂型（药箱页签与 AI 工具共用）
MED_FORMS = {"tablet": "药片", "capsule": "胶囊", "liquid": "口服液", "drops": "滴剂",
             "ointment": "外用", "spray": "喷剂", "injection": "针剂", "other": "其他"}


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
  image TEXT,
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
CREATE TABLE IF NOT EXISTS medbox_items(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  form TEXT DEFAULT 'other',
  spec TEXT DEFAULT '',
  qty REAL,
  unit TEXT DEFAULT '',
  expiry_date TEXT,
  opened_date TEXT,
  open_period_days INTEGER DEFAULT 90,
  location TEXT DEFAULT '',
  purpose TEXT DEFAULT '',
  created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS medbox_pets(
  item_id INTEGER NOT NULL REFERENCES medbox_items(id) ON DELETE CASCADE,
  pet_id INTEGER NOT NULL REFERENCES pets(id) ON DELETE CASCADE,
  PRIMARY KEY(item_id, pet_id)
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
CREATE INDEX IF NOT EXISTS idx_medbox_pets ON medbox_pets(pet_id, item_id);
CREATE TABLE IF NOT EXISTS trash(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  rows_json TEXT NOT NULL,
  deleted_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS coach_tasks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  week TEXT NOT NULL,
  dim TEXT NOT NULL,
  title TEXT NOT NULL,
  link_view TEXT DEFAULT '',
  status TEXT DEFAULT 'open',
  created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_coach_week ON coach_tasks(week, status);
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
        # 增量迁移：老库 health_records 补 repeat_rule / image 列（保留数据）
        rec_cols = [r["name"] for r in conn.execute("PRAGMA table_info(health_records)").fetchall()]
        if rec_cols and "repeat_rule" not in rec_cols:
            conn.execute("ALTER TABLE health_records ADD COLUMN repeat_rule TEXT DEFAULT ''")
        if rec_cols and "image" not in rec_cols:
            conn.execute("ALTER TABLE health_records ADD COLUMN image TEXT")
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
            "INSERT INTO health_records(pet_id,type,date,title,note,next_date,repeat_rule,image)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (pet_id, data.get("type"), data.get("date") or today_str(),
             data.get("title"), data.get("note"), data.get("next_date") or None,
             data.get("repeat_rule") or "", data.get("image") or None))
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
            "UPDATE health_records SET type=?, date=?, title=?, note=?, next_date=?, repeat_rule=?, image=?"
            " WHERE id=?",
            (cur_type, cur_date or today_str(),
             data.get("title") if data.get("title") is not None else row["title"],
             data.get("note") if data.get("note") is not None else row["note"],
             data.get("next_date") or None, repeat,
             data["image"] if data.get("image") is not None else row["image"], record_id))
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
                  "health_records", "medications", "memories", "expenses", "feeding_logs",
                  "medbox_pets", "medbox_items", "coach_tasks", "trash", "pets"):
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


# ---------------------------------------------------------------- 撤销删除（回收站）
# 删除前把整行序列化成 JSON 存进 trash 表；撤销时按原 id 回插（含子表级联），失败静默不影响删除本身。
# 表名只来自本模块硬编码常量，绝不接受外部输入，字符串拼接 SQL 在此是安全的。

TRASHABLE = {
    "record":    {"table": "health_records"},
    "memory":    {"table": "memories"},
    "medication": {"table": "medications"},
    "expense":   {"table": "expenses"},
    "diet":      {"table": "feeding_logs"},
    # 药箱：删条目连带适用关联进快照；删宠物时 medbox_pets 是 CASCADE 子表，回插即可（medbox_items 本体不消失）
    "medbox":    {"table": "medbox_items", "children": [("medbox_pets", "item_id")]},
    "session":   {"table": "chat_sessions", "children": [("chat_history", "session_id")]},
    # pets 级联删 CASCADE 子表；memories/expenses 是 SET NULL 不消失——撤销时 relink 回宠物而不是回插
    "pet":       {"table": "pets", "children": [
        ("health_records", "pet_id"), ("weight_logs", "pet_id"), ("medications", "pet_id"),
        ("feeding_logs", "pet_id"), ("medbox_pets", "pet_id")],
        "relink": [("expenses", "pet_id"), ("memories", "pet_id")]},
}


def snapshot_for_trash(kind: str, row_id: int) -> str | None:
    """序列化实体（含子表行）为回收站 payload；实体不存在返回 None。"""
    spec = TRASHABLE.get(kind)
    if not spec:
        return None
    conn = get_conn()
    try:
        row = conn.execute(f"SELECT * FROM {spec['table']} WHERE id=?", (row_id,)).fetchone()
        if row is None:
            return None
        payload = {"kind": kind, "rows": [{"table": spec["table"], "data": dict(row)}]}
        for child_table, fk in spec.get("children", []):
            kids = [dict(r) for r in conn.execute(
                f"SELECT * FROM {child_table} WHERE {fk}=?", (row_id,)).fetchall()]
            if kids:
                payload["rows"].append({"table": child_table, "data": kids})
        relinks = []
        for rel_table, rel_fk in spec.get("relink", []):
            ids = [r["id"] for r in conn.execute(
                f"SELECT id FROM {rel_table} WHERE {rel_fk}=?", (row_id,)).fetchall()]
            if ids:
                relinks.append({"table": rel_table, "fk": rel_fk, "owner": row_id, "ids": ids})
        if relinks:
            payload["relink"] = relinks
        return json.dumps(payload, ensure_ascii=False)
    finally:
        conn.close()


def trash_put(kind: str, row_id: int) -> int | None:
    """删除前调用：存快照返回 trash_id。快照失败返回 None（不阻断删除）。"""
    payload = snapshot_for_trash(kind, row_id)
    if payload is None:
        return None
    conn = get_conn()
    try:
        cur = conn.execute("INSERT INTO trash(kind, rows_json) VALUES(?, ?)", (kind, payload))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def restore_from_trash(trash_id: int) -> bool:
    """撤销：按原 id 回插全部行（父表在前子表在后，FK 满足），成功删除快照。"""
    conn = get_conn()
    try:
        snap = conn.execute("SELECT rows_json FROM trash WHERE id=?", (trash_id,)).fetchone()
        if snap is None:
            return False
        payload = json.loads(snap["rows_json"])
        for part in payload["rows"]:
            table, data = part["table"], part["data"]
            items = data if isinstance(data, list) else [data]
            for item in items:
                # 必须按原 id 回插：子表行引用父表旧 id，撞号时整笔回滚失败而非留下孤行
                cols = list(item.keys())
                conn.execute(
                    f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                    [item[c] for c in cols])
        # relink：宠物删除时被 SET NULL 的 expenses 等，把外键指回（只认领仍为 NULL 的）
        for rl in payload.get("relink", []):
            marks = ",".join("?" * len(rl["ids"]))
            conn.execute(
                f"UPDATE {rl['table']} SET {rl['fk']}=? WHERE id IN ({marks}) AND {rl['fk']} IS NULL",
                [rl["owner"], *rl["ids"]])
        conn.execute("DELETE FROM trash WHERE id=?", (trash_id,))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def trash_prune(keep: int = 50) -> None:
    """只保留最近 keep 条快照，防回收站无限膨胀。"""
    conn = get_conn()
    try:
        conn.execute("DELETE FROM trash WHERE id NOT IN (SELECT id FROM trash ORDER BY id DESC LIMIT ?)", (keep,))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- 数据库快照备份

BACKUP_KEEP = 14   # 保留最近 14 份快照（启动一次 + 每日一次 ≈ 半个月历史）


def _backup_dir() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "backups")


def backup_db(keep: int = BACKUP_KEEP) -> str | None:
    """VACUUM INTO 生成一致性快照（WAL 下同样安全）到 pets.db 同级 backups/，
    按文件名清理旧快照只留最近 keep 份。返回快照路径；库文件尚不存在时返回 None。"""
    if not os.path.exists(DB_PATH):
        return None
    backup_dir = _backup_dir()
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = os.path.join(backup_dir, f"pets-{stamp}.db")
    if os.path.exists(target):   # 同秒重复触发 → 追加微秒避免 VACUUM INTO 目标已存在报错
        target = os.path.join(backup_dir, f"pets-{stamp}{datetime.now().strftime('%f')}.db")
    conn = get_conn()
    try:
        conn.execute("VACUUM INTO ?", (target,))
    finally:
        conn.close()
    snaps = sorted(f for f in os.listdir(backup_dir) if f.startswith("pets-") and f.endswith(".db"))
    for old in (snaps[:-keep] if keep > 0 else []):
        try:
            os.remove(os.path.join(backup_dir, old))
        except OSError:
            pass
    return target


def today_backup_done() -> bool:
    """backups/ 里是否已有今天的快照（按文件名前缀判断，供每日备份循环避免重复）。"""
    prefix = f"pets-{datetime.now().strftime('%Y%m%d')}"
    return os.path.isdir(_backup_dir()) and any(
        f.startswith(prefix) for f in os.listdir(_backup_dir()))


# 快照文件名白名单：pets-YYYYMMDD-HHMMSS(.微秒).db——恢复端点只认这个形态，杜绝路径穿越
VALID_BACKUP_RE = re.compile(r"^pets-\d{8}-\d{6}(?:\d{6})?\.db$")


def list_backups() -> list[dict]:
    """快照列表（新→旧）：name / 可读时间 / 大小 KB。"""
    d = _backup_dir()
    if not os.path.isdir(d):
        return []
    out = []
    for f in sorted(os.listdir(d), reverse=True):
        if not VALID_BACKUP_RE.match(f):
            continue
        try:
            st = os.stat(os.path.join(d, f))
        except OSError:
            continue
        stamp = f[5:-3]   # 去掉 pets- 与 .db
        human = f"{stamp[0:4]}-{stamp[4:6]}-{stamp[6:8]} {stamp[9:11]}:{stamp[11:13]}:{stamp[13:15]}"
        out.append({"name": f, "stamp": human, "size_kb": round(st.st_size / 1024, 1)})
    return out


def restore_backup(name: str) -> dict:
    """用快照覆盖当前库。安全链：名称白名单 → 快照可读且含 pets 表 → 先把当前库快照一份
    （pre_restore 标记防混淆，仍走 pets-* 命名）→ backup API 逐页写回活库（WAL 下安全，无需重启）。"""
    if not VALID_BACKUP_RE.match(name or ""):
        return {"error": "快照名称不合法"}
    src_path = os.path.join(_backup_dir(), name)
    if not os.path.isfile(src_path):
        return {"error": "快照不存在"}
    src = sqlite3.connect(pathlib.Path(src_path).as_uri() + "?mode=ro", uri=True)
    try:
        tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "pets" not in tables:
            return {"error": "快照文件不是有效的宠物库"}
        safety = backup_db()   # 恢复前先保住当前数据
        dst = get_conn()
        try:
            src.backup(dst)
            dst.commit()
        finally:
            dst.close()
    except Exception as e:
        return {"error": f"恢复失败：{type(e).__name__}"}
    finally:
        src.close()
    return {"ok": True, "safety": os.path.basename(safety) if safety else None}


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
    已做记录（health_records.date）、进行中的用药（medications active 且当天落在 start~end 区间，end 空视为长期）、
    药箱到期事项（medbox_items，effective_deadline 落在当天，分级 overdue/today/soon，整条只标 1 天）。
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
        return days.setdefault(d, {"reminders": [], "records": [], "meds": [], "expiry": []})

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

    month_first = date(year, month, 1)
    month_last = date(year, month, calendar.monthrange(year, month)[1])
    for m in meds:
        m = dict(m)
        start = max(m["start_date"] or lo, lo)
        end = min(m["end_date"] or hi, hi)
        freq = m.get("frequency") or ""
        try:
            d0 = datetime.strptime(m["start_date"], "%Y-%m-%d").date() if m["start_date"] else None
            stop = datetime.strptime(end, "%Y-%m-%d").date()
        except ValueError:
            continue          # 老库脏日期：跳过这一条，不让整月日历 500
        item = {"id": m["id"], "pet_id": m["pet_id"], "pet_name": m["pet_name"], "name": m["name"],
                "dosage": m.get("dosage") or "", "frequency": freq,
                "start_date": m["start_date"], "end_date": m.get("end_date")}
        if "每月" in freq and d0:
            # 「每月一次」不是每天都要用：只标每月该用的那天（与 start 同日号，钳制月末）
            cand = date(year, month, min(d0.day, month_last.day))
            if d0 <= cand <= stop:
                bucket(cand.isoformat())["meds"].append(item)
        elif "每周" in freq and d0:
            # 「每周一次」：从 start 起每 7 天一次，只标落在查询月内的
            cur = d0
            while cur < month_first:
                cur += timedelta(days=7)
            while cur <= stop and cur <= month_last:
                bucket(cur.isoformat())["meds"].append(item)
                cur += timedelta(days=7)
        else:
            # 每日/每日两次/未填频次/无开始日期 → 疗程内每天都要用，逐日展开
            cur = datetime.strptime(start, "%Y-%m-%d").date()
            while cur <= stop:
                bucket(cur.isoformat())["meds"].append(item)
                cur += timedelta(days=1)

    # 第四源：药箱到期（list_medbox 原始行批量算 effective_deadline，落在所查月份的按日标记）
    for it in list_medbox():
        dl = it.get("effective_deadline")
        if not dl or not (lo <= dl <= hi) or it.get("remain_days") is None:
            continue
        level = "overdue" if it["remain_days"] < 0 else ("today" if it["remain_days"] == 0 else "soon")
        bucket(dl)["expiry"].append({"id": it["id"], "name": it["name"], "level": level})

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
            "medbox": sum(len(d["expiry"]) for d in days.values()),
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


# ---------------------------------------------------------------- 药箱
# 家庭常备药库存：有效期与开封后使用期取更早者为截止日（effective_deadline）；
# medbox_pets 多对多关联适用宠物（无关联=全家通用）。到期计算纯函数，不落库。

def _med_date(s) -> date | None:
    """宽容解析 YYYY-MM-DD：空值/脏值返回 None，绝不让列表或日历整页 500。"""
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def medbox_status(item: dict) -> dict:
    """给单条药箱条目追加到期/临期计算字段并返回（就地改）：
    form_label 剂型中文；effective_deadline 截止日（开封药取 min(有效期, 开封日+开封保质期)，
    未开封取有效期，皆空 None）；remain_days 剩余天数（可负）；remain_pct 剩余/总保质期
    钳到 [2,100]（无期限 None，总长≤0 按 1 天算）；opened 是否已开封；
    status 三态 expired/soon/ok（截止已过=expired，≤30 天=soon），未建期限一律 ok。"""
    form = item.get("form") or "other"
    item["form_label"] = MED_FORMS.get(form, form)
    expiry = _med_date(item.get("expiry_date"))
    opened_dt = _med_date(item.get("opened_date"))
    item["opened"] = opened_dt is not None
    try:
        period = int(item.get("open_period_days") or 0)
    except (TypeError, ValueError):
        period = 0
    open_dl = opened_dt + timedelta(days=period) if (opened_dt and period > 0) else None
    deadline, from_open = None, False
    if expiry and open_dl:
        deadline = min(expiry, open_dl); from_open = open_dl < expiry
    elif expiry:
        deadline = expiry
    elif open_dl:
        deadline = open_dl; from_open = True
    item["effective_deadline"] = deadline.isoformat() if deadline else None
    if deadline is None:
        item["remain_days"] = None
        item["remain_pct"] = None
        item["status"] = "ok"
    else:
        remain = (deadline - date.today()).days
        item["remain_days"] = remain
        if from_open:
            total = period          # 截止由开封后使用期决定：总长就是开封保质期
        else:
            anchor = opened_dt or _med_date(item.get("created_at")) or date.today()
            total = (deadline - anchor).days
        if total <= 0:
            total = 1               # 开封日期晚于截止等脏数据：按 1 天算，不除零
        item["remain_pct"] = max(2, min(100, int(round(remain / total * 100))))
        item["status"] = "expired" if remain < 0 else ("soon" if remain <= 30 else "ok")
    item["status_label"] = {"expired": "已过期", "soon": "临期"}.get(item["status"], "可用")
    return item


MEDBOX_FIELDS = ("name", "form", "spec", "qty", "unit", "expiry_date", "opened_date",
                 "open_period_days", "location", "purpose")


def _medbox_pets_of(conn, item_id: int) -> list[dict]:
    """某条药品的适用宠物（id/name/avatar/type）。"""
    return [{"id": r["id"], "name": r["name"], "avatar": r["avatar"], "type": r["type"]}
            for r in conn.execute(
                "SELECT p.id, p.name, p.avatar, p.type FROM medbox_pets mp"
                " JOIN pets p ON p.id = mp.pet_id WHERE mp.item_id=? ORDER BY p.id", (item_id,))]


def list_medbox() -> list[dict]:
    """全部药箱条目（带 pets 与 medbox_status 展开字段），紧急度排序：expired→soon→ok，同级按剩余天数升（无期限最后）。"""
    conn = get_conn()
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM medbox_items").fetchall()]
        for d in rows:
            d["pets"] = _medbox_pets_of(conn, d["id"])
            medbox_status(d)
    finally:
        conn.close()
    rank = {"expired": 0, "soon": 1, "ok": 2}
    rows.sort(key=lambda x: (rank[x["status"]], x["remain_days"] is None,
                             x["remain_days"] if x["remain_days"] is not None else 0))
    return rows


def get_medbox(item_id: int) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM medbox_items WHERE id=?", (item_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["pets"] = _medbox_pets_of(conn, item_id)
    finally:
        conn.close()
    return medbox_status(d)


def add_medbox(data: dict) -> dict | None:
    """新增药箱条目：data 可含 pet_ids:[int]（任一宠物不存在则整体 None）；form 白名单外落 other。"""
    name = (data.get("name") or "").strip()
    if not name:
        return None
    pet_ids = sorted({int(p) for p in (data.get("pet_ids") or [])})
    conn = get_conn()
    try:
        if pet_ids:
            marks = ",".join("?" * len(pet_ids))
            found = {r["id"] for r in conn.execute(f"SELECT id FROM pets WHERE id IN ({marks})", pet_ids)}
            if found != set(pet_ids):
                return None
        cur = conn.execute(
            "INSERT INTO medbox_items(name,form,spec,qty,unit,expiry_date,opened_date,open_period_days,location,purpose)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (name,
             data.get("form") if data.get("form") in MED_FORMS else "other",
             (data.get("spec") or "").strip(),
             None if data.get("qty") is None else float(data["qty"]),
             (data.get("unit") or "").strip(),
             data.get("expiry_date") or None,
             data.get("opened_date") or None,
             int(data.get("open_period_days") or 90),
             (data.get("location") or "").strip(),
             (data.get("purpose") or "").strip()))
        item_id = cur.lastrowid
        for pid in pet_ids:
            conn.execute("INSERT INTO medbox_pets(item_id,pet_id) VALUES(?,?)", (item_id, pid))
        conn.commit()
    finally:
        conn.close()
    return get_medbox(item_id)


def update_medbox(item_id: int, data: dict) -> dict | None:
    """编辑药箱条目：白名单字段给了值才更新（qty 允许显式 null 清空）；
    pet_ids 给定时全删重建关联（含空列表=清空），宠物不存在返回 None。"""
    pet_ids = data.get("pet_ids") if "pet_ids" in data else None
    if pet_ids is not None:
        pet_ids = sorted({int(p) for p in pet_ids})
        conn = get_conn()
        try:
            if pet_ids:
                marks = ",".join("?" * len(pet_ids))
                found = {r["id"] for r in conn.execute(f"SELECT id FROM pets WHERE id IN ({marks})", pet_ids)}
                if found != set(pet_ids):
                    return None
        finally:
            conn.close()
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM medbox_items WHERE id=?", (item_id,)).fetchone()
        if row is None:
            return None
        row = dict(row)
        fields = {k: v for k, v in data.items() if k in MEDBOX_FIELDS and v is not None}
        if "qty" in data and data["qty"] is None:
            fields["qty"] = None
        # 日期同理：前端把空 date input 归一为 null，显式传 null 即清空（否则清不掉还提示成功）
        for dk in ("expiry_date", "opened_date"):
            if dk in data and data[dk] is None:
                fields[dk] = None
        if "qty" in fields and fields["qty"] is not None:
            fields["qty"] = float(fields["qty"])
        if "open_period_days" in fields:
            fields["open_period_days"] = int(fields["open_period_days"])
        if "form" in fields and fields["form"] not in MED_FORMS:
            fields["form"] = "other"
        if "name" in fields:
            fields["name"] = str(fields["name"]).strip() or row["name"]
        for k in ("spec", "unit", "location", "purpose"):
            if k in fields:
                fields[k] = str(fields[k]).strip()
        if fields:
            conn.execute("UPDATE medbox_items SET " + ",".join(f"{k}=?" for k in fields) + " WHERE id=?",
                         list(fields.values()) + [item_id])
        if pet_ids is not None:
            conn.execute("DELETE FROM medbox_pets WHERE item_id=?", (item_id,))
            for pid in pet_ids:
                conn.execute("INSERT INTO medbox_pets(item_id,pet_id) VALUES(?,?)", (item_id, pid))
        conn.commit()
    finally:
        conn.close()
    return get_medbox(item_id)


def delete_medbox(item_id: int) -> bool:
    conn = get_conn()
    try:
        cur = conn.execute("DELETE FROM medbox_items WHERE id=?", (item_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def medbox_attention() -> list[dict]:
    """需要关注的药箱条目（已过期/临期），沿用 list_medbox 的紧急度排序。"""
    return [i for i in list_medbox() if i["status"] != "ok"]


# ---------------------------------------------------------------- AI 巡检规则引擎
# 6 条独立规则，每条各自 try/except：单条坏数据只记 rule_errors，不拖垮整体。
# finding.id 为决定式（规则+实体+当天日期），与 LLM 无关；title/facts 全是规则拼接的事实句。

PATROL_RULES = ("appetite", "weight_gap", "spend", "overdue", "medbox", "weight_delta")


def _patrol_finding(rid: str, rule: str, level: str, title: str, facts: list, link: dict | None) -> dict:
    return {"id": rid, "rule": rule, "level": level, "title": title,
            "ai_text": "", "facts": facts[:4], "link": link}


def _patrol_pet_scan_rules(findings: list, errors: list) -> None:
    """逐宠规则：appetite / weight_gap / weight_delta（各自 try，互不牵连）。"""
    from datetime import timedelta
    today = date.today()
    conn = get_conn()
    try:
        pets = [dict(r) for r in conn.execute("SELECT id, name, created_at FROM pets ORDER BY id").fetchall()]
        for pet in pets:
            pid = pet["id"]
            # ① appetite：近3天喂食次数 vs 此前21天日均×3
            try:
                recent = conn.execute("SELECT COUNT(*) FROM feeding_logs WHERE pet_id=? AND date>=?",
                                      (pid, (today - timedelta(days=2)).isoformat())).fetchone()[0]
                prev = conn.execute("SELECT COUNT(*) FROM feeding_logs WHERE pet_id=? AND date>=? AND date<?",
                                    (pid, (today - timedelta(days=23)).isoformat(),
                                     (today - timedelta(days=2)).isoformat())).fetchone()[0]
                avg = prev / 21.0
                if recent == 0 and avg >= 1:
                    findings.append(_patrol_finding(
                        f"appetite:{pid}:{today.isoformat()}", "appetite", "high",
                        f"{pet['name']} · 近3天没有任何进食记录",
                        [{"k": "近3天喂餐", "v": "0 次"}, {"k": "此前日均", "v": f"{round(avg, 1)} 次/天"}],
                        {"view": "detail", "opts": {"petId": pid}}))
                elif recent >= 1 and avg >= 1 and recent <= avg * 3 * 0.6:
                    pct = round((1 - recent / (avg * 3)) * 100)
                    findings.append(_patrol_finding(
                        f"appetite:{pid}:{today.isoformat()}", "appetite", "warn",
                        f"{pet['name']} · 近3天食欲下降{pct}%",
                        [{"k": "近3天喂餐", "v": f"{recent} 次"},
                         {"k": "此前均值", "v": f"{round(avg, 1)} 次/天"},
                         {"k": "降幅", "v": f"{pct}%"}],
                        {"view": "detail", "opts": {"petId": pid}}))
            except Exception as e:
                errors.append(f"appetite:{pid}: {type(e).__name__}")
            # ② weight_gap：距最新体重 >60 天且养宠 >60 天
            try:
                latest = conn.execute("SELECT date FROM weight_logs WHERE pet_id=? ORDER BY date DESC LIMIT 1",
                                      (pid,)).fetchone()
                created = (pet.get("created_at") or "")[:10]
                if latest and created:
                    gap = days_until(latest["date"])
                    owned = -days_until(created)
                    if gap < -60 and owned > 60:
                        findings.append(_patrol_finding(
                            f"weight_gap:{pid}:{today.isoformat()}", "weight_gap", "warn",
                            f"{pet['name']} · 已 {-gap} 天没有称重",
                            [{"k": "距上次称重", "v": f"{-gap} 天"}, {"k": "上次日期", "v": latest["date"]}],
                            {"view": "detail", "opts": {"petId": pid}}))
            except Exception as e:
                errors.append(f"weight_gap:{pid}: {type(e).__name__}")
            # ⑥ weight_delta：近30天首末差超 10%
            try:
                rows = [dict(r) for r in conn.execute(
                    "SELECT date, weight FROM weight_logs WHERE pet_id=? AND date>=? ORDER BY date ASC",
                    (pid, (today - timedelta(days=29)).isoformat())).fetchall()]
                if len(rows) >= 2 and rows[0]["weight"]:
                    w0, w1 = rows[0]["weight"], rows[-1]["weight"]
                    pct = abs(w1 - w0) / w0 * 100
                    if pct > 10:
                        trend = "上升" if w1 > w0 else "下降"
                        findings.append(_patrol_finding(
                            f"weight_delta:{pid}:{today.isoformat()}", "weight_delta", "warn",
                            f"{pet['name']} · 近30天体重{trend} {round(pct, 1)}%",
                            [{"k": "30天前", "v": f"{w0} kg"}, {"k": "当前", "v": f"{w1} kg"},
                             {"k": "幅度", "v": f"{round(pct, 1)}%"}],
                            {"view": "detail", "opts": {"petId": pid}}))
            except Exception as e:
                errors.append(f"weight_delta:{pid}: {type(e).__name__}")
    finally:
        conn.close()


def patrol_scan() -> dict:
    """AI 巡检规则引擎：食欲骤降 / 久未称重 / 医疗花费异动 / 提醒逾期 / 药箱到期 / 体重波动。
    每条规则独立捕获异常（坏数据记入 rule_errors），findings 按 high→warn→info 排序。"""
    findings: list[dict] = []
    errors: list[str] = []
    today = date.today()
    try:
        _patrol_pet_scan_rules(findings, errors)
    except Exception as e:
        errors.append(f"pet-rules: {type(e).__name__}")
    # ③ spend：本月医疗 vs 上月医疗（3 倍且 ≥100 → warn；上月 0 且本月 ≥300 → info）
    try:
        now = today
        py, pm = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
        cur_med = expense_summary(now.year, now.month)["by_category"].get("medical", 0.0)
        prev_med = expense_summary(py, pm)["by_category"].get("medical", 0.0)
        rid = f"spend:0:{today.isoformat()}"
        if prev_med > 0 and cur_med >= prev_med * 3 and cur_med >= 100:
            findings.append(_patrol_finding(rid, "spend", "warn",
                          f"本月医疗花费异动（上月 ¥{prev_med:.2f} → 本月 ¥{cur_med:.2f}）",
                          [{"k": "本月医疗", "v": f"¥{cur_med:.2f}"},
                           {"k": "上月医疗", "v": f"¥{prev_med:.2f}"}],
                          {"view": "ledger", "opts": {}}))
        elif prev_med == 0 and cur_med >= 300:
            findings.append(_patrol_finding(rid, "spend", "info",
                          f"本月医疗花费 ¥{cur_med:.2f}（上月无医疗支出）",
                          [{"k": "本月医疗", "v": f"¥{cur_med:.2f}"}, {"k": "上月医疗", "v": "¥0.00"}],
                          {"view": "ledger", "opts": {}}))
    except Exception as e:
        errors.append(f"spend: {type(e).__name__}")
    # ④ overdue：逾期最久分级（>14 天 high；>7 天 warn；1~7 天 info 汇总一条）
    try:
        overdue = [r for r in compute_reminders() if r["overdue"]]
        if overdue:
            worst = max(-r["days_left"] for r in overdue)
            level = "high" if worst > 14 else ("warn" if worst > 7 else "info")
            facts = [{"k": "逾期事项", "v": f"{len(overdue)} 项"},
                     {"k": "最久逾期", "v": f"{worst} 天"}]
            for r in overdue[:2]:
                facts.append({"k": f"{r['pet_name']}·{r['title']}", "v": f"逾期 {-r['days_left']} 天"})
            findings.append(_patrol_finding(
                f"overdue:0:{today.isoformat()}", "overdue", level,
                f"{len(overdue)} 项提醒已逾期（最久 {worst} 天）",
                facts, {"view": "reminders", "opts": {}}))
    except Exception as e:
        errors.append(f"overdue: {type(e).__name__}")
    # ⑤ medbox：过期 high / 仅临期 warn
    try:
        attention = medbox_attention()
        if attention:
            has_expired = any(a["status"] == "expired" for a in attention)
            facts = []
            for a in attention[:3]:
                rd = a.get("remain_days")
                facts.append({"k": a["name"],
                              "v": (f"已过期 {-rd} 天" if rd is not None and rd < 0
                                    else f"剩 {rd} 天" if rd is not None else "临期")})
            findings.append(_patrol_finding(
                f"medbox:0:{today.isoformat()}", "medbox",
                "high" if has_expired else "warn",
                f"药箱 {len(attention)} 种药品需关注"
                + ("（含已过期）" if has_expired else "（临期）"),
                facts, {"view": "medbox", "opts": {}}))
    except Exception as e:
        errors.append(f"medbox: {type(e).__name__}")
    rank = {"high": 0, "warn": 1, "info": 2}
    findings.sort(key=lambda f: (rank.get(f["level"], 3), f["id"]))
    return {"findings": findings, "scanned": len(PATROL_RULES), "rule_errors": errors}


# ---------------------------------------------------------------- 主人养成教练（规则层）
# 分数与任务全部决定式计算，LLM 只在 agent.coach_letter 写口吻文案。
# 每维 0–100，综合分加权平均；扣分原因可追溯到具体宠物/日期。

COACH_DIM_DEFS = (
    ("weigh", "称重规律", 0.15),
    ("overdue", "提醒响应", 0.20),
    ("records", "档案完整", 0.15),
    ("meds", "用药依从", 0.15),
    ("medbox", "药箱卫生", 0.10),
    ("diet", "饮食记录", 0.10),
    ("ledger", "记账习惯", 0.10),
    ("patrol", "巡检清零", 0.05),
)


def coach_week_id(d: date | None = None) -> str:
    """ISO 周标识（周一为一周起点），如 2026-W39。"""
    d = d or date.today()
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _coach_clamp(n: float) -> int:
    return max(0, min(100, int(round(n))))


def coach_dims() -> list[dict]:
    """八维养育分：score 0–100 + note 扣分说明（空=满分）。"""
    today = date.today()
    conn = get_conn()
    dims: list[dict] = []
    try:
        pets = [dict(r) for r in conn.execute("SELECT id, name, created_at FROM pets ORDER BY id").fetchall()]
        # ① 称重：各宠距最近称重的最大间隔
        worst_gap, worst_name, last_date = 0, "", ""
        for p in pets:
            row = conn.execute(
                "SELECT date FROM weight_logs WHERE pet_id=? ORDER BY date DESC LIMIT 1", (p["id"],)
            ).fetchone()
            if not row:
                gap = 999
                ld = "从未"
            else:
                gap = max(0, -days_until(row["date"]))
                ld = row["date"]
            if gap > worst_gap:
                worst_gap, worst_name, last_date = gap, p["name"], ld
        if not pets:
            w_sc, w_note = 100, ""
        elif worst_gap <= 30:
            w_sc, w_note = 100, ""
        elif worst_gap <= 60:
            w_sc, w_note = 70, f"{worst_name}距上次称重 {worst_gap} 天（{last_date}）"
        elif worst_gap >= 999:
            w_sc, w_note = 40, f"{worst_name}还没有称重记录"
        else:
            w_sc = _coach_clamp(55 - (worst_gap - 60) * 0.5)
            w_note = f"{worst_name}距上次称重 {worst_gap} 天（{last_date}）"
        dims.append({"key": "weigh", "label": "称重规律", "score": w_sc, "note": w_note})

        # ② 提醒响应：逾期项
        rem = compute_reminders(within_days=3650)
        od = [r for r in rem if r.get("overdue")]
        if not od:
            o_sc, o_note = 100, ""
        else:
            worst = min(int(r.get("days_left") or 0) for r in od)
            o_sc = _coach_clamp(100 - len(od) * 18 - max(0, -worst) * 0.4)
            o_note = f"{len(od)} 项逾期，最久已拖 {-worst} 天"
        dims.append({"key": "overdue", "label": "提醒响应", "score": o_sc, "note": o_note})

        # ③ 档案完整：有宠物却从未体检/驱虫
        missing = []
        for p in pets:
            n_chk = conn.execute(
                "SELECT COUNT(*) FROM health_records WHERE pet_id=? AND type IN ('checkup','deworm','vaccine')",
                (p["id"],)).fetchone()[0]
            if n_chk == 0:
                missing.append(p["name"])
        if not pets:
            r_sc, r_note = 100, ""
        elif not missing:
            r_sc, r_note = 100, ""
        else:
            r_sc = _coach_clamp(100 - len(missing) * 25)
            r_note = "缺少健康记录：" + "、".join(missing[:3])
        dims.append({"key": "records", "label": "档案完整", "score": r_sc, "note": r_note})

        # ④ 用药依从：在用且已超 end_date
        over_meds = []
        for m in active_medications_all():
            ed = m.get("end_date")
            if ed and days_until(ed) < 0:
                over_meds.append(f"{m.get('pet_name') or ''}{m['name']}".strip())
        if not over_meds:
            m_sc, m_note = 100, ""
        else:
            m_sc = _coach_clamp(100 - len(over_meds) * 20)
            m_note = "疗程已超仍标记在用：" + "、".join(over_meds[:3])
        dims.append({"key": "meds", "label": "用药依从", "score": m_sc, "note": m_note})

        # ⑤ 药箱卫生
        att = [i for i in list_medbox() if i.get("status") != "ok"]
        exp = [i for i in att if i.get("status") == "expired"]
        if not att:
            b_sc, b_note = 100, ""
        elif exp:
            b_sc = _coach_clamp(100 - len(exp) * 22 - (len(att) - len(exp)) * 8)
            b_note = "有过期药品：" + "、".join(x["name"] for x in exp[:3])
        else:
            b_sc = _coach_clamp(100 - len(att) * 12)
            b_note = "临期药品：" + "、".join(x["name"] for x in att[:3])
        dims.append({"key": "medbox", "label": "药箱卫生", "score": b_sc, "note": b_note})

        # ⑥ 饮食记录：本周是否有流水
        monday = today - timedelta(days=today.weekday())
        n_feed = conn.execute(
            "SELECT COUNT(*) FROM feeding_logs WHERE date>=?", (monday.isoformat(),)
        ).fetchone()[0]
        if n_feed >= 5:
            d_sc, d_note = 100, ""
        elif n_feed >= 2:
            d_sc, d_note = 75, f"本周仅 {n_feed} 条饮食记录"
        elif n_feed == 1:
            d_sc, d_note = 55, "本周只有 1 条饮食记录"
        else:
            d_sc, d_note = 35, "本周还没有饮食记录"
        dims.append({"key": "diet", "label": "饮食记录", "score": d_sc, "note": d_note})

        # ⑦ 记账：本月是否有流水
        y, mo = today.year, today.month
        n_exp = conn.execute(
            "SELECT COUNT(*) FROM expenses WHERE strftime('%Y', date)=? AND strftime('%m', date)=?",
            (f"{y:04d}", f"{mo:02d}")).fetchone()[0]
        if n_exp >= 3:
            g_sc, g_note = 100, ""
        elif n_exp >= 1:
            g_sc, g_note = 80, f"本月仅 {n_exp} 笔流水"
        else:
            g_sc, g_note = 50, "本月还没有记账"
        dims.append({"key": "ledger", "label": "记账习惯", "score": g_sc, "note": g_note})

        # ⑧ 巡检清零：高/关注级发现数（与 patrol 同源扫描，这里直接数 level）
        scan = patrol_scan()
        bad = [f for f in scan["findings"] if f.get("level") in ("high", "warn")]
        if not bad:
            p_sc, p_note = 100, ""
        else:
            p_sc = _coach_clamp(100 - len(bad) * 15)
            p_note = f"{len(bad)} 项巡检发现待处理"
        dims.append({"key": "patrol", "label": "巡检清零", "score": p_sc, "note": p_note})
    finally:
        conn.close()
    return dims


def _coach_task_templates(dims: list[dict], pets: list[dict]) -> list[dict]:
    """从低分维派生任务文案（够得着，不堆理想清单）。"""
    by = {d["key"]: d for d in dims}
    out: list[dict] = []
    pet = pets[0]["name"] if pets else "宠物"

    def push(dim: str, title: str, view: str = "") -> None:
        out.append({"dim": dim, "title": title, "link_view": view})

    if by["weigh"]["score"] < 85:
        push("weigh", f"给{pet}称一次体重", "library")
    if by["overdue"]["score"] < 85:
        push("overdue", "清一清到期提醒（先做逾期）", "reminders")
    if by["records"]["score"] < 85:
        push("records", f"给{pet}补一条健康记录（体检/驱虫）", "records")
    if by["meds"]["score"] < 85:
        push("meds", "核对用药疗程，结束已吃完的", "library")
    if by["medbox"]["score"] < 85:
        push("medbox", "清理药箱过期/临期药品", "medbox")
    if by["diet"]["score"] < 85:
        push("diet", "这周记满 3 天饮食", "library")
    if by["ledger"]["score"] < 85:
        push("ledger", "补几笔本月花销流水", "ledger")
    if by["patrol"]["score"] < 85:
        push("patrol", "处理巡检里的高优先发现", "patrol")
    # 低分优先，最多 3 张
    out.sort(key=lambda t: by[t["dim"]]["score"])
    return out[:3]


def coach_list_tasks(week: str) -> list[dict]:
    conn = get_conn()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, week, dim, title, link_view, status, created_at FROM coach_tasks"
            " WHERE week=? ORDER BY id", (week,)).fetchall()]
    finally:
        conn.close()
    return rows


def coach_ensure_tasks(dims: list[dict], week: str) -> list[dict]:
    tasks = coach_list_tasks(week)
    if tasks:
        return tasks  # 本周已有任务（含已完成）不重复派发
    pets = list_pets()
    tpl = _coach_task_templates(dims, pets)
    conn = get_conn()
    try:
        for t in tpl:
            conn.execute(
                "INSERT INTO coach_tasks(week, dim, title, link_view, status) VALUES(?,?,?,?, 'open')",
                (week, t["dim"], t["title"], t["link_view"]))
        conn.commit()
    finally:
        conn.close()
    return coach_list_tasks(week)


def coach_task_done(task_id: int) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM coach_tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE coach_tasks SET status='done' WHERE id=?", (task_id,))
        conn.commit()
    finally:
        conn.close()
    return {"id": task_id, "status": "done"}


def coach_grade(score: int) -> str:
    if score >= 95:
        return "S"
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    return "C"


def coach_streak_update(score: int, week: str) -> int:
    """连续达标周数：本周分 ≥85 记 1 并衔接上周，否则清零；同周重复调用不叠加。"""
    raw = kv_get("coach:streak") or "0:never"
    try:
        streak_s, last = raw.split(":", 1)
        streak = int(streak_s)
    except Exception:
        streak, last = 0, "never"
    if last == week:
        return streak
    if score >= 85:
        streak = 1 if last == "never" else streak + 1
    else:
        streak = 0
    kv_set("coach:streak", f"{streak}:{week}")
    return streak


def coach_weekly() -> dict:
    """GET /api/coach/weekly 的规则层主体（不含 LLM 信）。"""
    week = coach_week_id()
    dims = coach_dims()
    score = 0.0
    for key, _label, w in COACH_DIM_DEFS:
        d = next(x for x in dims if x["key"] == key)
        score += d["score"] * w
    score = _coach_clamp(score)
    tasks = coach_ensure_tasks(dims, week)
    streak = coach_streak_update(score, week)
    return {
        "week": week,
        "score": score,
        "grade": coach_grade(score),
        "dims": dims,
        "tasks": tasks,
        "streak": streak,
    }


if __name__ == "__main__":
    init_and_seed()
    print("DB ready:", DB_PATH)
    print("pets:", len(list_pets()), "| reminders:", len(compute_reminders()))
