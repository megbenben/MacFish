"""
运行清单（Run Manifest）
-----------------------
为每一次推演记录一份可复现的元信息，随报告一起落盘。

存在的理由：MacFish 的输出此前完全不可复现——不知道用了哪个模型、什么温度、
哪一版材料，两次相同输入得到不同结果也无法审计。回测要成立，必须能回答
"这份分数对应的是哪一次配置"。

**硬约束：密钥绝不落盘。** 只记录 sha256 指纹（用于判断两次运行是否用了同一把
密钥），以及 base_url 的 host（不含路径与查询串）。这条由单元测试守着。
"""

import hashlib
import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from ..config import Config
from ..settings import runtime_settings
from ..utils.locale import get_locale

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = 'manifest.json'

#: 仓库根（用于采集 git 状态）
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))


def sha256_file(path: Optional[str]) -> Optional[str]:
    """文件的 sha256；文件不存在返回 None（回测里这是"产物缺失"的信号之一）。"""
    if not path or not os.path.isfile(path):
        return None
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(65536), b''):
            digest.update(block)
    return digest.hexdigest()


def fingerprint(secret: Optional[str]) -> Optional[str]:
    """给密钥做指纹：能比对是否同一把，但无法反推。"""
    if not secret:
        return None
    return hashlib.sha256(secret.encode('utf-8')).hexdigest()[:12]


def _host_only(url: Optional[str]) -> Optional[str]:
    """只取 host，丢掉路径/查询串——base_url 里有时会带 token。"""
    if not url:
        return None
    try:
        parsed = urlparse(url)
        return parsed.netloc or parsed.path or None
    except Exception:
        return None


def git_state() -> Optional[Dict[str, Any]]:
    """
    记录 git 状态。取不到就返回 None（不是错误——可能不在 git 仓库里）。

    刻意记录 dirty 与 diff 的 sha1：即便工作树不干净，有了这两个值
    仍然可以重建出当时的代码状态。
    """
    try:
        def run(*args: str) -> str:
            result = subprocess.run(
                ['git', *args], cwd=_REPO_ROOT,
                capture_output=True, text=True, timeout=10,
            )
            return result.stdout.strip() if result.returncode == 0 else ''

        commit = run('rev-parse', '--short', 'HEAD')
        if not commit:
            return None

        branch = run('rev-parse', '--abbrev-ref', 'HEAD')
        status = run('status', '--porcelain')
        diff = run('diff', 'HEAD')

        return {
            'commit': commit,
            'branch': branch or None,
            'dirty': bool(status),
            'tracked_diff_sha1': hashlib.sha1(diff.encode('utf-8')).hexdigest() if diff else None,
            'untracked_count': sum(1 for line in status.splitlines() if line.startswith('??')),
        }
    except Exception as exc:
        logger.debug('采集 git 状态失败（忽略）: %s', exc)
        return None


def build_manifest(
    *,
    project_id: Optional[str] = None,
    graph_id: Optional[str] = None,
    simulation_id: Optional[str] = None,
    report_id: Optional[str] = None,
    chunk_count: Optional[int] = None,
    entity_count: Optional[int] = None,
    profile_count: Optional[int] = None,
    total_rounds: Optional[int] = None,
    max_rounds: Optional[int] = None,
    platform: Optional[str] = None,
    config_path: Optional[str] = None,
    extracted_text_path: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    组装运行清单。

    Args:
        config_path: simulation_config.json 的路径（有则记录其 sha256）
        extracted_text_path: extracted_text.txt 的路径（有则记录其 sha256）
        extra: 调用方附加的字段（回测会用它塞 case_id / attempt 之类）
    """
    llm_cfg = runtime_settings.get_llm_config()

    manifest: Dict[str, Any] = {
        'schema_version': 1,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'locale': get_locale(),
        'ids': {
            'project_id': project_id,
            'graph_id': graph_id,
            'simulation_id': simulation_id,
            'report_id': report_id,
        },
        'llm': {
            'provider': runtime_settings._data.get('provider'),
            'model': llm_cfg.get('model'),
            'base_url_host': _host_only(llm_cfg.get('base_url')),
            # 只存指纹：可比对是否为同一把密钥，但无法反推
            'api_key_fingerprint': fingerprint(llm_cfg.get('api_key')),
        },
        'sampling': {
            'report_agent_temperature': Config.REPORT_AGENT_TEMPERATURE,
            'report_agent_max_tool_calls': Config.REPORT_AGENT_MAX_TOOL_CALLS,
            'report_agent_max_reflection_rounds': Config.REPORT_AGENT_MAX_REFLECTION_ROUNDS,
            'oasis_default_max_rounds': Config.OASIS_DEFAULT_MAX_ROUNDS,
        },
        'scale': {
            'chunk_size': Config.DEFAULT_CHUNK_SIZE,
            'chunk_overlap': Config.DEFAULT_CHUNK_OVERLAP,
            'chunk_count': chunk_count,
            'entity_count': entity_count,
            'profile_count': profile_count,
            'total_rounds': total_rounds,
            'max_rounds': max_rounds,
            'platform': platform,
            'enable_twitter': True,
            'enable_reddit': True,
        },
        'inputs': {
            'simulation_config_sha256': sha256_file(config_path),
            'extracted_text_sha256': sha256_file(extracted_text_path),
        },
        'git': git_state(),
    }

    if extra:
        manifest['extra'] = extra
    return manifest


def save_manifest(report_id: str, manifest: Dict[str, Any]) -> str:
    """写入 uploads/reports/{report_id}/manifest.json（原子写）。"""
    folder = os.path.join(Config.UPLOAD_FOLDER, 'reports', report_id)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, MANIFEST_FILENAME)

    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False, sort_keys=True)
    os.replace(tmp, path)
    return path


def load_manifest(report_id: str) -> Optional[Dict[str, Any]]:
    """读回清单；不存在或损坏返回 None。"""
    path = os.path.join(Config.UPLOAD_FOLDER, 'reports', report_id, MANIFEST_FILENAME)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            return json.load(handle)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning('读取 manifest 失败 %s: %s', report_id, exc)
        return None


# ---- 便利入口：把"去哪找产物"的知识集中在这里，让调用方保持精简 ----

def run_artifact_paths(simulation_id: str, project_id: str) -> Dict[str, Optional[str]]:
    """定位一次运行的关键产物文件。"""
    sim_dir = os.path.join(Config.OASIS_SIMULATION_DATA_DIR, simulation_id)
    proj_dir = os.path.join(Config.UPLOAD_FOLDER, 'projects', project_id)
    return {
        'simulation_dir': sim_dir,
        'config_path': os.path.join(sim_dir, 'simulation_config.json'),
        'extracted_text_path': os.path.join(proj_dir, 'extracted_text.txt'),
    }


def planned_rounds(simulation_id: str) -> Optional[int]:
    """从 simulation_config.json 读出未截断的计划轮数；读不到返回 None。"""
    path = os.path.join(Config.OASIS_SIMULATION_DATA_DIR, simulation_id, 'simulation_config.json')
    if not os.path.isfile(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            config = json.load(handle)
        time_config = config.get('time_config') or {}
        total_hours = float(time_config.get('total_simulation_hours', 0) or 0)
        minutes_per_round = float(time_config.get('minutes_per_round', 0) or 0)
        if total_hours <= 0 or minutes_per_round <= 0:
            return None
        return int(total_hours * 60 / minutes_per_round)
    except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
        logger.debug('读取计划轮数失败 %s: %s', simulation_id, exc)
        return None


def build_for_run(
    *,
    simulation_id: str,
    report_id: str,
    project=None,
    state=None,
    chunk_count: Optional[int] = None,
    max_rounds: Optional[int] = None,
    platform: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    为一次真实的报告生成组装清单。

    project / state 分别是 models.project.Project 与 SimulationState（可为 None，
    字段缺失时如实记 None，不要编造）。
    """
    project_id = getattr(project, 'project_id', None) or getattr(state, 'project_id', None)
    graph_id = getattr(state, 'graph_id', None) or getattr(project, 'graph_id', None)
    paths = run_artifact_paths(simulation_id, project_id) if project_id else {}

    return build_manifest(
        project_id=project_id,
        graph_id=graph_id,
        simulation_id=simulation_id,
        report_id=report_id,
        chunk_count=chunk_count,
        entity_count=getattr(state, 'entities_count', None),
        profile_count=getattr(state, 'profiles_count', None),
        total_rounds=planned_rounds(simulation_id),
        max_rounds=max_rounds,
        platform=platform,
        config_path=paths.get('config_path'),
        extracted_text_path=paths.get('extracted_text_path'),
        extra=extra,
    )

