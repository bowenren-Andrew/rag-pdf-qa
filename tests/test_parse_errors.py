# -*- coding: utf-8 -*-
"""
上传解析错误的中文提示测试（BUG-01 / BUG-02）

测试原则：
1. 用程序化生成的异常文件触发真实解析路径，不依赖外部素材；
2. 只检查 loader 抛出的中文错误，不涉及 Streamlit 界面；
3. 断言错误信息里不出现底层英文异常原文。
"""
import io
import os

import pytest
from PyPDF2 import PdfWriter

from loader import (
    MSG_BROKEN_PDF,
    MSG_BROKEN_PPTX,
    MSG_EMPTY_FILE,
    MSG_ENCRYPTED_PDF,
    FileParseError,
    load_and_split_file,
)


def _encrypted_pdf_bytes(password: str = "123456") -> bytes:
    """生成一个带打开密码的 PDF（空页即可，只为验证加密识别）。"""
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt(password)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _parse(data: bytes, filename: str):
    return load_and_split_file(io.BytesIO(data), filename)


def test_空文件提示中文可操作():
    """0 字节文件应提示“文件为空”，而不是 Cannot read an empty file。"""
    with pytest.raises(FileParseError) as exc:
        _parse(b"", "empty.pdf")

    assert str(exc.value) == MSG_EMPTY_FILE
    assert "empty file" not in str(exc.value).lower()


def test_纯文本改名的PDF提示文件损坏():
    """纯文本改扩展名的假 PDF 应提示损坏，而不是 EOF marker not found。"""
    with pytest.raises(FileParseError) as exc:
        _parse("这不是 PDF，只是改了后缀。".encode("utf-8"), "fake.pdf")

    assert str(exc.value) == MSG_BROKEN_PDF
    assert "EOF" not in str(exc.value)


def test_随机字节PDF提示文件损坏():
    """随机字节同样归入“损坏或不是有效 PDF”。"""
    with pytest.raises(FileParseError) as exc:
        _parse(os.urandom(2048), "corrupt.pdf")

    assert str(exc.value) == MSG_BROKEN_PDF


def test_加密PDF提示先解除密码():
    """加密 PDF 应给出针对性提示，而不是 File has not been decrypted。"""
    with pytest.raises(FileParseError) as exc:
        _parse(_encrypted_pdf_bytes(), "encrypted.pdf")

    assert str(exc.value) == MSG_ENCRYPTED_PDF
    assert "decrypt" not in str(exc.value).lower()


def test_不支持的类型提示仅支持PDF与PPTX():
    """.txt 等类型应提示仅支持 PDF 与 PPTX，并保留具体后缀便于排查。"""
    with pytest.raises(FileParseError) as exc:
        _parse(b"hello", "note.txt")

    msg = str(exc.value)
    assert "不支持的文件类型" in msg
    assert "仅支持 PDF 与 PPTX" in msg
    assert ".txt" in msg


def test_损坏的PPTX提示中文():
    """损坏的 PPTX 也不能把底层异常原文暴露给用户。"""
    with pytest.raises(FileParseError) as exc:
        _parse(b"not a zip package", "broken.pptx")

    assert str(exc.value) == MSG_BROKEN_PPTX


def test_空PPTX提示文件为空():
    """0 字节 PPTX 与空 PDF 使用同一条空文件提示。"""
    with pytest.raises(FileParseError) as exc:
        _parse(b"", "empty.pptx")

    assert str(exc.value) == MSG_EMPTY_FILE


def test_FileParseError仍属于ValueError以保证兼容():
    """既有调用方与测试按 ValueError 捕获，新异常必须保持兼容。"""
    assert issubclass(FileParseError, ValueError)
