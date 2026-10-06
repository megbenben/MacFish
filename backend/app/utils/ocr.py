"""
OCR 引擎（可切换）
-----------------
两个引擎共用一套接口：

- ``MacVisionOCREngine`` —— macOS 原生 Vision 框架。纯本地、免费、无需密钥，
  中英文混排识别准确，模型随系统自带。需要 pyobjc-framework-Vision/Quartz。
- ``ApiVisionOCREngine`` —— OpenAI 兼容的视觉大模型（默认指向阿里百炼 qwen-vl-max）。
  对图表语义的理解更好，但需要额外的密钥和费用。

接口收 **bytes 而不是路径**：同一个接口要同时服务独立图片文件、PDF 渲染出的页面、
以及将来的 pptx 内嵌图，谁都不必落临时文件。

引擎选择策略见 ``_candidate_engines()``：
- ``off``    —— 不使用 OCR（图片会得到明确的报错，而不是被静默跳过）
- ``vision`` —— 只用本地；不可用时若配了视觉 API 密钥则降级过去并记录说明
- ``api``    —— 只用视觉 API；**不**静默改用本地（用户明确要求了引擎，悄悄换掉比报错更糟）
- ``auto``   —— 默认。本地优先，其次视觉 API
"""

import base64
import importlib.util
import logging
import sys
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Sequence

from .locale import t
from .parsers.base import ExtractionError

logger = logging.getLogger(__name__)


class OCRError(ExtractionError):
    """
    OCR 不可用或识别失败。str(exc) 直接面向用户。

    继承 ExtractionError（进而是 ValueError），这样任何捕获文档解析异常的调用方
    都会一并覆盖 OCR 失败，不必再记一条特例。
    """


class OCRResult:
    """一次 OCR 的结果。"""

    __slots__ = ('text', 'engine', 'notes')

    def __init__(self, text: str, engine: str, notes: Optional[List[str]] = None):
        self.text = text
        self.engine = engine
        self.notes = notes or []


class OCREngine(ABC):
    """OCR 引擎接口。"""

    name: str = 'base'

    @abstractmethod
    def is_available(self) -> bool:
        """引擎当前是否可用（依赖是否装齐、密钥是否配置）。绝不抛异常。"""

    @abstractmethod
    def recognize(self, image_bytes: bytes, *, hint: str = '') -> OCRResult:
        """识别图片中的文字。失败时抛 OCRError。"""


# ---------------- 本地引擎：macOS Vision ----------------

class MacVisionOCREngine(OCREngine):
    """基于 macOS Vision 框架的本地 OCR。"""

    name = 'vision'

    def __init__(self, languages: Optional[Sequence[str]] = None):
        self.languages = list(languages or ('zh-Hans', 'en-US'))

    def is_available(self) -> bool:
        if sys.platform != 'darwin':
            return False
        try:
            return (importlib.util.find_spec('Vision') is not None
                    and importlib.util.find_spec('Quartz') is not None)
        except (ImportError, ValueError):
            return False

    def recognize(self, image_bytes: bytes, *, hint: str = '') -> OCRResult:
        if not image_bytes:
            raise OCRError(t('api.ocrFailed', engine=self.name, error='empty image'))

        try:
            import Quartz
            import Vision
        except ImportError as exc:
            raise OCRError(t('api.ocrEngineUnavailable', engine=self.name)) from exc

        try:
            # 直接喂 bytes：ImageIO 支持 PNG/JPEG/TIFF/BMP/GIF/HEIC，无需落盘
            source = Quartz.CGImageSourceCreateWithData(image_bytes, None)
            if source is None:
                raise OCRError(t('api.ocrFailed', engine=self.name, error='unsupported image data'))
            cg_image = Quartz.CGImageSourceCreateImageAtIndex(source, 0, None)
            if cg_image is None:
                # RAW（CR2/NEF/ARW）等格式会走到这里
                raise OCRError(t('api.ocrFailed', engine=self.name, error='unreadable image'))

            # 每次调用都新建 request：run.py 是 threaded=True，
            # Vision 的 handler/request 不可跨线程复用
            handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg_image, None)
            request = Vision.VNRecognizeTextRequest.alloc().init()
            request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
            request.setRecognitionLanguages_(self.languages)
            request.setUsesLanguageCorrection_(True)

            ok, err = handler.performRequests_error_([request], None)
            if not ok:
                raise OCRError(t('api.ocrFailed', engine=self.name, error=str(err)))
        except OCRError:
            raise
        except Exception as exc:
            raise OCRError(t('api.ocrFailed', engine=self.name, error=str(exc))) from exc

        lines: List[str] = []
        for observation in request.results() or []:
            candidates = observation.topCandidates_(1)
            if candidates and len(candidates):
                lines.append(candidates[0].string())

        return OCRResult('\n'.join(lines), self.name)


# ---------------- 远程引擎：OpenAI 兼容的视觉模型 ----------------

_MIME_SIGNATURES = (
    (b'\x89PNG\r\n\x1a\n', 'image/png'),
    (b'\xff\xd8\xff', 'image/jpeg'),
    (b'GIF87a', 'image/gif'),
    (b'GIF89a', 'image/gif'),
    (b'BM', 'image/bmp'),
    (b'II*\x00', 'image/tiff'),
    (b'MM\x00*', 'image/tiff'),
)

_OCR_PROMPT = (
    "请逐字转录这张图片中的全部文字。"
    "保留原有的标题层级、列表和表格结构；表格用 Markdown 管道表格输出。"
    "只输出转录结果本身，不要添加任何解释、评论或开场白。"
)


class ApiVisionOCREngine(OCREngine):
    """基于 OpenAI 兼容视觉模型的 OCR。"""

    name = 'api'

    def __init__(self, api_key: str = '', base_url: str = '', model_name: str = ''):
        self.api_key = (api_key or '').strip()
        self.base_url = base_url or 'https://dashscope.aliyuncs.com/compatible-mode/v1'
        self.model_name = model_name or 'qwen-vl-max'

    def is_available(self) -> bool:
        # 必须先查密钥非空：LLMClient 在 key 为空时会静默回退到当前 provider 的密钥，
        # 那样就会把图片发给 deepseek-chat
        return bool(self.api_key)

    @staticmethod
    def _mime(image_bytes: bytes) -> Optional[str]:
        for sig, mime in _MIME_SIGNATURES:
            if image_bytes.startswith(sig):
                return mime
        return None

    def recognize(self, image_bytes: bytes, *, hint: str = '') -> OCRResult:
        if not self.is_available():
            raise OCRError(t('api.ocrEngineUnavailable', engine=self.name))

        mime = self._mime(image_bytes)
        if mime is None:
            # HEIC/RAW 没有解码器（不引入 pillow-heif）：如实报错，
            # 让 auto 模式回退到本地 Vision
            raise OCRError(t('api.ocrFailed', engine=self.name,
                             error='unsupported image format for API engine'))

        # 用显式参数构造，绝不能写 provider='vision'：
        # RuntimeSettings.get_llm_config 对未知 provider 会静默返回 network 配置
        from .llm_client import LLMClient

        client = LLMClient(api_key=self.api_key, base_url=self.base_url, model=self.model_name)

        data_url = f"data:{mime};base64,{base64.b64encode(image_bytes).decode('ascii')}"
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": _OCR_PROMPT},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }]

        try:
            # 复用 LLMClient.chat，顺带得到 <think> 标签清理（Qwen-VL 之类会吐推理标签）
            text = client.chat(messages, temperature=0.0, max_tokens=4096)
        except Exception as exc:
            raise OCRError(t('api.ocrFailed', engine=self.name, error=str(exc))) from exc

        return OCRResult(text or '', self.name)


# ---------------- 引擎解析与调度 ----------------

_ENGINE_CLASSES = {
    MacVisionOCREngine.name: MacVisionOCREngine,
    ApiVisionOCREngine.name: ApiVisionOCREngine,
}

# 按配置指纹缓存引擎实例，避免每一页都重新探测依赖
_ENGINE_CACHE: Dict[tuple, OCREngine] = {}


def _build_engine(name: str, cfg: Dict[str, Any]) -> OCREngine:
    if name == MacVisionOCREngine.name:
        return MacVisionOCREngine(languages=cfg.get('vision', {}).get('languages'))
    api_cfg = cfg.get('api', {})
    return ApiVisionOCREngine(
        api_key=api_cfg.get('api_key', ''),
        base_url=api_cfg.get('base_url', ''),
        model_name=api_cfg.get('model_name', ''),
    )


def _get_engine(name: str, cfg: Dict[str, Any]) -> OCREngine:
    api_cfg = cfg.get('api', {})
    key = (name, api_cfg.get('api_key', ''), api_cfg.get('base_url', ''), api_cfg.get('model_name', ''))
    engine = _ENGINE_CACHE.get(key)
    if engine is None:
        engine = _build_engine(name, cfg)
        _ENGINE_CACHE[key] = engine
    return engine


def _candidate_engines(mode: str, cfg: Dict[str, Any]) -> List[OCREngine]:
    """按配置给出候选引擎的有序列表。"""
    vision = _get_engine(MacVisionOCREngine.name, cfg)
    api = _get_engine(ApiVisionOCREngine.name, cfg)

    if mode == 'vision':
        # 用户点名本地：可以在不可用时降级到已配好的视觉 API
        return [vision, api] if api.is_available() else [vision]
    if mode == 'api':
        # 用户点名 API：不静默改用本地
        return [api]
    return [vision, api]


def get_ocr_config() -> Dict[str, Any]:
    """读取当前的 OCR 配置（来自 RuntimeSettings）。"""
    from ..settings import runtime_settings
    return runtime_settings.get_ocr_config()


def recognize(image_bytes: bytes, *, hint: str = '') -> OCRResult:
    """
    用当前配置的引擎识别图片文字。

    Raises:
        OCRError: 所有候选引擎都不可用或全部失败
    """
    cfg = get_ocr_config()
    mode = cfg.get('engine', 'auto')

    if mode == 'off':
        raise OCRError(t('api.ocrUnavailable'))

    candidates = _candidate_engines(mode, cfg)
    reasons: List[str] = []
    for engine in candidates:
        if not engine.is_available():
            reasons.append(t('api.ocrEngineUnavailable', engine=engine.name))
            continue
        try:
            result = engine.recognize(image_bytes, hint=hint)
        except OCRError as exc:
            reasons.append(str(exc))
            continue
        # 点名 vision 却由 API 兜底时，把降级事实记录下来
        if mode == 'vision' and engine.name != MacVisionOCREngine.name:
            result.notes.append('ocr-fallback:' + engine.name)
        return result

    # 没有任何引擎可用
    if mode == 'auto' and not any(e.is_available() for e in candidates):
        raise OCRError(t('api.ocrUnavailable'))
    detail = '; '.join(r for r in reasons if r)
    raise OCRError(detail or t('api.ocrUnavailable'))


def ocr_status() -> Dict[str, Any]:
    """给设置界面用的引擎可用性快照。只做探测，不做识别。"""
    cfg = get_ocr_config()
    vision = _get_engine(MacVisionOCREngine.name, cfg)
    api = _get_engine(ApiVisionOCREngine.name, cfg)
    return {
        'engine': cfg.get('engine', 'auto'),
        'scan_pdf_fallback': bool(cfg.get('scan_pdf_fallback')),
        'max_pages': cfg.get('max_pages'),
        'vision': vision.is_available(),
        'api': api.is_available(),
    }
