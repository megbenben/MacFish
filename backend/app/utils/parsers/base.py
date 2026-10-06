"""
多格式文档解析的公共基础
------------------------
包含三部分：
1. 异常层级 —— 所有可恢复的解析失败都抛 ExtractionError 子类，str(exc) 直接面向用户
2. 各类上限 —— 防止超大文件把同步上传请求拖死
3. 容器嗅探 + Markdown 表格渲染 —— 各解析器共用

输出格式契约（所有解析器必须遵守）
---------------------------------
原因是 `services/text_processor.py` 的 `preprocess_text()` 会折叠 `\\n{3,}` 为 `\\n\\n`
并 `strip()` 每一行：

1. 结构边界一律用 ATX 标题（`#` / `##` / `###`）
2. 绝不产生连续 3 个以上换行
3. 表格内部绝不出现空行（否则 GFM 表格解析中断）
4. 绝不依赖行首缩进，嵌套深度 ≤ 1（子项用 `- （1）…` 前缀而非缩进）
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Sequence

from ..locale import t


@dataclass
class ExtractionResult:
    """
    单个文件的解析结果。

    Attributes:
        text: 提取出的 Markdown 文本
        notes: 处理过程中的说明（如 'ocr:vision'、'工作表 销量 已截断'），
               仅用于日志与前端展示，不参与图谱构建
    """

    text: str
    notes: List[str] = field(default_factory=list)

# ---- 各类上限 ----

MAX_SHEET_ROWS = 200        # 单个工作表/CSV 渲染的最大行数
MAX_SHEET_COLS = 30         # 单个工作表渲染的最大列数
MAX_SHEETS = 20             # 单个工作簿渲染的最大工作表数
MAX_TABLE_ROWS = 200        # docx/pptx 内表格的最大行数
TABLE_COL_CAP = 20          # docx/pptx 内表格的最大列数
MAX_SLIDES = 200            # 单个 deck 的最大页数
MAX_DOCX_BLOCKS = 5000      # docx 最大块数

# 一页 PDF 的文字层少于这个字符数就认定是扫描页，该走 OCR。
# 阈值刻意定得低：扫描页通常只有 0~3 个杂散字形，而稀疏但有文字的页面
# （标题页、只有图注的页）不该被误判成扫描页而白跑一次 OCR。
PDF_MIN_CHARS_PER_PAGE = 20
PDF_RENDER_DPI = 200        # OCR 渲染分辨率：中文识别的精度/耗时甜点
OLE2_MAGIC = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
ZIP_MAGIC = b'PK\x03\x04'
ZIP_EMPTY_MAGIC = b'PK\x05\x06'
ZIP_SPANNED_MAGIC = b'PK\x07\x08'
PDF_MAGIC = b'%PDF'

# 旧版 Office 二进制格式 → 建议另存的目标格式
LEGACY_OFFICE_TARGETS = {'.doc': '.docx', '.ppt': '.pptx', '.xls': '.xlsx'}

# ImageIO（macOS Vision 走这条路）原生支持的签名
_IMAGE_SIGNATURES = (
    (b'\x89PNG\r\n\x1a\n', 'png'),
    (b'\xff\xd8\xff', 'jpeg'),
    (b'GIF87a', 'gif'),
    (b'GIF89a', 'gif'),
    (b'BM', 'bmp'),
    (b'II*\x00', 'tiff'),
    (b'MM\x00*', 'tiff'),
)
_HEIF_BRANDS = {b'heic', b'heix', b'hevc', b'hevx', b'heim', b'heis', b'hevm', b'hevs',
                b'mif1', b'msf1', b'avif'}


# ---- 异常层级 ----
#
# 全部同时继承 ValueError：file_parser 原先在遇到未知格式时抛裸 ValueError，
# 任何捕获 ValueError 的既有调用方（含测试）都应继续工作。

class ExtractionError(ValueError):
    """解析失败基类。str(exc) 是已本地化的、可直接展示给用户的文案。"""


class UnsupportedFormatError(ExtractionError):
    """扩展名不在支持列表内。"""

    def __init__(self, ext: str):
        self.ext = ext or '(无扩展名)'
        super().__init__(t('api.unsupportedFileType', ext=self.ext))


class LegacyOfficeFormatError(ExtractionError):
    """旧版 Office 二进制格式：不支持解析，给出可操作的转换提示。"""

    def __init__(self, ext: str, target_ext: str):
        self.ext = ext
        self.target_ext = target_ext
        super().__init__(t('api.legacyOfficeUnsupported', ext=ext, target=target_ext))


class EncryptedDocumentError(ExtractionError):
    """文档被加密/受密码保护，或其实是旧版二进制格式。"""

    def __init__(self, filename: str):
        self.filename = filename
        super().__init__(t('api.encryptedDocument', filename=filename))


class FileTypeMismatchError(ExtractionError):
    """文件真实内容与扩展名不符（扩展名可被伪造）。"""

    def __init__(self, ext: str, kind: str):
        self.ext = ext
        self.kind = kind
        super().__init__(t('api.fileTypeMismatch', ext=ext))


# ---- 容器嗅探 ----

def sniff_container(path: str) -> str:
    """
    根据魔数判断文件真实类型。

    Returns:
        'pdf' | 'zip' | 'ole2' | 'image' | 'unknown'
    """
    try:
        with open(path, 'rb') as f:
            head = f.read(32)
    except OSError:
        return 'unknown'

    if not head:
        return 'unknown'
    if head.startswith(PDF_MAGIC):
        return 'pdf'
    if head.startswith((ZIP_MAGIC, ZIP_EMPTY_MAGIC, ZIP_SPANNED_MAGIC)):
        return 'zip'
    if head.startswith(OLE2_MAGIC):
        return 'ole2'
    for sig, _kind in _IMAGE_SIGNATURES:
        if head.startswith(sig):
            return 'image'
    # ISO-BMFF（HEIC/HEIF/AVIF）：偏移 4 处是 'ftyp'，紧随其后的 4 字节是 brand
    if len(head) >= 12 and head[4:8] == b'ftyp' and head[8:12] in _HEIF_BRANDS:
        return 'image'
    return 'unknown'


def ensure_readable_package(path: str, expect: str = 'zip') -> None:
    """
    Office 文件（docx/pptx/xlsx）本质是 ZIP 包。在交给 python-docx 等库之前先验证，
    因为这些库对加密文档只会抛出让人看不懂的 PackageNotFoundError / BadZipFile。

    Args:
        path: 文件路径
        expect: 期望的容器类型，目前只用 'zip'

    Raises:
        EncryptedDocumentError: 实际是 OLE2 —— 密码保护，或者是被改了扩展名的旧格式
        FileTypeMismatchError: 魔数与扩展名不符
    """
    kind = sniff_container(path)
    ext = Path(path).suffix.lower()
    if kind == 'ole2':
        raise EncryptedDocumentError(Path(path).name)
    if kind != expect:
        raise FileTypeMismatchError(ext, kind)


# ---- 取值与渲染 ----

def cell_to_str(value: Any) -> str:
    """把单元格/形状的任意取值转成单行字符串。"""
    if value is None:
        return ''
    # datetime 要在 date 之前判断（datetime 是 date 的子类）
    if hasattr(value, 'isoformat') and not isinstance(value, str):
        try:
            iso = value.isoformat()
            # 纯日期去掉补零的 00:00:00，读起来更干净
            if iso.endswith('T00:00:00'):
                iso = iso[:10]
            return iso
        except Exception:
            pass
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, bool):
        return 'TRUE' if value else 'FALSE'
    return _escape_cell(str(value))


def _escape_cell(text: str) -> str:
    """转义会破坏管道表格的字符，并把多行压成 <br>。"""
    text = text.strip()
    if not text:
        return ''
    text = text.replace('|', r'\|')
    text = re.sub(r'\r\n?|\n', '<br>', text)
    return text


def render_markdown_table(
    rows: Sequence[Sequence[Any]],
    *,
    max_rows: int = MAX_TABLE_ROWS,
    max_cols: int = TABLE_COL_CAP,
    header: Optional[Sequence[Any]] = None,
    total_rows: Optional[int] = None,
) -> str:
    """
    把二维数据渲染成 GFM 管道表格。

    保证：表格主体内不出现空行（否则 GFM 表格会解析中断）；截断说明以 blockquote
    形式追加在表格之后，与表格之间隔一个空行。

    Args:
        rows: **数据行**。header 为 None 时，rows[0] 会被当作表头并从数据里剔除。
        max_rows: 数据行上限（不含表头）
        max_cols: 列上限
        header: 显式表头；给了它，rows 就必须是**纯数据行**，不能再包含表头
        total_rows: 数据行的真实总数（用于截断说明）。默认取 len(rows)。
            只在调用方已经预先截断 rows、但知道真实总数时才有必要传。

    Returns:
        Markdown 文本；无有效数据时返回空串
    """
    normalized: List[List[str]] = [[cell_to_str(c) for c in row] for row in rows]
    if header is None:
        if not normalized:
            return ''
        normalized_header = normalized[0]
        normalized = normalized[1:]
    else:
        normalized_header = [cell_to_str(c) for c in header]

    # 去掉尾部整行为空的行
    while normalized and not any(normalized[-1]):
        normalized.pop()
    if not normalized_header and not normalized:
        return ''

    # 有效的列数 = 最后一个出现过非空值的列往后一位
    total_cols = max([len(normalized_header)] + [len(r) for r in normalized])
    last_used = -1
    for idx in range(total_cols):
        if idx < len(normalized_header) and normalized_header[idx]:
            last_used = idx
            continue
        if any(idx < len(r) and r[idx] for r in normalized):
            last_used = idx
    used_cols = last_used + 1
    if used_cols <= 0:
        return ''

    cols_truncated = used_cols > max_cols
    keep_cols = min(used_cols, max_cols)

    def _pad(row: List[str]) -> List[str]:
        return (row + [''] * keep_cols)[:keep_cols]

    header_cells = _pad(normalized_header)
    if not any(header_cells):
        # GFM 表格必须有表头行，表头全空时补占位
        header_cells = [f'列{i + 1}' for i in range(keep_cols)]

    known_total = total_rows if total_rows is not None else len(normalized)
    body = normalized[:max_rows]
    rows_truncated = known_total > len(body)

    lines = ['| ' + ' | '.join(header_cells) + ' |',
             '| ' + ' | '.join(['---'] * keep_cols) + ' |']
    for row in body:
        lines.append('| ' + ' | '.join(_pad(row)) + ' |')

    table = '\n'.join(lines)

    if rows_truncated or cols_truncated:
        table += '\n\n' + note(t('parse.tableTruncated',
                                 rows=known_total,
                                 cols=used_cols,
                                 shown_rows=len(body),
                                 shown_cols=keep_cols))
    return table


def drop_consecutive_blank_blocks(blocks: Sequence[str]) -> List[str]:
    """
    去掉空块与连续空行块，保证拼出来的文本不出现 `\\n{3,}`。

    各解析器统一用它来组装最终输出。
    """
    out: List[str] = []
    for block in blocks:
        if block is None:
            continue
        text = block.strip('\n')
        if not text.strip():
            continue
        out.append(text)
    return out


def join_blocks(blocks: Sequence[str]) -> str:
    """用恰好一个空行拼接块，保证不产生连续 3 个以上换行。"""
    return '\n\n'.join(drop_consecutive_blank_blocks(blocks))


def heading(level: int, text: str) -> str:
    """生成 ATX 标题，层数夹在 1~6。"""
    level = max(1, min(6, level))
    return '#' * level + ' ' + text.strip()


def note(text: str) -> str:
    """生成 blockquote 形式的说明行。"""
    return f'> （{text}）'
