# -*- coding: utf-8 -*-
"""
loader.py（v3.2）
职责：读取 PDF / PPTX 并切分文本片段（单一职责，不涉及向量化和问答）。
- PDF：PyPDF2 逐页提取文本；无文本页（扫描件、图片页）自动走 PyMuPDF + RapidOCR 兜底；
- PPTX：python-pptx 逐张幻灯片提取文本（含表格单元格文本）；
- 清洗文本：合并段内换行、压缩多余空白；
- 按「句子边界优先」切分：每段约 500 字，段间保留约 50 字的句子级重叠；
- 单句超过 500 字时，退化为字符滑动窗口硬切兜底。

v3.1 新增：load_and_split_pptx、load_and_split_file（按后缀分发）、无文本页 OCR 兜底。
v3.2 新增：force_ocr 参数——针对 LaTeX 导出的公式类 PDF（有文本层但公式位置为空、
整页字数远超阈值）提供"强制全文 OCR"开关；不勾选时行为与 v3.1 完全一致。
"""
import io
import logging
import os
import re
from typing import IO, Union

from PyPDF2 import PdfReader

logger = logging.getLogger(__name__)

# 切分参数：每段目标长度 / 段间重叠长度（单位：字符）
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50

# 单页清洗后文本少于该字符数，就判定为"无文本页"并触发 OCR 兜底
MIN_TEXT_LEN = 10
# OCR 前把页面渲染成图片的缩放倍数（2 倍清晰度足够，体积也可控）
OCR_ZOOM = 2.0
# 目前支持的文件后缀（小写）
SUPPORTED_EXTS = ("pdf", "pptx")

# 单文件硬限制（v3.8）：超过直接中文拒绝，不解析、不扣额度。
MAX_FILE_PAGES = 100
MAX_FILE_BYTES = 20 * 1024 * 1024
MSG_FILE_TOO_LARGE = "文件过大（超过 100 页或 20MB），请按章节拆分后上传"

# 面向用户的中文错误文案：底层异常原文只写日志，不展示给用户
MSG_EMPTY_FILE = "文件为空，请重新上传。"
MSG_BROKEN_PDF = "文件已损坏或不是有效的 PDF，请重新上传。"
MSG_ENCRYPTED_PDF = "该 PDF 已加密，请解除密码后重新上传。"
MSG_BROKEN_PPTX = "文件已损坏或不是有效的 PPTX，请重新上传。"


class FileParseError(ValueError):
    """文件解析失败的中文错误（继承 ValueError，兼容既有调用与测试）。"""


def _pdf_error_message(error: Exception) -> str:
    """按 PyPDF2 抛出的异常文本归类为中文提示。"""
    text = str(error).lower()
    if "empty file" in text:
        return MSG_EMPTY_FILE
    if "decrypt" in text or "encrypt" in text:
        return MSG_ENCRYPTED_PDF
    return MSG_BROKEN_PDF


def _notify_progress(on_progress, stage: str, done: int, total: int) -> None:
    """把解析阶段进度回调给界面；回调本身出错不能影响解析。"""
    if on_progress is None:
        return
    try:
        on_progress(stage, done, total)
    except Exception:
        logger.debug("进度回调异常", exc_info=True)


def _repair_broken_words(text: str) -> str:
    """
    修复 PDF 排版造成的单词/编号断字（BUG-10）。

    只处理低风险模式，避免把正常英文词组粘在一起：
    - 邮箱局部被拆成 "suppor t@..."：合并成 "support@..."；
    - 邮箱 @ 两侧的空白；
    - 型号/编号被拆成 "X 1"、"SN 12345"：合并为连续字符串。
    - 数字+大写字母被拆成 "5A TM"：合并为 "5ATM"（DEF-01）；
    - 短大写字母对被拆开且紧邻中文："AM OLED屏"：合并为 "AMOLED屏"。
    """
    if not text:
        return ""
    # 邮箱：最后一个字母与 @ 之间被插入空格（典型：suppor t@example.com）
    text = re.sub(r"(?<=[A-Za-z])\s+(?=[A-Za-z]{1,2}@)", "", text)
    # 邮箱：@ 前后被排版空格隔开
    text = re.sub(r"(?<=[A-Za-z0-9._%+-])\s+(?=@)", "", text)
    text = re.sub(r"(?<=@)\s+", "", text)
    # 型号/编号：短大写前缀与数字之间被拆开（X 1、SN 12345、USB 3）
    text = re.sub(r"\b([A-Z]{1,4})\s+(\d{1,6})\b", r"\1\2", text)
    # DEF-01：数字+大写字母+大写字母被拆开（5A TM → 5ATM）。
    # 只在其后紧跟中文/标点/行尾时合并，避免误伤 "5A TM1000" 这类正常间隔。
    text = re.sub(
        r"(?<=\d)([A-Z]{1,3})\s+([A-Z]{1,4})(?=\s?[\u4e00-\u9fff]|\s?[，。；：、（）)]|$)",
        r"\1\2",
        text,
    )
    # DEF-01：1–2 个字母与 2–4 个字母组成的短大写串被拆开（AM OLED屏 → AMOLED屏）。
    # 要求首个片段不超过 2 个字母，避免把 "PDF FILE"、"HTTP API" 这类正常词组粘起来。
    text = re.sub(
        r"\b([A-Z]{1,2})\s+([A-Z]{2,4})(?=\s?[\u4e00-\u9fff]|\s?[，。；：、（）)]|$)",
        r"\1\2",
        text,
    )
    return text


def _fix_ocr_chars(text: str) -> str:
    """
    修正 OCR 常见的字符混淆（DEF-05），只用于 OCR 识别出的文本。

    - 型号里的乘号：云雀×1 → 云雀X1（466×466、7×24 这类数字间的乘号保持不变）；
    - 负温度：-10C → -10℃；
    - 温度区间起点：45C至50C → 45℃至50℃。
    规则都要求上下文（前一个中文/负号数字、后面是数字或“至/到”），避免误伤型号。
    """
    if not text:
        return ""
    # 中文后面紧跟“乘号+数字”：OCR 把型号里的 X 认成了 ×
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s*[×✕]\s*(?=\d)", "X", text)
    # 负温度：-10C → -10℃（后面不能再跟字母，避免 -10Cx 这类编号被改）
    text = re.sub(r"([-−]\d{1,3})\s*[Cc](?![A-Za-z])", r"\1℃", text)
    # 温度区间起点：-10C至45C → -10℃至45℃
    text = re.sub(r"(\d)\s*[Cc](?=\s*(?:至|到|~|～))", r"\1℃", text)
    # 温度区间终点：-10℃至45C → -10℃至45℃（只在已有 ℃ 的区间里改，避免误伤 5C 快充）
    text = re.sub(r"(℃\s*(?:至|到|~|～)\s*\d{1,3})\s*[Cc](?![A-Za-z])", r"\1℃", text)
    return text


def repair_chunk_text(text: str) -> str:
    """
    对已入库片段做一次文本后处理（DEF-02）：只修断字与 OCR 字符混淆，
    不重新抽取、不重新 OCR、不改动其他内容。
    """
    return _fix_ocr_chars(_repair_broken_words(text or ""))


# OCR 引擎懒加载缓存：只有真的遇到无文本页时才会初始化（首次会下载识别模型）
_ocr_engine = None
_ocr_error = ""


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
    # 修复 PDF 排版导致的断字（邮箱、型号、编号等连续字符串）
    text = _repair_broken_words(text)
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


def _read_file_bytes(file: Union[str, IO[bytes]]) -> bytes:
    """
    统一把入参读成 bytes：既支持文件路径，也支持 Streamlit 的 UploadedFile。
    读成 bytes 后可以反复使用（PyPDF2 与 PyMuPDF 各解析一次），避免流指针位置问题。
    """
    if isinstance(file, (str, bytes, os.PathLike)):
        path = os.fspath(file)
        with open(path, "rb") as f:
            return f.read()
    try:
        file.seek(0)
    except Exception:
        pass  # 不支持 seek 的流对象直接读即可
    data = file.read()
    if isinstance(data, str):
        data = data.encode("utf-8")
    return data


def _get_ocr():
    """
    懒加载 RapidOCR 引擎（只有真的遇到无文本页时才会调用）。
    首次调用会自动下载识别模型（约几十 MB），失败时抛 RuntimeError 并缓存原因，
    避免对每一页重复尝试、拖慢入库。

    DEF-05：关闭角度分类器（use_angle_cls=False）。
    该分类器会把横排长行误判成 180° 倒置（实测置信度 0.98），翻转后整行识别为空串，
    随后又被置信度过滤掉，表现为「整句凭空消失」。倒置扫描页极少见，
    真遇到时旋转 PDF 再传即可，不值得为此承担静默丢行的风险。
    """
    global _ocr_engine, _ocr_error
    if _ocr_engine is not None:
        return _ocr_engine
    if _ocr_error:
        raise RuntimeError(_ocr_error)
    try:
        from rapidocr_onnxruntime import RapidOCR

        engine = RapidOCR(use_angle_cls=False)
        # 不同 RapidOCR 版本对角度分类开关的命名/处理不一致：有的用
        # use_angle_cls，有的用 use_cls，旧参数还可能被静默忽略。
        # 构造完成后再显式关闭一次，确保线上新版本也不会走角度分类器。
        for flag in ("use_angle_cls", "use_cls"):
            if hasattr(engine, flag):
                setattr(engine, flag, False)
        _ocr_engine = engine
    except Exception as e:  # 模型未下载、依赖缺失等情况都走这里
        _ocr_error = f"OCR 引擎初始化失败：{e}"
        raise RuntimeError(_ocr_error) from e
    return _ocr_engine


def _ocr_image(image_bytes: bytes) -> str:
    """对单张图片做文字识别，返回识别出的纯文本（失败返回空串）。"""
    engine = _get_ocr()
    result, _ = engine(image_bytes)
    if not result:
        return ""
    # RapidOCR 返回结构：[[框坐标, 文本, 置信度], ...]
    return "\n".join(str(item[1]) for item in result if len(item) > 1)


def _render_pdf_page_png(data: bytes, page_no: int, zoom: float = OCR_ZOOM) -> bytes:
    """用 PyMuPDF 把 PDF 指定页渲染成 PNG 图片字节（zoom 为缩放倍数）。"""
    try:  # 新版 PyMuPDF 推荐 import pymupdf，旧版只有 fitz
        import pymupdf as fitz
    except ImportError:
        import fitz
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        page = doc[page_no - 1]  # PyMuPDF 页码从 0 开始
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        return pix.tobytes("png")
    finally:
        doc.close()


def load_and_split_pdf(
    file: Union[str, IO[bytes]],
    ocr_report: dict = None,
    force_ocr: bool = False,
    on_progress=None,
) -> list[dict]:
    """
    读取 PDF 并切分文本；无文本页（扫描件、图片页）自动走 OCR 兜底。
    :param file: PDF 文件路径，或类文件对象（如 Streamlit 的 UploadedFile）
    :param ocr_report: 可选，传入一个空 dict，函数会把 OCR 情况写进去：
                       {"ocr_pages": [走过OCR的页码], "ocr_failed": [OCR仍失败的页码],
                        "ocr_error": "引擎不可用的原因", "ocr_mode": "auto"/"force"}
    :param on_progress: 可选回调 (stage, done, total)，stage 为 "extract"/"ocr"，
                        用于界面展示真实解析进度；回调异常会被忽略。
    :param force_ocr: True 时忽略文本层、对每一页都渲染成图片走 OCR。
                      适用于 LaTeX 导出的公式类 PDF——这类文件有文本层（整页字数不少），
                      但公式符号没有文本映射、提取后公式位置为空白，常规兜底不会触发。
                      单页 OCR 失败会回退到该页提取到的文本，失败绝不中断整文件。
    :return: [{"text": 片段文本, "page": 页码(从1开始)}, ...]
    """
    try:
        reader = PdfReader(file)
    except Exception as e:
        logger.warning("PDF 读取失败：%s", e)
        raise FileParseError(_pdf_error_message(e)) from e

    # 加密 PDF 在取页文本时会抛 "File has not been decrypted"，
    # 这里提前识别，给出可操作的提示。
    if getattr(reader, "is_encrypted", False) is True:
        raise FileParseError(MSG_ENCRYPTED_PDF)

    results = []
    pending_ocr = []  # 待 OCR 的页：(页码, 该页原本提取到的文本，用作失败回退)

    def _append(page_no: int, text: str):
        """把一页文本切分后追加进结果（空文本直接跳过）。"""
        if not text:
            return
        for chunk in _split_page_text(text):
            results.append({"text": chunk, "page": page_no})

    total_pages = len(reader.pages)
    for page_no, page in enumerate(reader.pages, start=1):
        try:
            raw_text = page.extract_text() or ""
        except Exception as e:
            logger.warning("PDF 第 %s 页文本提取失败：%s", page_no, e)
            raise FileParseError(_pdf_error_message(e)) from e
        cleaned = _clean_text(raw_text)
        if force_ocr:
            # 强制模式：每一页都送进 OCR，提取到的文本只留作失败回退
            pending_ocr.append((page_no, cleaned))
        elif len(cleaned) < MIN_TEXT_LEN:
            # 自动模式：只有"无文本页"才 OCR；
            # 记录页码与"原本那点文本"：OCR 失败时还能退回用它，不把已有内容弄丢
            pending_ocr.append((page_no, cleaned))
        else:
            _append(page_no, cleaned)
        _notify_progress(on_progress, "extract", page_no, total_pages)

    ocr_pages, ocr_failed = [], []
    if pending_ocr:
        # 只有在真的存在无文本页时，才把原始数据读成 bytes 交给 PyMuPDF 渲染，
        # 避免正常 PDF 被多余地整份读进内存。
        try:
            data = _read_file_bytes(file)
        except Exception as e:
            if ocr_report is not None:
                ocr_report["ocr_error"] = f"无法读取原始文件，OCR 跳过：{e}"
            data = None

        if data:
            total_ocr = len(pending_ocr)
            for ocr_index, (page_no, fallback) in enumerate(pending_ocr, start=1):
                try:
                    png = _render_pdf_page_png(data, page_no)
                    # OCR 文本额外过一遍字符混淆修正（DEF-05）；文本层不走这一步
                    text = _fix_ocr_chars(_clean_text(_ocr_image(png)))
                except Exception as e:
                    # 单页 OCR 失败绝不能中断整个文件的入库
                    if ocr_report is not None:
                        ocr_report["ocr_error"] = str(e)
                    text = ""
                if len(text) >= MIN_TEXT_LEN:
                    ocr_pages.append(page_no)
                    _append(page_no, text)
                elif fallback:
                    # OCR 没帮上忙，但原本还提取到一点文本：保留它，不算跳过
                    _append(page_no, fallback)
                else:
                    # 真的一个字都没有，才算这一页被跳过
                    ocr_failed.append(page_no)
                _notify_progress(on_progress, "ocr", ocr_index, total_ocr)
        else:
            # 读不到原始数据：退回原本提取到的短文本，只有完全无文本的页才算跳过
            for page_no, fallback in pending_ocr:
                if fallback:
                    _append(page_no, fallback)
                else:
                    ocr_failed.append(page_no)

    if ocr_report is not None:
        ocr_report["ocr_pages"] = ocr_pages
        ocr_report["ocr_failed"] = ocr_failed
        # 供界面区分两种模式：auto=只 OCR 无文本页，force=全文强制 OCR
        ocr_report["ocr_mode"] = "force" if force_ocr else "auto"

    # OCR 的页是后追加的，按页码重新排一次，保证输出顺序与原文一致
    results.sort(key=lambda c: c["page"])
    return results


def _iter_shape_text(shape) -> list[str]:
    """
    递归取一个形状里的文本：优先表格单元格，其次文本框，组合形状继续下钻。
    返回文本列表（每张幻灯片内部再用换行连接）。
    """
    texts = []
    # 表格：逐单元格取文本
    if getattr(shape, "has_table", False):
        try:
            for row in shape.table.rows:
                for cell in row.cells:
                    if cell.text and cell.text.strip():
                        texts.append(cell.text.strip())
            return texts
        except Exception:
            return texts

    # 普通文本框（标题、正文、项目符号等）
    if getattr(shape, "has_text_frame", False):
        text = shape.text_frame.text
        if text and text.strip():
            texts.append(text.strip())
        return texts

    # 组合形状：递归子形状
    sub_shapes = getattr(shape, "shapes", None)
    if sub_shapes is not None:
        try:
            for sub in sub_shapes:
                texts.extend(_iter_shape_text(sub))
        except Exception:
            pass
    return texts


def load_and_split_pptx(file: Union[str, IO[bytes]], on_progress=None) -> list[dict]:
    """
    读取 PPTX 并切分文本：逐张幻灯片提取所有形状的文本（含表格单元格），
    同一张幻灯片内的文本用换行连接，然后复用清洗与切分逻辑。
    :param file: PPTX 文件路径，或类文件对象（如 Streamlit 的 UploadedFile）
    :return: [{"text": 片段文本, "page": 幻灯片序号(从1开始)}, ...]
             与 load_and_split_pdf 完全一致，便于入库与引用逻辑复用
    """
    from pptx import Presentation

    data = _read_file_bytes(file)
    if not data:
        raise FileParseError(MSG_EMPTY_FILE)
    try:
        prs = Presentation(io.BytesIO(data))
    except Exception as e:
        logger.warning("PPTX 读取失败：%s", e)
        raise FileParseError(MSG_BROKEN_PPTX) from e
    results = []
    total_slides = len(prs.slides)
    for slide_no, slide in enumerate(prs.slides, start=1):
        parts = []
        for shape in slide.shapes:
            parts.extend(_iter_shape_text(shape))
        cleaned = _clean_text("\n".join(parts))
        if cleaned:
            for chunk in _split_page_text(cleaned):
                results.append({"text": chunk, "page": slide_no})
        _notify_progress(on_progress, "extract", slide_no, total_slides)
    return results


def _file_size_bytes(file, size_bytes: int = None) -> int:
    """取文件字节数：优先用调用方给的 size，其次文件对象自带 size，最后才读一遍。"""
    if size_bytes is not None:
        return int(size_bytes)
    declared = getattr(file, "size", None)
    if isinstance(declared, int) and declared >= 0:
        return declared
    if isinstance(file, (str, bytes, os.PathLike)):
        try:
            return os.path.getsize(os.fspath(file))
        except Exception:
            pass
    try:
        return len(_read_file_bytes(file))
    except Exception:
        return 0


def count_file_pages(file: Union[str, IO[bytes]], filename: str) -> int | None:
    """
    只读取页数/幻灯片数，不做文本提取与 OCR。
    解析失败返回 None，交给真正的解析函数给出对应的中文错误，避免这里误报。
    """
    ext = os.path.splitext(filename or "")[1].lower().lstrip(".")
    try:
        data = _read_file_bytes(file)
        if ext == "pdf":
            reader = PdfReader(io.BytesIO(data))
            return len(reader.pages)
        if ext == "pptx":
            from pptx import Presentation

            return len(Presentation(io.BytesIO(data)).slides)
    except Exception:
        logger.debug("提前读取《%s》页数失败", filename, exc_info=True)
    return None


def check_file_limits(
    file: Union[str, IO[bytes]], filename: str, size_bytes: int = None
) -> dict:
    """
    上传前检查单文件硬限制：最多 100 页（PPTX 为 100 张）、20MB。
    超限直接抛 FileParseError（中文提示），不解析、不写库、不扣额度。
    :return: {"size": 字节数, "pages": 页数或 None}
    """
    ext = os.path.splitext(filename or "")[1].lower().lstrip(".")
    size = _file_size_bytes(file, size_bytes)
    if ext in SUPPORTED_EXTS:
        if size > MAX_FILE_BYTES:
            raise FileParseError(MSG_FILE_TOO_LARGE)
        pages = count_file_pages(file, filename)
        if pages is not None and pages > MAX_FILE_PAGES:
            raise FileParseError(MSG_FILE_TOO_LARGE)
        return {"size": size, "pages": pages}
    return {"size": size, "pages": None}


def load_and_split_file(
    file: Union[str, IO[bytes]],
    filename: str,
    ocr_report: dict = None,
    force_ocr: bool = False,
    on_progress=None,
) -> list[dict]:
    """
    按文件后缀分发解析器（PDF 走 PDF 解析，PPTX 走 PPTX 解析）。
    :param filename: 原始文件名，仅用于判断后缀
    :param ocr_report: 仅 PDF 使用，见 load_and_split_pdf 的说明
    :param force_ocr: 仅 PDF 使用，True 时全文强制 OCR（PPTX 本身有文本结构，忽略该参数）
    :raises ValueError: 遇到不支持的后缀
    """
    ext = os.path.splitext(filename or "")[1].lower().lstrip(".")
    if ext not in SUPPORTED_EXTS:
        raise FileParseError(
            f"不支持的文件类型：.{ext or '未知'}。仅支持 PDF 与 PPTX，请重新上传。"
        )
    try:
        if ext == "pdf":
            return load_and_split_pdf(
                file,
                ocr_report=ocr_report,
                force_ocr=force_ocr,
                on_progress=on_progress,
            )
        return load_and_split_pptx(file, on_progress=on_progress)
    except FileParseError:
        raise
    except Exception as e:
        # 兜底：任何未预料的底层异常都不把英文原文暴露给用户
        logger.warning("解析《%s》失败：%s", filename, e)
        raise FileParseError(MSG_BROKEN_PPTX if ext == "pptx" else MSG_BROKEN_PDF) from e
