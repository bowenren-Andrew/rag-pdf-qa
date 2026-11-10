# -*- coding: utf-8 -*-
"""启动自检：识别 Streamlit 热重载后仍加载旧模块的情况。"""
from pathlib import Path

import auth
import build_guard
import db

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_模块指纹与磁盘源文件一致():
    """导入时记录的指纹必须等于当前源文件，避免自检基准本身就是旧的。"""
    for module, filename in (
        (auth, "auth.py"),
        (db, "db.py"),
        (build_guard, "build_guard.py"),
    ):
        expected = build_guard.source_fingerprint(str(PROJECT_ROOT / filename))
        assert expected
        assert module.MODULE_BUILD == expected


def test_旧模块会被识别():
    """模拟 Streamlit 缓存了旧 auth：指纹不一致时必须报出来。"""
    class Stale:
        MODULE_BUILD = "old-build"

    class Fresh:
        pass

    fresh = Fresh()
    fresh.MODULE_BUILD = build_guard.source_fingerprint(
        str(PROJECT_ROOT / "auth.py")
    )

    assert build_guard.stale_modules(
        {"auth": (Stale(), str(PROJECT_ROOT / "auth.py"))}
    ) == ["auth"]
    assert build_guard.stale_modules(
        {"auth": (fresh, str(PROJECT_ROOT / "auth.py"))}
    ) == []


def test_缺少构建指纹的模块会被识别为旧模块():
    """旧版 auth/db 没有 MODULE_BUILD，启动自检不能放行。"""

    class NoBuild:
        pass

    assert build_guard.module_is_current(
        NoBuild(), str(PROJECT_ROOT / "auth.py")
    ) is False


def test_app启动时会执行旧模块自检():
    """防止以后误删 app.py 里的自检调用。"""
    text = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")
    assert "_verify_loaded_modules()" in text
    assert "build_guard.stale_modules" in text
