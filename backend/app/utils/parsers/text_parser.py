"""
纯文本类解析：.txt / .md / .markdown / .csv
"""

import csv
import io
from pathlib import Path

from . import base
from .base import ExtractionResult
from ..locale import t

# CSV 方言嗅探的候选分隔符。中文 Excel 导出的分隔符经常是 ';' 或制表符。
_CSV_DELIMITERS = ',;\t|'


def read_text_with_fallback(file_path: str) -> str:
    """
    读取文本文件，UTF-8 失败时自动探测编码。

    多级回退：
    1. UTF-8 严格解码
    2. charset_normalizer 检测
    3. chardet 检测
    4. UTF-8 + errors='replace' 兜底
    """
    data = Path(file_path).read_bytes()

    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        pass

    encoding = None
    try:
        from charset_normalizer import from_bytes
        best = from_bytes(data).best()
        if best and best.encoding:
            encoding = best.encoding
    except Exception:
        pass

    if not encoding:
        try:
            import chardet
            result = chardet.detect(data)
            encoding = result.get('encoding') if result else None
        except Exception:
            pass

    if not encoding:
        encoding = 'utf-8'

    return data.decode(encoding, errors='replace')


def extract_text_file(path: str) -> ExtractionResult:
    """.txt / .md / .markdown —— 原样返回，不加任何结构标记。"""
    return ExtractionResult(text=read_text_with_fallback(path), notes=[])


def extract_csv(path: str) -> ExtractionResult:
    """
    .csv —— 解析成 Markdown 表格。

    用 csv.reader 而不是 splitlines()：后者会破坏字段内部的换行。
    """
    raw = read_text_with_fallback(path)

    sample = raw[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=_CSV_DELIMITERS)
    except csv.Error:
        dialect = csv.excel

    rows = list(csv.reader(io.StringIO(raw), dialect))

    # 整行为空的尾部行没有信息量，去掉
    while rows and not any(cell.strip() for cell in rows[-1]):
        rows.pop()
    if not rows:
        return ExtractionResult(text='', notes=[])

    table = base.render_markdown_table(rows, max_rows=base.MAX_SHEET_ROWS, max_cols=base.MAX_SHEET_COLS)
    if not table:
        return ExtractionResult(text='', notes=[])

    body = base.join_blocks([base.heading(2, t('parse.csvHeading')), table])
    return ExtractionResult(text=body, notes=[])
