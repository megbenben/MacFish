"""
LLM 调用量预估与预算护栏
-----------------------
启动一次推演之前，先算清楚它会发起多少次 LLM 调用。

**只报调用次数，不猜价格。** 硬编码单价会随供应商与模型变动而失真，而次数是
客观的——用户自己乘自己那份价目表即可。

公式全部来自真实实现，各处的出处见 `_FORMULA_NOTES`。其中模拟阶段是绝对大头，
且它是**上界**：`total_rounds × Agent 数` 假设每个 Agent 每轮都行动，而实际上
`get_active_agents_for_round()` 会按活跃度与时段筛掉一部分。宁可高估也不要低估，
所以这里保留上界，并把 `activity_factor` 暴露出来供后续按实测校准。
"""

import logging
import math
import os
from typing import Any, Dict, List, Optional

from ..config import Config
from . import run_manifest

logger = logging.getLogger(__name__)


# ---- 公式参数（集中在顶部，便于按实测校准）----

#: 图谱抽取：每个 chunk 一次 chat_json
#: 出处：DeepSeekGraphExtractor.extract_from_batch 对每个文本调一次 chat_json
CALLS_PER_CHUNK = 1

#: 模拟配置：1 次时间配置 + 1 次事件配置 + ceil(实体数 / 每批 Agent 数) 次 Agent 配置
#: 出处：SimulationConfigGenerator.generate_config 的分阶段调用
CONFIG_FIXED_CALLS = 2
AGENTS_PER_BATCH = 15

#: 人设：每个实体一次（失败会重试，但重试次数不确定，这里按成功路径计）
CALLS_PER_ENTITY = 1

#: 报告：1 次大纲 + 每章 (max_iterations 次主循环 + 每次工具调用的额外开销)
#: 出处：ReportAgent 的 max_iterations = 5、MAX_TOOL_CALLS_PER_SECTION = 5；
#: insight_forge / interview_agents 这类工具自身还会再调 LLM，故给每次工具调用记 2 次开销
REPORT_OUTLINE_CALLS = 1
REPORT_SECTION_MAX_ITERATIONS = 5
REPORT_TOOL_CALLS_PER_SECTION = 5
REPORT_TOOL_OVERHEAD_PER_CALL = 2

#: 报告章节数的初值（真实值由 LLM 在大纲阶段决定，这里是保守估计）
DEFAULT_SECTION_COUNT = 4

#: 模拟阶段的活跃度折算系数。1.0 = 上界（每个 Agent 每轮都行动）
DEFAULT_ACTIVITY_FACTOR = 1.0

_FORMULA_NOTES = {
    'ontology': '1 次：OntologyGenerator.generate -> chat_json',
    'graph': f'{CALLS_PER_CHUNK} 次/chunk：DeepSeekGraphExtractor 对每条文本一次 chat_json',
    'profiles': f'{CALLS_PER_ENTITY} 次/实体：OasisProfileGenerator 每个实体的 LLM 人设（失败会重试）',
    'config': f'{CONFIG_FIXED_CALLS} + ceil(实体数/{AGENTS_PER_BATCH})：时间配置 + 事件配置 + 分批 Agent 配置',
    'simulation': f'轮数 × Agent 数 × 平台数 × 活跃度系数({DEFAULT_ACTIVITY_FACTOR})：上界估计，每个 Agent 每轮一次动作 LLM 调用',
    'report': f'{REPORT_OUTLINE_CALLS} + 章节数 × ({REPORT_SECTION_MAX_ITERATIONS} + {REPORT_TOOL_CALLS_PER_SECTION}×{REPORT_TOOL_OVERHEAD_PER_CALL})：大纲 + 每章 ReACT',
}


def estimate_calls(
    *,
    chunk_count: int = 0,
    entity_count: int = 0,
    total_rounds: int = 0,
    platform_count: int = 2,
    section_count: int = DEFAULT_SECTION_COUNT,
    activity_factor: float = DEFAULT_ACTIVITY_FACTOR,
) -> Dict[str, Any]:
    """
    纯函数：由规模参数推算各阶段的 LLM 调用次数。

    Args:
        chunk_count: 图谱阶段的文本块数
        entity_count: 变成 Agent 的实体数（同时也等于人设数）
        total_rounds: 模拟轮数
        platform_count: 平台数（parallel = 2）
        section_count: 预计报告章节数

    Returns:
        {'stages': {...}, 'total': int, 'upper_bound_stages': [...], 'assumptions': {...}}
    """
    chunk_count = max(int(chunk_count or 0), 0)
    entity_count = max(int(entity_count or 0), 0)
    total_rounds = max(int(total_rounds or 0), 0)
    platform_count = max(int(platform_count or 0), 0)

    stages = {
        'ontology': 1,
        'graph': chunk_count * CALLS_PER_CHUNK,
        'profiles': entity_count * CALLS_PER_ENTITY,
        'config': CONFIG_FIXED_CALLS + math.ceil(entity_count / AGENTS_PER_BATCH) if entity_count else CONFIG_FIXED_CALLS,
        'simulation': int(total_rounds * entity_count * platform_count * activity_factor),
        'report': REPORT_OUTLINE_CALLS + section_count * (
            REPORT_SECTION_MAX_ITERATIONS + REPORT_TOOL_CALLS_PER_SECTION * REPORT_TOOL_OVERHEAD_PER_CALL
        ),
    }

    return {
        'stages': stages,
        'total': sum(stages.values()),
        # 模拟阶段是上界而非精确值，调用方展示时要如实标注
        'upper_bound_stages': ['simulation'],
        'formula_notes': _FORMULA_NOTES,
        'assumptions': {
            'chunk_count': chunk_count,
            'entity_count': entity_count,
            'total_rounds': total_rounds,
            'platform_count': platform_count,
            'section_count': section_count,
            'activity_factor': activity_factor,
        },
    }


def _completed_stages(simulation_id: str, project_id: Optional[str]) -> List[str]:
    """
    依据磁盘产物判断哪些阶段已经跑完——预算判断必须只看**剩余**开销。

    判定尽量保守：拿不准就算作未完成（宁可多估）。
    """
    completed: List[str] = []
    paths = run_manifest.run_artifact_paths(simulation_id, project_id or '')

    if os.path.isfile(paths.get('extracted_text_path') or ''):
        completed.append('ontology')

    # 图谱是否已建：以 simulation_config.json 里记的 graph_id + 图谱库的节点数为准过于
    # 绕，这里用"配置已生成"作为图谱也已完成的下游证据（prepare 必须在图谱之后）。
    if os.path.isfile(paths.get('config_path') or ''):
        completed.extend(['graph', 'profiles', 'config'])

    return completed


def estimate_for_simulation(
    simulation_id: str,
    *,
    max_rounds: Optional[int] = None,
    platform: str = 'parallel',
    section_count: int = DEFAULT_SECTION_COUNT,
) -> Dict[str, Any]:
    """
    针对一次具体模拟的预估。

    Returns:
        在 estimate_calls 的结果上追加：
        - completed_stages: 已完成的阶段
        - remaining_stages / remaining_total: 还没跑的部分（预算判断用后者）
        - entity_count_source / total_rounds_source: 各参数是实测还是假设
    """
    from .simulation_manager import SimulationManager

    state = SimulationManager().get_simulation(simulation_id)
    if state is None:
        raise ValueError(f'模拟不存在: {simulation_id}')

    entity_count = int(state.entities_count or 0)
    entity_source = 'state.entities_count' if entity_count else 'unknown(0)'

    # 实体数为 0 说明还没 prepare（或图谱里没有可用实体），此时退回图谱探针
    graph_id = state.graph_id
    if entity_count == 0 and graph_id:
        try:
            from .zep_entity_reader import ZepEntityReader
            probe = ZepEntityReader().filter_defined_entities(graph_id, enrich_with_edges=False)
            entity_count = int(probe.filtered_count)
            entity_source = 'graph probe'
        except Exception as exc:
            logger.debug('实体数探针失败（按 0 处理）: %s', exc)

    # 轮数：优先用配置里的真实计划值，其次 max_rounds，最后系统默认
    rounds = run_manifest.planned_rounds(simulation_id)
    if rounds is not None and max_rounds:
        rounds = min(rounds, int(max_rounds))
        rounds_source = 'simulation_config.json (clamped by max_rounds)'
    elif rounds is not None:
        rounds_source = 'simulation_config.json'
    elif max_rounds:
        rounds = int(max_rounds)
        rounds_source = 'max_rounds'
    else:
        rounds = int(Config.OASIS_DEFAULT_MAX_ROUNDS)
        rounds_source = 'OASIS_DEFAULT_MAX_ROUNDS'

    platform_count = 1 if platform in ('twitter', 'reddit') else 2

    result = estimate_calls(
        chunk_count=0,  # 图谱阶段若已完成则不计入剩余；未完成时按 0 保守处理
        entity_count=entity_count,
        total_rounds=rounds,
        platform_count=platform_count,
        section_count=section_count,
    )

    completed = _completed_stages(simulation_id, state.project_id)
    remaining = {name: calls for name, calls in result['stages'].items() if name not in completed}

    result.update({
        'simulation_id': simulation_id,
        'project_id': state.project_id,
        'graph_id': graph_id,
        'completed_stages': completed,
        'remaining_stages': remaining,
        'remaining_total': sum(remaining.values()),
        'entity_count_source': entity_source,
        'total_rounds_source': rounds_source,
        'platform': platform,
    })
    return result
