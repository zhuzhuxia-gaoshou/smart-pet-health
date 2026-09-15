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
PET_TYPES = {"cat": "猫", "dog": "狗", "bird": "鸟", "fish": "鱼", "other": "其他"}
PET_STATUS = {"healthy": "健康", "attention": "需关注", "ill": "治疗中"}
GENDERS = {"male": "公", "female": "母", "unknown": "未知"}
# 提醒重复周期：'' 表示不重复；完成一轮后按周期滚动生成下一轮记录
REPEAT_RULES = {"": "不重复", "daily": "每天", "weekly": "每周", "monthly": "每月", "yearly": "每年"}
# 用药状态
MED_STATUS = {"active": "在用", "finished": "已结束"}


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
    """按重复周期从 base 滚动到下一次日期；月/年滚动钳制到目标月最后一天（1月31日→2月28日）。"""
    import calendar
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
    滚动基准 = max(今天, 原到期日)：逾期完成从今天重置节奏，提前完成保持原节奏。"""
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM health_records WHERE id=?", (record_id,)).fetchone()
        if row is None:
            return None
        if not row["next_date"]:
            return {"record": _rec_labels(dict(row)), "next": None}
        today = today_str()
        conn.execute("UPDATE health_records SET next_date=NULL WHERE id=?", (record_id,))
        next_id = None
        rule = row["repeat_rule"] or ""
        if rule in REPEAT_RULES and rule:
            next_date = roll_date(max(today, row["next_date"]), rule)
            cur = conn.execute(
                "INSERT INTO health_records(pet_id,type,date,title,note,next_date,repeat_rule)"
                " VALUES(?,?,?,?,?,?,?)",
                (row["pet_id"], row["type"], today, row["title"], row["note"], next_date, rule))
            next_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return {"record": get_record(record_id), "next": get_record(next_id) if next_id else None}


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


def list_all_records(limit: int | None = None) -> list[dict]:
    """全部健康记录（带宠物名/头像），按日期倒序 — 供"健康记录"页使用。"""
    sql = ("SELECT r.*, p.name AS pet_name, p.avatar AS pet_avatar, p.type AS pet_type"
           " FROM health_records r JOIN pets p ON p.id = r.pet_id"
           " ORDER BY r.date DESC, r.id DESC")
    conn = get_conn()
    try:
        rows = conn.execute(sql + (" LIMIT ?" if limit else ""),
                            ((limit,) if limit else ())).fetchall()
    finally:
        conn.close()
    return [_rec_labels(dict(r)) for r in rows]


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
                  "health_records", "medications", "memories", "pets"):
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


if __name__ == "__main__":
    init_and_seed()
    print("DB ready:", DB_PATH)
    print("pets:", len(list_pets()), "| reminders:", len(compute_reminders()))
