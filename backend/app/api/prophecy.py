"""
水晶预言球 API

从报告铸造预言球，并支持开启、问卜、应验校验与预言厅统计。
"""
import traceback

from flask import request, jsonify

from . import prophecy_bp
from ..services.prophecy_service import ProphecyService
from ..utils.locale import t
from ..utils.logger import get_logger

logger = get_logger('macfish.api.prophecy')


def _error(message, status=400, exc=None):
    payload = {"success": False, "error": message}
    if exc is not None:
        payload["traceback"] = traceback.format_exc()
    return jsonify(payload), status


def _map_exception(exc):
    if isinstance(exc, LookupError):
        return _error(str(exc), 404)
    if isinstance(exc, PermissionError):
        return _error(str(exc), 409)
    if isinstance(exc, ValueError):
        return _error(str(exc), 400)
    logger.error("预言球接口异常: %s", exc)
    return _error(str(exc), 500, exc)


@prophecy_bp.route('/overview', methods=['GET'])
def overview():
    try:
        return jsonify({"success": True, "data": ProphecyService.overview()})
    except Exception as exc:
        logger.error("读取预言厅总览失败: %s", exc)
        return _error(str(exc), 500, exc)


@prophecy_bp.route('/calibration', methods=['GET'])
def calibration():
    """预言球的 Brier / log loss / 可靠性分桶。"""
    try:
        return jsonify({"success": True, "data": ProphecyService.calibration()})
    except Exception as exc:
        logger.error("读取预言球校准失败: %s", exc)
        return _error(str(exc), 500, exc)


@prophecy_bp.route('/reminders', methods=['GET'])
def reminders():
    """即将到期/已过期的预言球，用于页面横幅与导航角标。"""
    try:
        days = request.args.get('days', 7, type=int)
        return jsonify({"success": True, "data": ProphecyService.reminders(days)})
    except Exception as exc:
        logger.error("读取预言球到期提醒失败: %s", exc)
        return _error(str(exc), 500, exc)


@prophecy_bp.route('/orbs/forge', methods=['POST'])
def forge():
    """从一份报告铸造预言球。同一报告幂等。"""
    try:
        data = request.get_json(silent=True) or {}
        report_id = data.get('report_id') or ''
        result = ProphecyService.forge(report_id)
        return jsonify({"success": True, "data": result})
    except Exception as exc:
        return _map_exception(exc)


@prophecy_bp.route('/orbs', methods=['GET'])
def list_orbs():
    try:
        items = ProphecyService.list_orbs(
            status=request.args.get('status', ''),
            source_report_id=request.args.get('source_report_id', ''),
        )
        return jsonify({"success": True, "data": items, "count": len(items)})
    except Exception as exc:
        return _map_exception(exc)


@prophecy_bp.route('/orbs/<orb_id>', methods=['GET'])
def get_orb(orb_id: str):
    try:
        orb = ProphecyService.get_orb(orb_id)
        if not orb:
            return _error(t('api.prophecyNotFound', id=orb_id), 404)
        return jsonify({"success": True, "data": orb})
    except Exception as exc:
        return _map_exception(exc)


@prophecy_bp.route('/orbs/<orb_id>/open', methods=['POST'])
def open_orb(orb_id: str):
    try:
        orb = ProphecyService.open_orb(orb_id)
        if not orb:
            return _error(t('api.prophecyNotFound', id=orb_id), 404)
        return jsonify({"success": True, "data": orb})
    except Exception as exc:
        return _map_exception(exc)


@prophecy_bp.route('/orbs/<orb_id>/scry', methods=['POST'])
def scry(orb_id: str):
    try:
        data = request.get_json(silent=True) or {}
        question = data.get('question') or ''
        result = ProphecyService.scry(orb_id, question)
        return jsonify({"success": True, "data": result})
    except Exception as exc:
        return _map_exception(exc)


@prophecy_bp.route('/orbs/<orb_id>/verify', methods=['POST'])
def verify(orb_id: str):
    try:
        data = request.get_json(silent=True) or {}
        outcome = data.get('outcome') or ''
        verdict = data.get('verdict') or ''
        accuracy = data.get('accuracy')
        notes = data.get('notes') or ''
        orb = ProphecyService.verify(orb_id, outcome, verdict=verdict,
                                     accuracy=accuracy, notes=notes)
        if not orb:
            return _error(t('api.prophecyNotFound', id=orb_id), 404)
        return jsonify({"success": True, "data": orb})
    except Exception as exc:
        return _map_exception(exc)


@prophecy_bp.route('/orbs/<orb_id>', methods=['DELETE'])
def delete_orb(orb_id: str):
    try:
        deleted = ProphecyService.delete_orb(orb_id)
        if not deleted:
            return _error(t('api.prophecyNotFound', id=orb_id), 404)
        return jsonify({"success": True, "data": {"deleted": True}})
    except Exception as exc:
        return _map_exception(exc)
