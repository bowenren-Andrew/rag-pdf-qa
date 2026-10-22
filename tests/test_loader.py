# -*- coding: utf-8 -*-
"""
loader.py 文本切分函数的单元测试

测试原则：
1. 全部使用构造出来的假字符串，不读取任何真实 PDF 文件；
2. 涉及 PyPDF2 的地方用 unittest.mock 把 PdfReader 换成假的，避免文件 IO；
3. 每个测试函数的 docstring 说明"它在测什么"。
"""
from unittest.mock import MagicMock, patch

from loader import (
    _clean_text,
    _hard_split,
    _split_page_text,
    _split_sentences,
    load_and_split_pdf,
)

# 切分参数（与 loader.py 保持一致，用于断言）
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50


def _mk_sentence(i: int, length: int = 40) -> str:
    """构造一句固定长度、以句号结尾的假中文句子，长度默认 40 字。"""
    return f"{i:03d}" + "内" * (length - 4) + "。"


# ---------------- _clean_text ----------------

def test_clean_text_去掉段内换行并保留文字内容():
    """PDF 的换行是排版换行，应被合并；文字本身不能丢。"""
    raw = "这是第一行\n这是第二行\n\n这是第二段"
    out = _clean_text(raw)
    assert "\n" not in out                       # 换行被清理干净
    assert out == "这是第一行这是第二行这是第二段"   # 内容没有丢失


def test_clean_text_压缩连续空格为一个():
    """多个空格/制表符应被压缩成单个空格。"""
    assert _clean_text("词语A    词语B") == "词语A 词语B"


def test_clean_text_空字符串返回空字符串():
    """边界：空输入不应报错。"""
    assert _clean_text("") == ""


# ---------------- _split_sentences ----------------

def test_split_sentences_按中文句末标点切句并保留标点():
    """切句后标点必须留在句尾，句子才是完整可读的。"""
    out = _split_sentences("第一句话。第二句话！第三句话？")
    assert out == ["第一句话。", "第二句话！", "第三句话？"]


def test_split_sentences_没有标点时整段作为一句返回():
    """没有句末标点时不应切成零碎片段。"""
    assert _split_sentences("完全没有标点的长文本") == ["完全没有标点的长文本"]


# ---------------- _hard_split（滑动窗口兜底） ----------------

def test_hard_split_超长文本被切成多段且每段不超过500字():
    """超长单句走得是硬切兜底路径，长度必须受控。"""
    chunks = _hard_split("字" * 1200)
    assert len(chunks) == 3                             # 0-500 / 450-950 / 900-1200
    assert all(len(c) <= CHUNK_SIZE for c in chunks)
    assert len(chunks[0]) == CHUNK_SIZE                 # 第一段是满的


def test_hard_split_相邻片段之间保留50字重叠():
    """硬切也必须保留重叠，否则断点处的语义会丢失。"""
    chunks = _hard_split("字" * 1200)
    assert chunks[0][-CHUNK_OVERLAP:] == chunks[1][:CHUNK_OVERLAP]


def test_hard_split_短文本只产生一个片段():
    """比窗口短的文本不该被切碎。"""
    assert len(_hard_split("很短的一句话。")) == 1


# ---------------- _split_page_text（主切分逻辑） ----------------

def test_split_page_text_每段长度都不超过设定值():
    """核心约束：任何片段都不能超过 500 字，否则会超出检索/提示词的舒适区。"""
    text = "".join(_mk_sentence(i) for i in range(30))   # 30 句 × 40 字 = 1200 字
    chunks = _split_page_text(text)
    assert len(chunks) >= 3
    assert all(len(c) <= CHUNK_SIZE for c in chunks)


def test_split_page_text_相邻段落保留句子级重叠():
    """新的一段应当以上一段末尾的整句开头（约 50 字），保证句子不被切断。"""
    text = "".join(_mk_sentence(i) for i in range(30))
    chunks = _split_page_text(text)
    # 每句 40 字，重叠部分正好是上一段的最后一句
    assert chunks[1][:40] == chunks[0][-40:]
    assert chunks[0][-40:] in chunks[1]


def test_split_page_text_遇到超长单句走硬切兜底():
    """整页没有一个句末标点时，必须退化为字符滑动窗口，不能返回一整块超长文本。"""
    chunks = _split_page_text("数" * 1200 + "。")
    assert len(chunks) >= 3
    assert max(len(c) for c in chunks) <= CHUNK_SIZE


def test_split_page_text_空文本返回空列表():
    """边界：空输入返回空列表，而不是返回 ['']。"""
    assert _split_page_text("") == []


# ---------------- load_and_split_pdf（用假的 PdfReader） ----------------

@patch("loader.PdfReader")
def test_load_and_split_pdf_把假PDF切成带页码的片段(mock_reader):
    """主入口应当输出 {text, page} 字典，页码从 1 开始。"""
    page1 = MagicMock()
    page1.extract_text.return_value = "第一页第一句。第一页第二句。"
    page2 = MagicMock()
    page2.extract_text.return_value = "第二页第一句。"
    mock_reader.return_value.pages = [page1, page2]

    result = load_and_split_pdf("fake.pdf")

    assert result                                    # 至少切出一个片段
    assert {r["page"] for r in result} == {1, 2}     # 两页都被处理
    assert result[0]["text"].startswith("第一页第一句。")
    assert all(set(r.keys()) == {"text", "page"} for r in result)


@patch("loader.PdfReader")
def test_load_and_split_pdf_跳过没有文本的页面(mock_reader):
    """扫描页/纯图片页提取结果为 None 或空串，应当被跳过而不是报错。"""
    empty = MagicMock()
    empty.extract_text.return_value = None      # 模拟提取失败返回 None
    real = MagicMock()
    real.extract_text.return_value = "这一页有内容。"
    mock_reader.return_value.pages = [empty, real]

    result = load_and_split_pdf("fake.pdf")

    assert len(result) == 1
    assert result[0]["page"] == 2               # 页码仍按真实页码计，不因跳过而错位


@patch("loader.PdfReader")
def test_load_and_split_pdf_文本经过清洗不含换行(mock_reader):
    """输出的片段必须是清洗过的（不含 PDF 的排版换行）。"""
    page = MagicMock()
    page.extract_text.return_value = "第一行内容\n第二行内容。"
    mock_reader.return_value.pages = [page]

    result = load_and_split_pdf("fake.pdf")

    assert "\n" not in result[0]["text"]
