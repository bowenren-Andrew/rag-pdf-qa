# -*- coding: utf-8 -*-
"""P3：解析进度映射、阶段文案与流式占位工具、loader 进度回调的测试。"""
from unittest.mock import MagicMock, patch

import pytest
from pptx import Presentation

from loader import load_and_split_pdf, load_and_split_pptx
from ui_helpers import (
    parse_progress_fraction,
    parse_progress_label,
    reveal_on_first_chunk,
)


# ---------------- 进度映射 ----------------

def test_抽取阶段按页数从5推进到65():
    assert parse_progress_fraction("extract", 0, 10) == 0.05
    assert parse_progress_fraction("extract", 5, 10) == 0.35
    assert parse_progress_fraction("extract", 10, 10) == 0.65


def test_OCR阶段在70到90之间():
    assert parse_progress_fraction("ocr", 0, 2) == 0.70
    assert parse_progress_fraction("ocr", 1, 2) == 0.80
    assert parse_progress_fraction("ocr", 2, 2) == 0.90


def test_非法输入不抛异常且返回起始值():
    assert parse_progress_fraction("extract", None, None) == 0.05
    assert parse_progress_fraction("extract", "x", "y") == 0.05
    assert parse_progress_fraction("extract", 3, 0) == 0.05


def test_进度不会超过预定上限():
    assert parse_progress_fraction("extract", 99, 1) == 0.65
    assert parse_progress_fraction("ocr", 99, 1) == 0.90


def test_阶段文案显示真实页数():
    assert parse_progress_label("extract", 2, 5) == "正在抽取文本…（2/5 页）"
    assert parse_progress_label("ocr", 1, 3, "张") == "正在 OCR 识别…（1/3 张）"
    assert parse_progress_label("ocr", None, None) == "正在 OCR 识别…（0/0 页）"


# ---------------- 流式占位 ----------------

def test_首块到达只回调一次且结束回调():
    calls = []
    stream = reveal_on_first_chunk(
        iter(["a", "b"]),
        on_first=lambda: calls.append("first"),
        on_finish=lambda: calls.append("finish"),
    )
    assert list(stream) == ["a", "b"]
    assert calls == ["first", "finish"]


def test_空流也会清理占位():
    calls = []
    stream = reveal_on_first_chunk(
        iter([]), on_first=lambda: calls.append("first"), on_finish=lambda: calls.append("finish")
    )
    assert list(stream) == []
    assert calls == ["finish"]


def test_流中途抛错仍会清理占位():
    def broken():
        yield "a"
        raise RuntimeError("boom")

    calls = []
    stream = reveal_on_first_chunk(
        broken(), on_first=lambda: calls.append("first"), on_finish=lambda: calls.append("finish")
    )
    pieces = []
    with pytest.raises(RuntimeError):
        for piece in stream:
            pieces.append(piece)
    assert pieces == ["a"]
    assert calls == ["first", "finish"]


# ---------------- loader 进度回调 ----------------

@patch("loader.PdfReader")
def test_pdf解析逐页回调抽取进度(mock_reader):
    pages = []
    for text in (
        "第一页的内容足够长，不会被当成图片页。",
        "第二页的内容也足够长，不会触发图片页 OCR。",
        "第三页的内容同样足够长，避免走 OCR 分支。",
    ):
        page = MagicMock()
        page.extract_text.return_value = text
        pages.append(page)
    mock_reader.return_value.pages = pages

    calls = []
    load_and_split_pdf("fake.pdf", on_progress=lambda s, d, t: calls.append((s, d, t)))

    assert calls == [
        ("extract", 1, 3),
        ("extract", 2, 3),
        ("extract", 3, 3),
    ]


def test_pptx解析逐张回调抽取进度(tmp_path):
    prs = Presentation()
    for title in ("第一页", "第二页"):
        slide = prs.slides.add_slide(prs.slide_layouts[5])
        slide.shapes.title.text = title
    path = tmp_path / "demo.pptx"
    prs.save(str(path))

    calls = []
    load_and_split_pptx(str(path), on_progress=lambda s, d, t: calls.append((s, d, t)))

    assert calls == [("extract", 1, 2), ("extract", 2, 2)]


def test_进度回调抛异常不影响解析(tmp_path):
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.title.text = "内容"
    path = tmp_path / "demo2.pptx"
    prs.save(str(path))

    def bad_callback(*args):
        raise RuntimeError("回调坏了")

    chunks = load_and_split_pptx(str(path), on_progress=bad_callback)
    assert chunks and "内容" in chunks[0]["text"]
