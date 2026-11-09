# -*- coding: utf-8 -*-
"""单文件硬限制：最多 100 页 / 20MB，超限直接中文拒绝。"""
import io

import pytest
from PyPDF2 import PdfWriter
from pptx import Presentation

from loader import (
    MAX_FILE_BYTES,
    MAX_FILE_PAGES,
    MSG_FILE_TOO_LARGE,
    FileParseError,
    check_file_limits,
)


def _pdf_bytes(pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=100, height=100)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _pptx_bytes(slides: int) -> bytes:
    prs = Presentation()
    layout = prs.slide_layouts[6]  # 空白版式
    for _ in range(slides):
        prs.slides.add_slide(layout)
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def test_100页PDF允许上传():
    result = check_file_limits(io.BytesIO(_pdf_bytes(MAX_FILE_PAGES)), "ok.pdf")
    assert result["pages"] == 100


def test_101页PDF被中文拒绝且不解析():
    with pytest.raises(FileParseError) as exc:
        check_file_limits(io.BytesIO(_pdf_bytes(101)), "too_many.pdf")
    assert str(exc.value) == MSG_FILE_TOO_LARGE
    assert "100 页" in str(exc.value)
    assert "20MB" in str(exc.value)
    assert "请按章节拆分后上传" in str(exc.value)


def test_101张PPTX同样被拒绝():
    with pytest.raises(FileParseError) as exc:
        check_file_limits(io.BytesIO(_pptx_bytes(101)), "too_many.pptx")
    assert str(exc.value) == MSG_FILE_TOO_LARGE


def test_超过20MB直接拒绝_不读页数():
    """超大文件在页数检查之前就应被大小拦下。"""
    with pytest.raises(FileParseError) as exc:
        check_file_limits(
            io.BytesIO(b"x"),
            "huge.pdf",
            size_bytes=MAX_FILE_BYTES + 1,
        )
    assert str(exc.value) == MSG_FILE_TOO_LARGE


def test_损坏PDF不在这里误报_交给解析层给中文错误():
    result = check_file_limits(io.BytesIO(b"not a pdf"), "broken.pdf", size_bytes=9)
    assert result["pages"] is None
