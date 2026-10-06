"""
PDF 解析：文字层提取 + 扫描页 OCR 兜底
------------------------------------
按**页**判断是否需要 OCR（混合型 PDF —— 正文 + 扫描附件 —— 很常见，按文档判断
要么什么都不 OCR，要么全部 OCR）。

判定标准：`page.get_text()` 的字符数低于 `PDF_MIN_CHARS_PER_PAGE`。扫描页通常只有
0~3 个杂散字形，而正常页即使只有图注也远超这个阈值。

未启用 OCR 时的扫描页会输出**可见**的说明行，而不是静默留空 —— 这正是本次改动的
重点：以前扫描版 PDF 会静默产出近乎空的本体，还照常花掉一次 LLM 调用。
"""

import logging
from pathlib import Path
from typing import List, Optional

from .. import ocr as ocr_module
from ..locale import t
from . import base
from .base import ExtractionError, ExtractionResult

logger = logging.getLogger(__name__)


def extract_pdf(path: str) -> ExtractionResult:
    """.pdf —— 逐页提取文字层，扫描页按配置走 OCR。"""
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise ExtractionError('需要安装 PyMuPDF: uv sync') from exc

    cfg = ocr_module.get_ocr_config()
    scan_fallback = bool(cfg.get('scan_pdf_fallback'))
    try:
        max_ocr_pages = int(cfg.get('max_pages') or 30)
    except (TypeError, ValueError):
        max_ocr_pages = 30

    blocks: List[str] = []
    notes: List[str] = []
    ocr_pages = 0
    pages_over_cap = 0
    filename = Path(path).name

    with fitz.open(path) as doc:
        for page_no, page in enumerate(doc, start=1):
            native = (page.get_text() or '').strip()
            text = native
            used_engine: Optional[str] = None
            empty_reason: Optional[str] = None

            # 文字层信息量不足 —— 大概率是扫描页。
            # 注意：即使没做 OCR，原生文字也要保留（标题页可能只有几十个字），
            # 只有在原生文字也为空时才输出「未启用 OCR」这类说明。
            if len(native) < base.PDF_MIN_CHARS_PER_PAGE:
                if scan_fallback and ocr_pages < max_ocr_pages:
                    ocr_text, used_engine, failure = _ocr_page(page, filename, page_no)
                    if ocr_text:
                        text = ocr_text
                        ocr_pages += 1
                        notes.append(f'ocr:{used_engine}#p{page_no}')
                    elif failure:
                        empty_reason = failure
                elif scan_fallback:
                    pages_over_cap += 1
                    if not native:
                        empty_reason = t('parse.pagesNotOcr')
                elif not native:
                    empty_reason = t('parse.scannedPageNoOcr')

            title = t('parse.pageHeading', n=page_no)
            if used_engine:
                title += f" (OCR: {used_engine})"

            block = base.heading(2, title)
            if text:
                block = base.join_blocks([block, text])
            if empty_reason:
                block = base.join_blocks([block, base.note(empty_reason)])
            blocks.append(block)

    if pages_over_cap:
        blocks.append(base.note(t('parse.pagesNotOcrCount', n=pages_over_cap)))

    return ExtractionResult(text=base.join_blocks(blocks), notes=notes)


def _ocr_page(page, filename: str, page_no: int):
    """
    把一页渲染成 PNG 再 OCR。

    Returns:
        (text, engine_name, failure_reason) —— 成功时 failure_reason 为 None，
        失败时 text 为空串且 engine_name 为 None
    """
    try:
        pixmap = page.get_pixmap(dpi=base.PDF_RENDER_DPI)
        png_bytes = pixmap.tobytes('png')
    except Exception as exc:
        logger.warning('渲染 PDF 第 %s 页失败 (%s): %s', page_no, filename, exc)
        return '', None, t('parse.pageRenderFailed')

    try:
        result = ocr_module.recognize(png_bytes, hint=f'{filename}#p{page_no}')
    except ocr_module.OCRError as exc:
        logger.warning('OCR PDF 第 %s 页失败 (%s): %s', page_no, filename, exc)
        return '', None, str(exc)

    text = (result.text or '').strip()
    if not text:
        return '', None, t('parse.scannedPageNoText')
    return text, result.engine, None
