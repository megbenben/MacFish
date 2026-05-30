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
