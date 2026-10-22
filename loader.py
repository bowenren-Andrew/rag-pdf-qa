# -*- coding: utf-8 -*-
"""
loader.py
职责：读取 PDF 并切分文本片段（单一职责，不涉及向量化和问答）。
- 使用 PyPDF2 逐页提取文本；
- 清洗文本：合并段内换行、压缩多余空白；
- 按「句子边界优先」切分：每段约 500 字，段间保留约 50 字的句子级重叠；
- 单句超过 500 字时，退化为字符滑动窗口硬切兜底。
"""
import re
from typing import IO, Union

from PyPDF2 import PdfReader

# 切分参数：每段目标长度 / 段间重叠长度（单位：字符）
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50


def _clean_text(text: str) -> str:
    """清洗单页文本：合并段内换行、去掉多余空格。"""
    if not text:
        return ""
    # 统一换行符
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # 多个连续换行视为段落分隔，压缩为一个
    text = re.sub(r"\n{2,}", "\n", text)
    # 中文 PDF 的段内换行多为排版产生，直接去掉以免切断句子
    text = text.replace("\n", "")
    # 压缩连续空格/制表符
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _split_sentences(text: str) -> list[str]:
    """按中英文句末标点切句，标点保留在句尾。"""
    sentences = re.split(r"(?<=[。！？；!?;])", text)
    return [s for s in sentences if s.strip()]


def _hard_split(sentence: str) -> list[str]:
    """兜底：对超长单句使用 500 字 / 50 字重叠的字符滑动窗口硬切。"""
    chunks = []
    step = CHUNK_SIZE - CHUNK_OVERLAP
    for start in range(0, len(sentence), step):
        chunk = sentence[start:start + CHUNK_SIZE]
        if chunk.strip():
            chunks.append(chunk)
        if start + CHUNK_SIZE >= len(sentence):
            break
    return chunks


def _split_page_text(text: str) -> list[str]:
    """把单页清洗后的文本切成约 500 字的片段，段间保留约 50 字句子级重叠。"""
    sentences = _split_sentences(text)
    chunks = []
    current = ""

    for sentence in sentences:
        # 单句本身超长：先结算当前段落，再对该句硬切
        if len(sentence) > CHUNK_SIZE:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_hard_split(sentence))
            continue

        if len(current) + len(sentence) <= CHUNK_SIZE:
            current += sentence
        else:
            chunks.append(current)
            # 从刚完成的段落尾部取若干句子，凑出约 50 字重叠，作为下一段开头
            overlap = ""
            for s in reversed(_split_sentences(current)):
                if len(overlap) + len(s) > CHUNK_OVERLAP:
                    break
                overlap = s + overlap
            current = overlap + sentence

    if current.strip():
        chunks.append(current)
    return chunks


def load_and_split_pdf(file: Union[str, IO[bytes]]) -> list[dict]:
    """
    读取 PDF 并切分文本。
    :param file: PDF 文件路径，或类文件对象（如 Streamlit 的 UploadedFile）
    :return: [{"text": 片段文本, "page": 页码(从1开始)}, ...]
    """
    reader = PdfReader(file)
    results = []
    for page_no, page in enumerate(reader.pages, start=1):
        raw = page.extract_text() or ""
        cleaned = _clean_text(raw)
        if not cleaned:
            continue  # 跳过无文本页（如纯图片页）
        for chunk in _split_page_text(cleaned):
            results.append({"text": chunk, "page": page_no})
    return results
