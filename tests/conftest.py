# -*- coding: utf-8 -*-
"""
pytest 共用配置
作用：
1. 把项目根目录（tests 的上一级）加入 sys.path，测试才能 import 到 loader / qa / db / auth；
2. 提供两个共用 fixture：临时 sqlite 数据库、随机 Fernet 密钥。
注意：这里不加载向量模型、不发起任何真实网络请求。
"""
import os
import sys

import pytest
from cryptography.fernet import Fernet

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import db  # noqa: E402（必须在 sys.path 处理之后导入）


@pytest.fixture
def tmp_db(tmp_path):
    """
    把数据库指向一个临时 sqlite 文件，测完自动清理。
    避免测试污染项目里的 app.db，也不连接任何真实 Postgres。
    """
    url = f"sqlite:///{tmp_path / 'test.db'}"
    db.set_database_url(url)
    db.init_db()
    yield url
    db.set_database_url(f"sqlite:///{os.path.join(PROJECT_ROOT, 'app.db')}")


@pytest.fixture
def fernet_key(monkeypatch):
    """注入一个随机 Fernet 密钥到环境变量，保证加密相关测试不依赖真实 .env。"""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("APP_SECRET_KEY", key)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    return key
