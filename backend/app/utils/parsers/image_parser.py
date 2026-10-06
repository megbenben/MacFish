"""
图片解析：走 OCR 把图片里的文字提取出来
--------------------------------------
支持的格式由 OCR 引擎决定。本地 Vision（ImageIO）原生支持
PNG / JPEG / TIFF / BMP / GIF / HEIC；视觉 API 支持 PNG / JPEG / TIFF / BMP / GIF。
RAW（CR2 / NEF / ARW 等）两者都不支持，会得到明确的报错。
"""

from pathlib import Path

from .. import ocr as ocr_module
from ..locale import t
from . import base
from .base import ExtractionResult


def extract_image(path: str) -> ExtractionResult:
    """图片 —— OCR 出文字，包成带标题的 Markdown。"""
    image_bytes = Path(path).read_bytes()

    # 识别失败（引擎不可用 / 格式不支持）会抛 OCRError，
    # 由上传路由转成逐文件错误 —— 让用户看到「需启用 OCR」而不是静默跳过
    result = ocr_module.recognize(image_bytes, hint=Path(path).name)

    text = (result.text or '').strip()
    if not text:
        return ExtractionResult(text='', notes=list(result.notes))

    body = base.join_blocks([
        base.heading(2, t('parse.imageOcrHeading', engine=result.engine)),
        text,
    ])
    return ExtractionResult(text=body, notes=[f'ocr:{result.engine}'] + list(result.notes))
