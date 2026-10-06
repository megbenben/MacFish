"""
文件解析工具（门面层）
---------------------
实际的多格式解析实现放在 `utils/parsers/` 包里，本模块只保留对外入口，
以维持既有的 `FileParser.extract_text()` 契约（失败即抛）。

支持的格式：
- 文本类：.txt / .md / .markdown / .csv
- 文档类：.pdf（文字层，扫描页可选 OCR）/ .docx / .pptx / .xlsx / .xlsm
- 图片类：.png / .jpg / .jpeg / .heic / .heif / .tiff / .tif / .bmp / .webp / .gif（走 OCR）

旧版 Office（.doc / .ppt / .xls）不做解析，会抛出带「另存为」提示的
LegacyOfficeFormatError，由上传接口转成逐文件错误反馈给用户。
"""

from pathlib import Path
from typing import List

from . import parsers
from .parsers import (  # noqa: F401  (对外转出，方便调用方直接 import)
    ExtractionError,
    ExtractionResult,
    FileTypeMismatchError,
    LegacyOfficeFormatError,
    UnsupportedFormatError,
)
from .parsers.text_parser import read_text_with_fallback as _read_text_with_fallback  # noqa: F401


class FileParser:
    """文件解析器"""

    #: 能解析的扩展名（带点小写）
    SUPPORTED_EXTENSIONS = parsers.SUPPORTED_EXTENSIONS
    #: 旧版 Office 扩展名 → 建议另存的目标格式
    LEGACY_EXTENSIONS = parsers.LEGACY_OFFICE_TARGETS

    @classmethod
    def extract_text(cls, file_path: str) -> str:
        """
        从文件中提取文本。

        Args:
            file_path: 文件路径

        Returns:
            提取的文本内容

        Raises:
            FileNotFoundError: 文件不存在
            ExtractionError: 不支持的格式或解析失败（str(exc) 可直接展示给用户）
        """
        return parsers.extract(file_path).text

    @classmethod
    def extract_text_detailed(cls, file_path: str) -> ExtractionResult:
        """同上，但额外返回处理说明（走了哪个 OCR 引擎、哪张表被截断等）。"""
        return parsers.extract(file_path)

    @classmethod
    def classify(cls, filename: str) -> str:
        """'supported' | 'legacy' | 'unsupported'"""
        return parsers.classify(filename)

    @classmethod
    def is_supported(cls, filename: str) -> bool:
        return parsers.classify(filename) == 'supported'

    @classmethod
    def explain_unsupported(cls, filename: str) -> str:
        """给不支持的文件生成可直接展示的说明文案。"""
        return parsers.explain_unsupported(filename)

    @classmethod
    def extract_from_multiple(cls, file_paths: List[str]) -> str:
        """
        从多个文件提取文本并合并（失败的文件以行内标记形式保留）。

        Args:
            file_paths: 文件路径列表

        Returns:
            合并后的文本
        """
        all_texts = []

        for i, file_path in enumerate(file_paths, 1):
            try:
                text = cls.extract_text(file_path)
                filename = Path(file_path).name
                all_texts.append(f"=== 文档 {i}: {filename} ===\n{text}")
            except Exception as e:
                all_texts.append(f"=== 文档 {i}: {file_path} (提取失败: {str(e)}) ===")

        return "\n\n".join(all_texts)


def split_text_into_chunks(
    text: str, 
    chunk_size: int = 500, 
    overlap: int = 50
) -> List[str]:
    """
    将文本分割成小块
    
    Args:
        text: 原始文本
        chunk_size: 每块的字符数
        overlap: 重叠字符数
        
    Returns:
        文本块列表
    """
    if len(text) <= chunk_size:
        return [text] if text.strip() else []
    
    chunks = []
    start = 0
    
    while start < len(text):
        end = start + chunk_size
        
        # 尝试在句子边界处分割
        if end < len(text):
            # 查找最近的句子结束符
            for sep in ['。', '！', '？', '.\n', '!\n', '?\n', '\n\n', '. ', '! ', '? ']:
                last_sep = text[start:end].rfind(sep)
                if last_sep != -1 and last_sep > chunk_size * 0.3:
                    end = start + last_sep + len(sep)
                    break
        
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        
        # 下一个块从重叠位置开始
        start = end - overlap if end < len(text) else len(text)
    
    return chunks

