"""
LLM 设置 API
提供运行时读取/修改 LLM provider 配置的能力
"""

from flask import request, jsonify

from . import settings_bp
from ..settings import runtime_settings
from ..config import Config
from ..utils.logger import get_logger

logger = get_logger('mirofish.api.settings')


@settings_bp.route('/llm', methods=['GET'])
def get_llm_settings():
    """获取当前 LLM 配置（含所有 provider 和当前激活项）"""
    try:
        full = runtime_settings.get_full_settings()
        return jsonify({"success": True, "data": full})
    except Exception as e:
        logger.error(f"获取 LLM 设置失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@settings_bp.route('/llm', methods=['PUT'])
def update_llm_settings():
    """
    更新 LLM 配置
    请求体示例:
    {
        "provider": "local",
        "network": {"base_url": "https://api.deepseek.com", "model_name": "deepseek-chat"},
        "local": {"base_url": "http://localhost:11434/v1", "model_name": "qwen2.5:7b"}
    }
    """
    try:
        data = request.get_json(silent=True) or {}
        runtime_settings.update_settings(data)
        # 返回更新后的完整配置
        full = runtime_settings.get_full_settings()
        return jsonify({"success": True, "data": full, "message": "配置已更新"})
    except Exception as e:
        logger.error(f"更新 LLM 设置失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@settings_bp.route('/llm/test', methods=['POST'])
def test_llm_connection():
    """
    测试指定 provider 的连接
    请求体: {"provider": "local"} 或 {"provider": "network"}
    不传 provider 则测试当前激活的 provider
    """
    try:
        data = request.get_json(silent=True) or {}
        provider = data.get('provider')
        result = runtime_settings.test_connection(provider)
        return jsonify({"success": result["ok"], "data": result})
    except Exception as e:
        logger.error(f"测试 LLM 连接失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------- 文档解析 / OCR ----------------

@settings_bp.route('/ocr', methods=['GET'])
def get_ocr_settings():
    """获取 OCR 配置与各引擎的可用性（密钥已脱敏）。"""
    try:
        from ..utils.ocr import ocr_status
        full = runtime_settings.get_full_settings()
        return jsonify({
            "success": True,
            "data": {
                "config": full.get('ocr', {}),
                "status": ocr_status(),
            },
        })
    except Exception as e:
        logger.error(f"获取 OCR 设置失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@settings_bp.route('/ocr', methods=['PUT'])
def update_ocr_settings():
    """
    更新 OCR 配置
    请求体示例:
    {
        "ocr": {
            "engine": "auto",
            "scan_pdf_fallback": false,
            "max_pages": 30,
            "api": {"api_key": "...", "base_url": "...", "model_name": "qwen-vl-max"}
        }
    }
    """
    try:
        data = request.get_json(silent=True) or {}
        ocr_data = data.get('ocr')
        if not isinstance(ocr_data, dict):
            ocr_data = data
        runtime_settings.update_settings({"ocr": ocr_data})

        from ..utils.ocr import ocr_status
        full = runtime_settings.get_full_settings()
        return jsonify({
            "success": True,
            "data": {"config": full.get('ocr', {}), "status": ocr_status()},
            "message": "配置已更新",
        })
    except Exception as e:
        logger.error(f"更新 OCR 设置失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@settings_bp.route('/ocr/test', methods=['POST'])
def test_ocr_engine():
    """
    用当前配置的引擎识别一张内存里现生成的测试图。

    这是用户唯一能从界面验证 OCR 接线（尤其是 pyobjc 是否装好）的方式。
    """
    try:
        from ..utils import ocr as ocr_module

        expected = 'MacFish OCR 测试 Test 123'
        image_bytes = _render_test_image(expected)

        result = ocr_module.recognize(image_bytes, hint='settings-test')
        text = (result.text or '').strip()
        ok = bool(text)
        return jsonify({
            "success": ok,
            "data": {
                "ok": ok,
                "engine": result.engine,
                "text": text,
                "message": text if ok else "OCR 未识别出任何文字",
            },
        })
    except Exception as e:
        logger.error(f"测试 OCR 失败: {e}")
        return jsonify({"success": False, "data": {"ok": False, "message": str(e)}})


def _render_test_image(text: str) -> bytes:
    """在内存里渲染一张测试图，不落盘。"""
    import io

    from PIL import Image, ImageDraw, ImageFont

    image = Image.new('RGB', (760, 120), 'white')
    draw = ImageDraw.Draw(image)
    font = None
    for candidate in (
        '/System/Library/Fonts/Supplemental/Arial Unicode.ttf',
        '/System/Library/Fonts/Helvetica.ttc',
    ):
        try:
            font = ImageFont.truetype(candidate, 36)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()

    draw.text((20, 40), text, font=font, fill='black')

    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()
