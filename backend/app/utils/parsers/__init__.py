"""
多格式文档解析入口
-----------------
按扩展名分派到具体解析器，统一产出 Markdown 文本（格式约定见 base.py）。

各解析器模块一律**惰性 import**：只装了 python-docx 的环境不该因为解析 PPT
而整个应用起不来；缺失的库会变成单个文件的错误，而不是 500。
"""

from pathlib import Path
from typing import Set

from .base import (  # noqa: F401  (对外转出，供 file_parser 与调用方使用)
    ExtractionError,
    ExtractionResult,
    FileTypeMismatchError,
    LegacyOfficeFormatError,
    UnsupportedFormatError,
    LEGACY_OFFICE_TARGETS,
)
TEXT_EXTENSIONS: Set[str] = {'.txt', '.md', '.markdown'}
CSV_EXTENSIONS: Set[str] = {'.csv'}
PDF_EXTENSIONS: Set[str] = {'.pdf'}
OFFICE_EXTENSIONS: Set[str] = {'.docx', '.pptx', '.xlsx', '.xlsm'}
IMAGE_EXTENSIONS: Set[str] = {
    '.png', '.jpg', '.jpeg', '.heic', '.heif',
    '.tiff', '.tif', '.bmp', '.webp', '.gif',
}

#: 能真正解析的扩展名（带点小写）
SUPPORTED_EXTENSIONS: Set[str] = (
    TEXT_EXTENSIONS | CSV_EXTENSIONS | PDF_EXTENSIONS | OFFICE_EXTENSIONS | IMAGE_EXTENSIONS
)

#: 能识别但不解析的旧版 Office 扩展名（带点小写）
LEGACY_EXTENSIONS: Set[str] = set(LEGACY_OFFICE_TARGETS)


def classify(filename: str) -> str:
    """
    判断文件属于哪一类。

    Returns:
        'supported' —— 能解析
        'legacy'    —— 旧版 Office，会给出「另存为」提示
        'unsupported' —— 其他
    """
    ext = Path(filename or '').suffix.lower()
    if ext in SUPPORTED_EXTENSIONS:
        return 'supported'
    if ext in LEGACY_EXTENSIONS:
        return 'legacy'
    return 'unsupported'


def explain_unsupported(filename: str) -> str:
    """给不支持的文件生成一句可直接展示给用户的说明。"""
    ext = Path(filename or '').suffix.lower()
    if ext in LEGACY_OFFICE_TARGETS:
        return str(LegacyOfficeFormatError(ext, LEGACY_OFFICE_TARGETS[ext]))
    return str(UnsupportedFormatError(ext))


def extract(file_path: str) -> ExtractionResult:
    """
    解析单个文件。

    Raises:
        FileNotFoundError: 文件不存在
        ExtractionError: 不支持的格式，或解析失败（str(exc) 面向用户）
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f'文件不存在: {file_path}')

    ext = path.suffix.lower()

    # 旧版 Office：明确不做解析，给出转换提示
    if ext in LEGACY_OFFICE_TARGETS:
        raise LegacyOfficeFormatError(ext, LEGACY_OFFICE_TARGETS[ext])

    if ext in TEXT_EXTENSIONS or ext in CSV_EXTENSIONS:
        from . import text_parser
        if ext in CSV_EXTENSIONS:
            return text_parser.extract_csv(file_path)
        return text_parser.extract_text_file(file_path)

    if ext in PDF_EXTENSIONS:
        from . import pdf_parser
        return pdf_parser.extract_pdf(file_path)

    if ext in OFFICE_EXTENSIONS:
        from . import office_parser
        if ext == '.docx':
            return office_parser.extract_docx(file_path)
        if ext == '.pptx':
            return office_parser.extract_pptx(file_path)
        return office_parser.extract_xlsx(file_path)

    if ext in IMAGE_EXTENSIONS:
        from . import image_parser
        return image_parser.extract_image(file_path)

    raise UnsupportedFormatError(ext)


def extract_text(file_path: str) -> str:
    """只要文本的便捷入口。"""
    return extract(file_path).text
