# -*- coding: utf-8 -*-
"""
pytest 共用配置
作用：把项目根目录（tests 的上一级）加入 sys.path，
      这样测试文件里才能 import 到 loader / qa 等模块。
注意：这里不加载向量模型、不读取真实 .env 之外的任何外部资源。
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
