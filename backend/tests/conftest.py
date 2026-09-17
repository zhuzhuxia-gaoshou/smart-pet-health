# -*- coding: utf-8 -*-
"""conftest — 把 DB_PATH 指到临时文件，每个测试拿到全新种子库，绝不触碰真实 pets.db。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import db  # noqa: E402


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """空库 + 两只最小宠物：不跑业务种子，断言不受种子相对日期与笔数漂移影响。"""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    conn = db.get_conn()
    try:
        conn.execute("INSERT INTO pets(name,type) VALUES('测测','dog')")
        conn.execute("INSERT INTO pets(name,type) VALUES('两两','cat')")
        conn.commit()
    finally:
        conn.close()
    return db
