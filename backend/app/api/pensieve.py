"""
冥想盆 API

记忆收割、检索、入盆回放、记忆瓶蒸馏与丝线网络。
"""
import traceback

from flask import request, jsonify

from . import pensieve_bp
from ..services.pensieve_service import PensieveService
from ..services.pensieve_store import PensieveStore
from ..utils.locale import t
from ..utils.logger import get_logger

logger = get_logger('macfish.api.pensieve')


def _error(message, status=400, exc=None):
    payload = {"success": False, "error": message}
    if exc is not None:
        payload["traceback"] = traceback.format_exc()
    return jsonify(payload), status


@pensieve_bp.route('/overview', methods=['GET'])
def overview():
    """冥想盆总览：计数 + 可收割的项目/模拟/报告。"""
    try:
        return jsonify({"success": True, "data": PensieveService.overview()})
    except Exception as exc:
        logger.error("读取冥想盆总览失败: %s", exc)
        return _error(str(exc), 500, exc)


@pensieve_bp.route('/harvest', methods=['POST'])
def harvest():
    """把已有产物收存为记忆。幂等：重复收割只更新不新增。"""
    try:
        data = request.get_json(silent=True) or {}
        scope = data.get('scope') or 'all'
        target_id = data.get('target_id') or ''
        result = PensieveService.harvest(scope=scope, target_id=target_id)
        return jsonify({"success": True, "data": result})
    except ValueError as exc:
        return _error(str(exc), 400)
    except Exception as exc:
        logger.error("收割记忆失败: %s", exc)
        return _error(str(exc), 500, exc)


@pensieve_bp.route('/memories', methods=['GET'])
def list_memories():
    """检索记忆。支持 q / kind / project_id / simulation_id / report_id。"""
    try:
        store = PensieveStore.get_instance()
        filters = {
            "q": request.args.get('q', ''),
            "kind": request.args.get('kind', ''),
            "project_id": request.args.get('project_id', ''),
            "simulation_id": request.args.get('simulation_id', ''),
            "report_id": request.args.get('report_id', ''),
            "source_type": request.args.get('source_type', ''),
        }
        limit = request.args.get('limit', 100, type=int)
        offset = request.args.get('offset', 0, type=int)
        items = store.list_memories(**filters, limit=limit, offset=offset)
        total = store.count_memories(**filters)
        return jsonify({
            "success": True,
            "data": items,
            "count": len(items),
            "total": total,
        })
    except Exception as exc:
        logger.error("检索记忆失败: %s", exc)
        return _error(str(exc), 500, exc)


@pensieve_bp.route('/memories/<memory_id>', methods=['GET'])
def get_memory(memory_id: str):
    try:
        detail = PensieveService.get_memory_detail(memory_id)
        if not detail:
            return _error(t('api.pensieveMemoryNotFound', id=memory_id), 404)
        return jsonify({"success": True, "data": detail})
    except Exception as exc:
        logger.error("读取记忆详情失败: %s", exc)
        return _error(str(exc), 500, exc)


@pensieve_bp.route('/memories/<memory_id>', methods=['DELETE'])
def delete_memory(memory_id: str):
    try:
        deleted = PensieveStore.get_instance().delete_memory(memory_id)
        if not deleted:
            return _error(t('api.pensieveMemoryNotFound', id=memory_id), 404)
        return jsonify({"success": True, "data": {"deleted": True}})
    except Exception as exc:
        logger.error("删除记忆失败: %s", exc)
        return _error(str(exc), 500, exc)


@pensieve_bp.route('/replay', methods=['POST'])
def replay():
    """入盆回放：返回场景、人物、时间线与第三人称旁白。"""
    try:
        data = request.get_json(silent=True) or {}
        memory_id = data.get('memory_id') or ''
        if not memory_id:
            return _error(t('api.pensieveMemoryRequired'), 400)
        scene = PensieveService.replay(memory_id)
        if not scene:
            return _error(t('api.pensieveMemoryNotFound', id=memory_id), 404)
        return jsonify({"success": True, "data": scene})
    except Exception as exc:
        logger.error("入盆回放失败: %s", exc)
        return _error(str(exc), 500, exc)


@pensieve_bp.route('/distill', methods=['POST'])
def distill():
    """把多条记忆蒸馏成一只记忆瓶。"""
    try:
        data = request.get_json(silent=True) or {}
        memory_ids = data.get('memory_ids') or []
        question = data.get('question') or ''
        vial = PensieveService.distill(memory_ids, question)
        return jsonify({"success": True, "data": vial})
    except ValueError as exc:
        return _error(str(exc), 400)
    except Exception as exc:
        logger.error("记忆蒸馏失败: %s", exc)
        return _error(str(exc), 500, exc)


@pensieve_bp.route('/vials', methods=['GET'])
def list_vials():
    try:
        items = PensieveStore.get_instance().list_vials(
            limit=request.args.get('limit', 50, type=int))
        return jsonify({"success": True, "data": items, "count": len(items)})
    except Exception as exc:
        logger.error("读取记忆瓶失败: %s", exc)
        return _error(str(exc), 500, exc)


@pensieve_bp.route('/vials/<vial_id>', methods=['GET'])
def get_vial(vial_id: str):
    try:
        vial = PensieveStore.get_instance().get_vial(vial_id)
        if not vial:
            return _error(t('api.pensieveVialNotFound', id=vial_id), 404)
        return jsonify({"success": True, "data": vial})
    except Exception as exc:
        logger.error("读取记忆瓶失败: %s", exc)
        return _error(str(exc), 500, exc)


@pensieve_bp.route('/vials/<vial_id>', methods=['DELETE'])
def delete_vial(vial_id: str):
    try:
        deleted = PensieveStore.get_instance().delete_vial(vial_id)
        if not deleted:
            return _error(t('api.pensieveVialNotFound', id=vial_id), 404)
        return jsonify({"success": True, "data": {"deleted": True}})
    except Exception as exc:
        logger.error("删除记忆瓶失败: %s", exc)
        return _error(str(exc), 500, exc)


@pensieve_bp.route('/network', methods=['GET'])
def network():
    """银色丝线网络（节点 + 边）。"""
    try:
        project_id = request.args.get('project_id', '')
        limit = request.args.get('limit', 300, type=int)
        data = PensieveStore.get_instance().get_network(project_id=project_id, limit=limit)
        return jsonify({"success": True, "data": data})
    except Exception as exc:
        logger.error("读取丝线网络失败: %s", exc)
        return _error(str(exc), 500, exc)
