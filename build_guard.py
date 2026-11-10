# -*- coding: utf-8 -*-
"""
build_guard.py
职责：启动自检，识别 Streamlit 热重载后仍在执行的旧模块。

背景：Streamlit 的 rerun 只重新执行主脚本 app.py；已经 import 的本地模块
（auth / db 等）可能继续留在 sys.modules 里。此时新文案会生效，但新逻辑
不会，表现为「代码已经推上去，线上行为还是旧版本」。

做法：每个模块导入时记录自己源文件的短 SHA-256，app.py 启动时再读一次
磁盘源文件做比对。指纹不一致就明确报错，避免静默地跑旧代码。
"""
import hashlib


def source_fingerprint(path: str) -> str:
    """返回源文件内容的短 SHA-256；文件不可读时返回空字符串。"""
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()[:12]
    except OSError:
        return ""


def module_is_current(module, source_path: str) -> bool:
    """模块导入时记录的指纹是否与磁盘上的源文件一致。"""
    expected = source_fingerprint(source_path)
    if not expected:
        # 读不到源文件时无法判断，宁可放行也不误伤正常部署。
        return True
    return getattr(module, "MODULE_BUILD", "") == expected


def stale_modules(modules: dict) -> list:
    """返回指纹不一致的模块名列表。modules 形如 {name: (module, path)}。"""
    return [
        name
        for name, (module, path) in modules.items()
        if not module_is_current(module, path)
    ]


MODULE_BUILD = source_fingerprint(__file__)
