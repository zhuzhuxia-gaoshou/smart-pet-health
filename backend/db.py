# -*- coding: utf-8 -*-
"""db.py — SQLite 数据层：建库、示例数据、CRUD、提醒计算。

约定：
- 每请求新建连接（sqlite3 线程安全模式下 check_same_thread=False）。
- 日期统一存 'YYYY-MM-DD' 字符串。
"""
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
PET_TYPES = {"cat": "猫", "dog": "狗", "bird": "鸟", "other": "其他"}
PET_STATUS = {"healthy": "健康", "attention": "需关注", "ill": "治疗中"}
GENDERS = {"male": "公", "female": "母", "unknown": "未知"}


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
  FOREIGN KEY(pet_id) REFERENCES pets(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS weight_logs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pet_id INTEGER, date TEXT, weight REAL,
  FOREIGN KEY(pet_id) REFERENCES pets(id) ON DELETE CASCADE
);
"""


def init_db() -> None:
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
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
            # (pet_id, type, date, title, note, next_date)
            (可乐, "vaccine",    _d(-350), "狂犬疫苗（第1年）", "已按时接种，无不良反应", _d(5)),
            (可乐, "deworm",     _d(-60),  "体内外驱虫（大宠爱）", "滴剂一支", _d(2)),
            (可乐, "checkup",    _d(-200), "年度体检", "各项指标正常", _d(165)),
            (可乐, "medication", _d(-3),   "肠胃调理（益生菌）", "每日一次，拌粮", None),
            (布丁, "vaccine",    _d(-400), "猫三联（第三针）", "接种后观察30分钟", _d(-2)),
            (布丁, "deworm",     _d(-30),  "体内驱虫（拜耳）", "空腹喂药", _d(60)),
            (布丁, "clinic",     _d(-8),   "外耳炎就诊", "左耳轻微发红，开耳药水滴7天", _d(1)),
            (布丁, "checkup",    _d(-150), "生化检查", "肾指标正常，注意饮水量", None),
            (翠翠, "checkup",    _d(-90),  "羽毛与喙部检查", "状态良好", _d(90)),
            (翠翠, "medication", _d(-5),   "电解质水补充", "换羽期补充营养", None),
        ]
        conn.executemany(
            "INSERT INTO health_records(pet_id,type,date,title,note,next_date)"
            " VALUES(?,?,?,?,?,?)", records)

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
            out.append({
                "record_id": r["id"], "pet_id": r["pet_id"], "pet_name": r["pet_name"],
                "type": r["type"], "type_label": RECORD_TYPES.get(r["type"], r["type"]),
                "title": r["title"], "next_date": r["next_date"],
                "days_left": d, "overdue": d < 0,
            })
    return out


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
        out = []
        for r in rows:
            d = dict(r)
            d["type_label"] = RECORD_TYPES.get(d["type"], d["type"])
            if d.get("next_date"):
                d["days_left"] = days_until(d["next_date"])
            out.append(d)
        return out
    finally:
        conn.close()


RECORD_FIELDS = ("type", "date", "title", "note", "next_date", "weight")


def add_record(pet_id: int, data: dict) -> dict | None:
    if get_pet(pet_id) is None:
        return None
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO health_records(pet_id,type,date,title,note,next_date)"
            " VALUES(?,?,?,?,?,?)",
            (pet_id, data.get("type"), data.get("date") or today_str(),
             data.get("title"), data.get("note"), data.get("next_date") or None))
        # 带体重的记录同步写入体重表（供趋势图与 Agent 分析）
        if data.get("weight"):
            conn.execute("INSERT INTO weight_logs(pet_id,date,weight) VALUES(?,?,?)",
                         (pet_id, data.get("date") or today_str(), float(data["weight"])))
            conn.execute("UPDATE pets SET weight=? WHERE id=?",
                         (float(data["weight"]), pet_id))
        conn.commit()
        row = conn.execute("SELECT * FROM health_records WHERE id=?",
                           (cur.lastrowid,)).fetchone()
        d = dict(row)
        d["type_label"] = RECORD_TYPES.get(d["type"], d["type"])
        return d
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


def recent_activity(limit: int = 8) -> list[dict]:
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


def init_and_seed() -> None:
    init_db()
    seed()


if __name__ == "__main__":
    init_and_seed()
    print("DB ready:", DB_PATH)
    print("pets:", len(list_pets()), "| reminders:", len(compute_reminders()))
