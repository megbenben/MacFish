"""
冥想盆服务

把 MacFish 已经产生的项目、知识图谱、模拟动作与报告收割成记忆，并负责：
- 银色丝线（血缘关系 + 关键词共振）
- 记忆检索与入盆回放
- 多条记忆的蒸馏（记忆瓶）
- 预言球等外部模块回流记忆

所有 LLM 调用都可降级：没有 API key、网络失败或返回结构不合法时，使用确定性
的本地摘要，接口仍然可用。
"""
import csv
import json
import os
import re
import sqlite3
import threading
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..config import Config
from ..utils.llm_client import LLMClient
from ..utils.locale import get_locale, t
from ..utils.logger import get_logger
from .local_graph_store import LocalGraphStore
from .pensieve_store import PensieveStore

logger = get_logger('macfish.pensieve_service')

# 自动收存：串行执行，避免报告完成与模拟完成同时触发时并发重建丝线。
_AUTO_HARVEST_LOCK = threading.Lock()
_AUTO_HARVEST_GUARD = threading.Lock()
_AUTO_HARVEST_INFLIGHT: set = set()

MAX_PROJECT_TEXT_CHUNKS = 12
MAX_GRAPH_ENTITIES = 40
MAX_AGENT_PROFILES = 40
MAX_EPISODES_PER_SIMULATION = 120
MAX_REPORT_SECTIONS = 12
MAX_CONTENT_CHARS = 5000
MAX_SUMMARY_CHARS = 240

_EN_RE = re.compile(r'[A-Za-z][A-Za-z0-9_\-]{2,}')
_CJK_RE = re.compile(r'[一-鿿]+')

_EN_STOP = {
    'the', 'and', 'for', 'with', 'from', 'that', 'this', 'these', 'those',
    'you', 'your', 'our', 'are', 'was', 'were', 'has', 'have', 'had', 'not',
    'but', 'can', 'could', 'will', 'would', 'should', 'about', 'into',
    'than', 'then', 'there', 'their', 'they', 'them', 'its', 'it', 'is',
    'of', 'to', 'in', 'on', 'at', 'by', 'as', 'or', 'an', 'a',
}

# 常见中文二元/三元片段，避免「我们 / 可以 / 因为」这类噪声淹没真正的主题词。
_CJK_STOP = {
    '我们', '他们', '你们', '它们', '自己', '大家', '一个', '一种', '一些',
    '这个', '那个', '这些', '那些', '这样', '那样', '这种', '那种', '其中',
    '以及', '因为', '所以', '但是', '而且', '并且', '或者', '如果', '虽然',
    '已经', '正在', '可以', '可能', '应该', '必须', '需要', '不会', '不能',
    '没有', '不是', '就是', '还是', '也是', '都是', '只是', '还有', '同时',
    '通过', '进行', '对于', '关于', '根据', '由于', '为了', '成为', '作为',
    '包括', '主要', '重要', '相关', '不同', '各种', '每个', '非常', '更加',
    '很多', '多少', '如何', '什么', '为什么', '怎么', '时候', '现在', '未来',
    '之间', '之后', '之前', '一直', '已经', '能够', '实现', '提供', '使用',
    '支持', '推动', '确保', '提升', '降低', '增加', '减少', '形成', '建立',
    '内容', '方式', '方法', '过程', '结果', '目标', '数据', '系统', '管理',
    '工作', '团队', '组织', '计划', '项目', '分析', '报告', '模拟', '实体',
    '记忆', '问题', '方面', '情况', '我们', '一个', '这个', '那个', '什么',
}


def _now() -> str:
    return datetime.now().isoformat()


def _read_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _truncate(text: str, limit: int) -> str:
    text = (text or '').strip()
    return text if len(text) <= limit else text[:limit].rstrip() + '…'


def _summary_of(text: str) -> str:
    text = re.sub(r'\s+', ' ', (text or '').strip())
    return _truncate(text, MAX_SUMMARY_CHARS)


def _dedupe(values: Iterable[Any]) -> List[str]:
    seen = set()
    result = []
    for value in values or []:
        text = str(value or '').strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _chunk_text(text: str, size: int = 1200) -> List[str]:
    text = (text or '').strip()
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text) and len(chunks) < MAX_PROJECT_TEXT_CHUNKS:
        end = min(len(text), start + size)
        # 尽量在换行处断开，读起来更像一页残页
        if end < len(text):
            newline = text.rfind('\n', start, end)
            if newline > start + size // 2:
                end = newline
        chunks.append(text[start:end].strip())
        start = end
    return [c for c in chunks if c]


def extract_keywords(text: str, entities: Optional[Iterable[str]] = None,
                     tags: Optional[Iterable[str]] = None, limit: int = 14) -> List[str]:
    """轻量关键词提取：实体/标签加权 + 英文词 + 中文 2/3-gram。"""
    scores: Counter = Counter()
    boosted = set()

    for value in list(entities or []) + list(tags or []):
        value = str(value or '').strip().lower()
        if len(value) >= 2 and value not in _EN_STOP:
            scores[value] += 6
            boosted.add(value)

    text = text or ''
    for word in _EN_RE.findall(text):
        word = word.lower()
        if word not in _EN_STOP:
            scores[word] += 1

    for run in _CJK_RE.findall(text):
        for n in (2, 3):
            for i in range(len(run) - n + 1):
                gram = run[i:i + n]
                if gram in _CJK_STOP or gram.isdigit():
                    continue
                scores[gram] += 1

    ranked = []
    for word, count in scores.most_common(limit * 4):
        # 长文本里重复出现的词优先；短问句/短摘要里，三字中文片段出现一次也可能
        # 是关键概念，允许入榜，否则问卜时几乎匹配不到任何分支。
        if count >= 2 or word in boosted or (len(word) >= 3 and _CJK_RE.fullmatch(word)):
            ranked.append(word)
        if len(ranked) >= limit:
            break
    return ranked


class PensieveService:
    """冥想盆的收割、检索、回放与蒸馏。"""

    # ------------------------------------------------------------ 基础工具
    @classmethod
    def store(cls) -> PensieveStore:
        return PensieveStore.get_instance()

    @staticmethod
    def _projects_dir() -> str:
        return os.path.join(Config.UPLOAD_FOLDER, 'projects')

    @staticmethod
    def _simulations_dir() -> str:
        return os.path.join(Config.UPLOAD_FOLDER, 'simulations')

    @staticmethod
    def _reports_dir() -> str:
        return os.path.join(Config.UPLOAD_FOLDER, 'reports')

    @classmethod
    def _simulation_project_map(cls) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        root = cls._simulations_dir()
        if not os.path.isdir(root):
            return mapping
        for name in os.listdir(root):
            state = _read_json(os.path.join(root, name, 'state.json'))
            if state and state.get('project_id'):
                mapping[state.get('simulation_id') or name] = state['project_id']
        return mapping

    # ------------------------------------------------------------ 收割入口
    @classmethod
    def harvest(cls, scope: str = 'all', target_id: str = '') -> Dict[str, Any]:
        scope = (scope or 'all').strip().lower()
        if scope not in ('all', 'project', 'simulation', 'report'):
            raise ValueError(t('api.pensieveInvalidScope'))

        store = cls.store()
        created = 0
        updated = 0
        errors: List[str] = []
        emitted: set = set()

        def emit(items: Iterable[Dict[str, Any]], label: str):
            nonlocal created, updated
            for item in items:
                key = item.get('external_key')
                if not key or key in emitted:
                    continue
                emitted.add(key)
                try:
                    _, is_new = store.upsert_memory(item)
                    if is_new:
                        created += 1
                    else:
                        updated += 1
                except Exception as exc:  # 单条记忆失败不能拖垮整次收割
                    logger.warning("冥想盆写入记忆失败: %s, error=%s", key, exc)
                    if len(errors) < 20:
                        errors.append(f"{label}: {key}: {exc}")

        if scope in ('all', 'project'):
            for project in cls._iter_projects(target_id if scope == 'project' else ''):
                try:
                    emit(cls._project_memories(project), 'project')
                except Exception as exc:
                    logger.warning("项目收割失败: %s", exc)
                    if len(errors) < 20:
                        errors.append(f"project {project.get('project_id', '?')}: {exc}")

        if scope in ('all', 'simulation'):
            sim_project_map = cls._simulation_project_map()
            for sim_id, sim_dir, state in cls._iter_simulations(
                    target_id if scope == 'simulation' else ''):
                try:
                    emit(cls._simulation_memories(sim_id, sim_dir, state,
                                                  sim_project_map.get(sim_id, '')),
                         'simulation')
                except Exception as exc:
                    logger.warning("模拟收割失败: %s", exc)
                    if len(errors) < 20:
                        errors.append(f"simulation {sim_id}: {exc}")

        if scope in ('all', 'report'):
            sim_project_map = cls._simulation_project_map()
            for report_id, meta in cls._iter_reports(
                    target_id if scope == 'report' else ''):
                try:
                    emit(cls._report_memories(report_id, meta, sim_project_map), 'report')
                except Exception as exc:
                    logger.warning("报告收割失败: %s", exc)
                    if len(errors) < 20:
                        errors.append(f"report {report_id}: {exc}")

        cls._rebuild_strands(store)
        stats = store.stats()
        return {
            "scope": scope,
            "target_id": target_id or "",
            "created": created,
            "updated": updated,
            "total": stats["memories"],
            "by_kind": stats["by_kind"],
            "errors": errors,
        }

    # ------------------------------------------------------------ 自动收存
    @classmethod
    def auto_harvest(cls, scope: str = 'all', target_id: str = '',
                     background: bool = True) -> Optional[Dict[str, Any]]:
        """在报告/模拟完成后自动收存。

        只收割指定 scope（而不是全量扫描），失败的收存取日志后放弃，
        绝不影响报告生成或模拟主流程。``background=True`` 时不阻塞调用方。
        """
        scope = (scope or 'all').strip().lower()
        target_id = (target_id or '').strip()
        if scope not in ('all', 'project', 'simulation', 'report'):
            logger.warning("忽略未知的自动收存范围: %s", scope)
            return None
        if scope != 'all' and not target_id:
            logger.warning("自动收存缺少 target_id: scope=%s", scope)
            return None

        key = f"{scope}:{target_id or '*'}"
        if background:
            with _AUTO_HARVEST_GUARD:
                if key in _AUTO_HARVEST_INFLIGHT:
                    return None
                _AUTO_HARVEST_INFLIGHT.add(key)
            threading.Thread(
                target=cls._run_auto_harvest,
                args=(key, scope, target_id),
                daemon=True,
                name=f"pensieve-harvest-{scope}",
            ).start()
            return None

        return cls._run_auto_harvest(key, scope, target_id, track=False)

    @classmethod
    def _run_auto_harvest(cls, key: str, scope: str, target_id: str,
                          track: bool = True) -> Optional[Dict[str, Any]]:
        try:
            # 串行化：报告完成和模拟完成可能几乎同时触发，丝线重建不宜并发。
            with _AUTO_HARVEST_LOCK:
                result = cls.harvest(scope, target_id)
            logger.info(
                "自动收存完成: scope=%s target=%s created=%s updated=%s total=%s",
                scope, target_id, result.get('created'), result.get('updated'),
                result.get('total'),
            )
            return result
        except Exception as exc:
            logger.warning("自动收存失败: scope=%s target=%s error=%s",
                           scope, target_id, exc)
            return None
        finally:
            if track:
                with _AUTO_HARVEST_GUARD:
                    _AUTO_HARVEST_INFLIGHT.discard(key)

    @classmethod
    def auto_harvest_simulation(cls, simulation_id: str,
                                background: bool = True) -> bool:
        """模拟完成后的收存入口。

        run-status 会被前端反复轮询，因此用模拟目录下的标记文件保证一次模拟
        只自动收存一次；收存失败时不写标记，下一次轮询会重试。
        """
        simulation_id = (simulation_id or '').strip()
        if not simulation_id:
            return False
        sim_dir = os.path.join(cls._simulations_dir(), simulation_id)
        if not os.path.isdir(sim_dir):
            return False

        # 只收存真正结束的模拟：报告可能在模拟还没跑完时就被强制重生成，
        # 此时不能把中途状态写进标记文件，否则完成后的收存会被永久跳过。
        run_state = _read_json(os.path.join(sim_dir, 'run_state.json')) or {}
        state = _read_json(os.path.join(sim_dir, 'state.json')) or {}
        runner_status = str(run_state.get('runner_status') or '')
        state_status = str(state.get('status') or '')
        finished = (runner_status in ('completed', 'stopped')
                    or (not runner_status and state_status in ('completed', 'stopped')))
        if not finished:
            return False

        marker = os.path.join(sim_dir, '.pensieve_harvested')
        if os.path.exists(marker):
            return False

        key = f"simulation:{simulation_id}"
        with _AUTO_HARVEST_GUARD:
            if key in _AUTO_HARVEST_INFLIGHT:
                return False
            _AUTO_HARVEST_INFLIGHT.add(key)

        def run():
            try:
                result = cls._run_auto_harvest(
                    key, 'simulation', simulation_id, track=False)
                if result is not None:
                    try:
                        with open(marker, 'w', encoding='utf-8') as f:
                            f.write(datetime.now().isoformat())
                    except OSError as exc:
                        logger.warning("写入自动收存标记失败: %s", exc)
            finally:
                with _AUTO_HARVEST_GUARD:
                    _AUTO_HARVEST_INFLIGHT.discard(key)

        if not background:
            run()
            return True
        threading.Thread(
            target=run, daemon=True,
            name=f"pensieve-harvest-{simulation_id}",
        ).start()
        return True

    # ------------------------------------------------------------ 项目
    @classmethod
    def _iter_projects(cls, target_id: str = ''):
        root = cls._projects_dir()
        if not os.path.isdir(root):
            return
        for name in sorted(os.listdir(root)):
            if target_id and name != target_id:
                continue
            project = _read_json(os.path.join(root, name, 'project.json'))
            if project:
                project.setdefault('project_id', name)
                yield project

    @classmethod
    def _project_memories(cls, project: Dict[str, Any]):
        project_id = project.get('project_id') or ''
        if not project_id:
            return
        name = project.get('name') or project_id
        graph_id = project.get('graph_id') or ''
        created_at = project.get('created_at') or ''
        requirement = project.get('simulation_requirement') or ''
        base = {
            'source_type': 'project',
            'source_id': project_id,
            'project_id': project_id,
            'graph_id': graph_id,
            'occurred_at': created_at,
        }

        if requirement:
            yield {
                **base,
                'external_key': f'project:{project_id}:requirement',
                'kind': 'seed',
                'title': f'现实种子 · {name}',
                'content': requirement,
                'summary': _summary_of(requirement),
                'tags': ['seed', 'requirement'],
                'entities': [name],
                'importance': 88,
            }

        analysis = project.get('analysis_summary') or ''
        if analysis:
            yield {
                **base,
                'external_key': f'project:{project_id}:analysis',
                'kind': 'seed',
                'title': f'分析摘要 · {name}',
                'content': analysis,
                'summary': _summary_of(analysis),
                'tags': ['seed', 'analysis'],
                'entities': [name],
                'importance': 82,
            }

        for index, file_info in enumerate(project.get('files') or []):
            if not isinstance(file_info, dict):
                continue
            filename = (file_info.get('original_filename') or file_info.get('filename')
                        or os.path.basename(file_info.get('path') or '') or f'file-{index}')
            saved = file_info.get('saved_filename') or filename
            size = file_info.get('size') or 0
            content = (f"档案：{filename}\n类型：{os.path.splitext(filename)[1] or '未知'}\n"
                       f"大小：{size} 字节\n保存名：{saved}")
            yield {
                **base,
                'external_key': f'project:{project_id}:file:{saved}',
                'kind': 'seed',
                'title': f'档案 · {filename}',
                'content': content,
                'summary': f"{filename}（{size} 字节）",
                'tags': ['seed', 'file', os.path.splitext(filename)[1].lstrip('.').lower()],
                'entities': [filename],
                'importance': 62,
                'metadata': {'filename': filename, 'size': size},
            }

        text_path = os.path.join(cls._projects_dir(), project_id, 'extracted_text.txt')
        if os.path.exists(text_path):
            try:
                with open(text_path, 'r', encoding='utf-8', errors='ignore') as f:
                    text = f.read()
            except OSError:
                text = ''
            for index, chunk in enumerate(_chunk_text(text)):
                yield {
                    **base,
                    'external_key': f'project:{project_id}:text:{index}',
                    'kind': 'seed',
                    'title': f'文本残页 {index + 1} · {name}',
                    'content': chunk,
                    'summary': _summary_of(chunk),
                    'tags': ['seed', 'document'],
                    'entities': [name],
                    'importance': 72,
                    'metadata': {'chunk_index': index},
                }

        ontology = project.get('ontology')
        if ontology:
            yield {
                **base,
                'external_key': f'project:{project_id}:ontology',
                'kind': 'seed',
                'title': f'世界本体 · {name}',
                'content': cls._format_ontology(ontology),
                'summary': f"{name} 的实体类型与关系定义",
                'tags': ['seed', 'ontology'],
                'entities': [name],
                'importance': 78,
                'metadata': {'ontology': ontology},
            }

        yield from cls._graph_entity_memories(project)

    @staticmethod
    def _format_ontology(ontology: Any) -> str:
        lines = []
        if isinstance(ontology, dict):
            for key in ('entity_types', 'entities', 'nodes'):
                for item in ontology.get(key) or []:
                    if isinstance(item, dict):
                        name = item.get('name') or item.get('type') or item.get('label')
                        desc = item.get('description') or item.get('summary') or ''
                        if name:
                            lines.append(f"实体类型 {name}：{desc}".strip('：'))
            for key in ('edge_types', 'relations', 'edges', 'relation_types'):
                for item in ontology.get(key) or []:
                    if isinstance(item, dict):
                        name = item.get('name') or item.get('type') or item.get('label')
                        desc = item.get('description') or item.get('summary') or ''
                        if name:
                            lines.append(f"关系类型 {name}：{desc}".strip('：'))
        if not lines:
            lines.append(_truncate(json.dumps(ontology, ensure_ascii=False), 3000))
        return '\n'.join(lines)

    @classmethod
    def _graph_entity_memories(cls, project: Dict[str, Any]):
        graph_id = project.get('graph_id')
        if not graph_id:
            return
        db_path = os.path.join(Config.UPLOAD_FOLDER, 'graphs.db')
        if not os.path.exists(db_path):
            return
        try:
            graph_store = LocalGraphStore.get_instance(db_path)
            nodes = graph_store.get_all_nodes(graph_id)
            edges = graph_store.get_all_edges(graph_id)
        except Exception as exc:
            logger.warning("读取图谱失败（%s）: %s", graph_id, exc)
            return

        degree: Counter = Counter()
        for edge in edges:
            degree[edge.source_node_uuid] += 1
            degree[edge.target_node_uuid] += 1
        nodes.sort(key=lambda n: (degree.get(n.uuid_, 0), len(n.summary or '')), reverse=True)

        for node in nodes[:MAX_GRAPH_ENTITIES]:
            content = node.summary or ''
            if node.attributes:
                try:
                    content += '\n' + json.dumps(node.attributes, ensure_ascii=False)
                except (TypeError, ValueError):
                    pass
            if not content.strip():
                content = node.name
            yield {
                'external_key': f'graph:{graph_id}:entity:{node.uuid_}',
                'kind': 'entity',
                'title': f'实体 · {node.name}',
                'content': _truncate(content, MAX_CONTENT_CHARS),
                'summary': _summary_of(node.summary or node.name),
                'source_type': 'graph',
                'source_id': graph_id,
                'project_id': project.get('project_id') or '',
                'graph_id': graph_id,
                'tags': _dedupe((node.labels or []) + ['entity'])[:8],
                'entities': [node.name],
                'importance': min(95, 55 + degree.get(node.uuid_, 0) // 2),
                'metadata': {'degree': degree.get(node.uuid_, 0), 'node_uuid': node.uuid_},
            }

    # ------------------------------------------------------------ 模拟
    @classmethod
    def _iter_simulations(cls, target_id: str = ''):
        root = cls._simulations_dir()
        if not os.path.isdir(root):
            return
        for name in sorted(os.listdir(root)):
            if target_id and name != target_id:
                continue
            sim_dir = os.path.join(root, name)
            if not os.path.isdir(sim_dir):
                continue
            state = _read_json(os.path.join(sim_dir, 'state.json'))
            if state:
                state.setdefault('simulation_id', name)
                yield name, sim_dir, state

    @classmethod
    def _simulation_memories(cls, sim_id: str, sim_dir: str,
                             state: Dict[str, Any], project_id: str):
        created_at = state.get('created_at') or ''
        requirement = state.get('simulation_requirement') or ''
        config = _read_json(os.path.join(sim_dir, 'simulation_config.json')) or {}
        requirement = requirement or config.get('simulation_requirement') or ''
        reasoning = state.get('config_reasoning') or config.get('generation_reasoning') or ''

        world_content = []
        if requirement:
            world_content.append(f"模拟需求：{requirement}")
        if reasoning:
            world_content.append(f"配置推理：{reasoning}")
        world_content.append(
            f"实体 {state.get('entities_count', 0)} 个，"
            f"人设 {state.get('profiles_count', 0)} 个，"
            f"类型：{', '.join(state.get('entity_types') or []) or '未知'}"
        )
        yield {
            'external_key': f'sim:{sim_id}:world',
            'kind': 'world',
            'title': f'模拟世界 · {sim_id}',
            'content': '\n\n'.join(world_content),
            'summary': _summary_of(requirement or reasoning or sim_id),
            'source_type': 'simulation',
            'source_id': sim_id,
            'project_id': project_id,
            'graph_id': state.get('graph_id') or '',
            'simulation_id': sim_id,
            'tags': _dedupe((state.get('entity_types') or []) + ['simulation'])[:10],
            'entities': _dedupe(state.get('entity_types') or [])[:10],
            'importance': 92,
            'occurred_at': created_at,
            'metadata': {
                'status': state.get('status'),
                'entities_count': state.get('entities_count'),
                'profiles_count': state.get('profiles_count'),
            },
        }

        yield from cls._agent_memories(sim_id, sim_dir, project_id, created_at)
        yield from cls._episode_memories(sim_id, sim_dir, project_id, created_at)
        yield from cls._voice_memories(sim_id, sim_dir, project_id)

    @staticmethod
    def _profile_rows(sim_dir: str) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        reddit_path = os.path.join(sim_dir, 'reddit_profiles.json')
        if os.path.exists(reddit_path):
            try:
                with open(reddit_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, list):
                    rows.extend(r for r in data if isinstance(r, dict))
            except (OSError, ValueError):
                pass
        if rows:
            return rows

        twitter_path = os.path.join(sim_dir, 'twitter_profiles.csv')
        if os.path.exists(twitter_path):
            try:
                with open(twitter_path, 'r', encoding='utf-8', newline='') as f:
                    for row in csv.DictReader(f):
                        rows.append({
                            'user_id': row.get('user_id'),
                            'name': row.get('name'),
                            'username': row.get('username'),
                            'bio': row.get('description') or '',
                            'persona': row.get('user_char') or '',
                        })
            except (OSError, ValueError):
                pass
        return rows

    @classmethod
    def _agent_memories(cls, sim_id: str, sim_dir: str, project_id: str,
                        created_at: str):
        for index, profile in enumerate(cls._profile_rows(sim_dir)[:MAX_AGENT_PROFILES]):
            name = (profile.get('name') or profile.get('username')
                    or f"agent-{profile.get('user_id', index)}")
            bio = profile.get('bio') or ''
            persona = profile.get('persona') or profile.get('user_char') or ''
            content = '\n\n'.join(part for part in (bio, persona) if part)
            if not content.strip():
                continue
            interests = profile.get('interested_topics') or []
            if isinstance(interests, str):
                interests = [interests]
            tags = _dedupe(
                [profile.get('profession'), profile.get('country')]
                + list(interests) + ['agent']
            )[:10]
            yield {
                'external_key': f"sim:{sim_id}:agent:{profile.get('user_id', index)}",
                'kind': 'agent',
                'title': f'群像 · {name}',
                'content': _truncate(content, MAX_CONTENT_CHARS),
                'summary': _summary_of(bio or persona or name),
                'source_type': 'simulation',
                'source_id': sim_id,
                'project_id': project_id,
                'simulation_id': sim_id,
                'tags': tags,
                'entities': [name],
                'importance': 70,
                'occurred_at': created_at,
                'metadata': {
                    'user_id': profile.get('user_id'),
                    'profession': profile.get('profession'),
                    'mbti': profile.get('mbti'),
                },
            }

    @staticmethod
    def _action_text(args: Any) -> str:
        if not isinstance(args, dict):
            return ''
        for key in ('content', 'post_content', 'comment_content', 'quote_content',
                    'text', 'description', 'message'):
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ''

    @classmethod
    def _episode_memories(cls, sim_id: str, sim_dir: str, project_id: str,
                          created_at: str):
        candidates: List[Dict[str, Any]] = []
        action_files = [
            ('twitter', os.path.join(sim_dir, 'twitter', 'actions.jsonl')),
            ('reddit', os.path.join(sim_dir, 'reddit', 'actions.jsonl')),
        ]
        found_actions_file = False
        for platform, path in action_files:
            if not os.path.exists(path):
                continue
            found_actions_file = True
            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    for line_index, line in enumerate(f):
                        line = line.strip()
                        if not line or len(candidates) >= MAX_EPISODES_PER_SIMULATION * 4:
                            continue
                        try:
                            item = json.loads(line)
                        except ValueError:
                            continue
                        if not isinstance(item, dict) or item.get('event_type'):
                            continue
                        action = str(item.get('action_type') or '').upper()
                        if not action:
                            continue
                        text = cls._action_text(item.get('action_args'))
                        if not text:
                            continue
                        candidates.append({
                            'platform': platform,
                            'line_index': line_index,
                            'action': action,
                            'agent_name': item.get('agent_name') or item.get('agent_id'),
                            'round': item.get('round'),
                            'timestamp': item.get('timestamp') or '',
                            'text': text,
                            'args': item.get('action_args') or {},
                        })
            except OSError:
                continue

        if not candidates or not found_actions_file:
            candidates.extend(cls._episodes_from_db(sim_id, sim_dir))

        def priority(item):
            action = item['action']
            base = 80 if action in ('CREATE_POST', 'QUOTE_POST') else 70
            if action in ('CREATE_COMMENT', 'COMMENT'):
                base = 66
            if action in ('LIKE_POST', 'LIKE_COMMENT', 'REPOST', 'FOLLOW'):
                base = 50
            return (-base, -len(item['text']), item.get('timestamp') or '')

        candidates.sort(key=priority)
        for item in candidates[:MAX_EPISODES_PER_SIMULATION]:
            agent = item.get('agent_name') or '未知 Agent'
            action = item['action']
            platform = item['platform']
            content = item['text']
            yield {
                'external_key': (f"sim:{sim_id}:{platform}:action:"
                                 f"{item.get('round')}:{item.get('line_index')}"),
                'kind': 'episode',
                'title': f'事件 · {agent} · {action}',
                'content': _truncate(content, MAX_CONTENT_CHARS),
                'summary': _summary_of(content),
                'source_type': 'simulation',
                'source_id': sim_id,
                'project_id': project_id,
                'simulation_id': sim_id,
                'tags': [platform, action.lower(), 'episode'],
                'entities': [str(agent)],
                'importance': 60 if action.startswith('LIKE') else 76,
                'occurred_at': item.get('timestamp') or created_at,
                'metadata': {
                    'platform': platform,
                    'action_type': action,
                    'round': item.get('round'),
                },
            }

    @classmethod
    def _episodes_from_db(cls, sim_id: str, sim_dir: str) -> List[Dict[str, Any]]:
        """actions.jsonl 缺失时的兜底：从 OASIS 社交库的 post/comment/trace 抽样。"""
        results: List[Dict[str, Any]] = []
        for platform in ('twitter', 'reddit'):
            db_path = os.path.join(sim_dir, f'{platform}_simulation.db')
            if not os.path.exists(db_path):
                continue
            try:
                conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
                conn.row_factory = sqlite3.Row
                users = {
                    row['user_id']: row['name']
                    for row in conn.execute("SELECT user_id, name FROM user").fetchall()
                }
                rows = conn.execute(
                    """SELECT post_id, user_id, content, created_at,
                              num_likes, num_shares
                       FROM post WHERE content IS NOT NULL AND content<>''
                       ORDER BY (num_likes + num_shares * 2) DESC LIMIT 60"""
                ).fetchall()
                for row in rows:
                    results.append({
                        'platform': platform,
                        'line_index': f"post-{row['post_id']}",
                        'action': 'CREATE_POST',
                        'agent_name': users.get(row['user_id'], f"user-{row['user_id']}"),
                        'round': None,
                        'timestamp': str(row['created_at'] or ''),
                        'text': row['content'],
                        'args': {},
                    })
                conn.close()
            except sqlite3.Error as exc:
                logger.warning("读取模拟库失败（%s）: %s", db_path, exc)
        return results

    @classmethod
    def _voice_memories(cls, sim_id: str, sim_dir: str, project_id: str):
        for platform in ('twitter', 'reddit'):
            db_path = os.path.join(sim_dir, f'{platform}_simulation.db')
            if not os.path.exists(db_path):
                continue
            try:
                conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
                conn.row_factory = sqlite3.Row
                users = {
                    row['user_id']: row['name']
                    for row in conn.execute("SELECT user_id, name FROM user").fetchall()
                }
                rows = conn.execute(
                    "SELECT user_id, created_at, info FROM trace "
                    "WHERE action='interview' ORDER BY created_at DESC LIMIT 50"
                ).fetchall()
                conn.close()
            except sqlite3.Error as exc:
                logger.warning("读取访谈记录失败（%s）: %s", db_path, exc)
                continue

            for index, row in enumerate(rows):
                try:
                    info = json.loads(row['info'] or '{}')
                except (TypeError, ValueError):
                    info = {}
                prompt = info.get('prompt') or ''
                response = info.get('response') or ''
                if not prompt and not response:
                    continue
                agent = users.get(row['user_id'], f"user-{row['user_id']}")
                interview_id = info.get('interview_id') or f"{row['created_at']}-{index}"
                content = f"问：{prompt}\n\n答：{response}"
                yield {
                    'external_key': f'sim:{sim_id}:{platform}:interview:{interview_id}',
                    'kind': 'voice',
                    'title': f'访谈回声 · {agent}',
                    'content': _truncate(content, MAX_CONTENT_CHARS),
                    'summary': _summary_of(response or prompt),
                    'source_type': 'simulation',
                    'source_id': sim_id,
                    'project_id': project_id,
                    'simulation_id': sim_id,
                    'tags': [platform, 'interview', 'voice'],
                    'entities': [str(agent)],
                    'importance': 82,
                    'occurred_at': str(row['created_at'] or ''),
                    'metadata': {'platform': platform, 'interview_id': interview_id},
                }

    # ------------------------------------------------------------ 报告
    @classmethod
    def _iter_reports(cls, target_id: str = ''):
        root = cls._reports_dir()
        if not os.path.isdir(root):
            return
        for name in sorted(os.listdir(root)):
            if target_id and name != target_id:
                continue
            meta = _read_json(os.path.join(root, name, 'meta.json'))
            if meta:
                meta.setdefault('report_id', name)
                yield name, meta

    @classmethod
    def _report_memories(cls, report_id: str, meta: Dict[str, Any],
                         sim_project_map: Dict[str, str]):
        from .report_agent import ReportOutline  # 延迟导入，避免与报告模块循环

        outline = None
        raw_outline = meta.get('outline')
        if raw_outline:
            try:
                outline = ReportOutline.from_dict(raw_outline)
            except Exception:
                outline = None

        title = (outline.title if outline else '') or meta.get('report_id') or report_id
        summary = (outline.summary if outline else '') or meta.get('simulation_requirement') or ''
        simulation_id = meta.get('simulation_id') or ''
        project_id = sim_project_map.get(simulation_id, '')
        created_at = meta.get('completed_at') or meta.get('created_at') or ''
        base = {
            'source_type': 'report',
            'source_id': report_id,
            'project_id': project_id,
            'graph_id': meta.get('graph_id') or '',
            'simulation_id': simulation_id,
            'report_id': report_id,
            'occurred_at': created_at,
        }

        yield {
            **base,
            'external_key': f'report:{report_id}:summary',
            'kind': 'prophecy',
            'title': f'预言总纲 · {title}',
            'content': '\n\n'.join(part for part in (
                summary, meta.get('simulation_requirement') or '') if part),
            'summary': _summary_of(summary or title),
            'tags': ['report', 'summary', 'prophecy'],
            'entities': [],
            'importance': 96,
            'metadata': {'status': meta.get('status'), 'report_title': title},
        }

        sections = list(outline.sections) if outline else []
        if not sections:
            sections = cls._sections_from_markdown(report_id)
        for index, section in enumerate(sections[:MAX_REPORT_SECTIONS]):
            content = getattr(section, 'content', '') if not isinstance(section, dict) else section.get('content', '')
            section_title = getattr(section, 'title', '') if not isinstance(section, dict) else section.get('title', '')
            if not content and not section_title:
                continue
            yield {
                **base,
                'external_key': f'report:{report_id}:section:{index}',
                'kind': 'prophecy',
                'title': f'预言残页 · {section_title or f"第 {index + 1} 章"}',
                'content': _truncate(content, MAX_CONTENT_CHARS),
                'summary': _summary_of(content or section_title),
                'tags': ['report', 'section', 'prophecy'],
                'entities': [],
                'importance': 90,
                'metadata': {'section_index': index, 'section_title': section_title,
                             'report_title': title},
            }

        scenarios = list(outline.scenarios) if outline and outline.scenarios else []
        for index, scenario in enumerate(scenarios):
            if isinstance(scenario, dict):
                name = scenario.get('name') or ''
                description = scenario.get('description') or ''
                triggers = scenario.get('trigger_conditions') or []
                likelihood = scenario.get('relative_likelihood') or ''
                actors = scenario.get('key_actors') or []
            else:
                name = scenario.name
                description = scenario.description
                triggers = scenario.trigger_conditions
                likelihood = scenario.relative_likelihood
                actors = scenario.key_actors
            content = '\n'.join(filter(None, [
                description,
                ('触发条件：' + '；'.join(triggers)) if triggers else '',
                ('关键角色：' + '、'.join(actors)) if actors else '',
                ('相对可能性：' + likelihood) if likelihood else '',
            ]))
            yield {
                **base,
                'external_key': f'report:{report_id}:scenario:{index}',
                'kind': 'prophecy',
                'title': f'情景分支 · {name or f"分支 {index + 1}"}',
                'content': _truncate(content, MAX_CONTENT_CHARS),
                'summary': _summary_of(description or name),
                'tags': _dedupe(['report', 'scenario', 'prophecy', likelihood])[:8],
                'entities': _dedupe(actors)[:12],
                'importance': 86,
                'metadata': {
                    'scenario_name': name,
                    'report_title': title,
                    'likelihood': likelihood,
                    'triggers': triggers,
                    'actors': actors,
                },
            }

    @classmethod
    def _sections_from_markdown(cls, report_id: str) -> List[Dict[str, str]]:
        path = os.path.join(cls._reports_dir(), report_id, 'full_report.md')
        if not os.path.exists(path):
            return []
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                text = f.read()
        except OSError:
            return []
        parts = re.split(r'(?m)^##\s+', text)
        sections = []
        for part in parts[1:MAX_REPORT_SECTIONS + 1]:
            lines = part.split('\n', 1)
            sections.append({
                'title': lines[0].strip(),
                'content': lines[1].strip() if len(lines) > 1 else '',
            })
        return sections

    # ------------------------------------------------------------ 丝线
    @classmethod
    def _rebuild_strands(cls, store: PensieveStore) -> None:
        memories = store.all_memories(limit=5000)
        if not memories:
            store.clear_strands()
            return

        store.clear_strands()
        by_external = {m['external_key']: m for m in memories}
        by_sim_world: Dict[str, Dict[str, Any]] = {}
        by_report_summary: Dict[str, Dict[str, Any]] = {}
        by_project_seed: Dict[str, Dict[str, Any]] = {}

        for memory in memories:
            key = memory['external_key']
            if key.endswith(':world') and memory['simulation_id']:
                by_sim_world[memory['simulation_id']] = memory
            elif key.endswith(':summary') and memory['report_id']:
                by_report_summary[memory['report_id']] = memory
            elif key.endswith(':requirement') and memory['project_id']:
                by_project_seed[memory['project_id']] = memory

        for memory in memories:
            key = memory['external_key']
            sim_id = memory['simulation_id']
            report_id = memory['report_id']
            project_id = memory['project_id']

            if report_id and (':section:' in key or ':scenario:' in key):
                summary_memory = by_report_summary.get(report_id)
                if summary_memory:
                    store.add_strand(summary_memory['memory_id'], memory['memory_id'],
                                     'chapter_of', '同一份预言的章节', 0.9)

            if report_id and key.endswith(':summary'):
                world = by_sim_world.get(sim_id)
                if world:
                    store.add_strand(memory['memory_id'], world['memory_id'],
                                     'derived_from', '预言源自模拟', 0.85)

            if memory['kind'] in ('episode', 'voice') and sim_id:
                world = by_sim_world.get(sim_id)
                if world:
                    store.add_strand(world['memory_id'], memory['memory_id'],
                                     'contains', '模拟世界中的事件', 0.72)

            if memory['kind'] == 'agent' and sim_id:
                world = by_sim_world.get(sim_id)
                if world:
                    store.add_strand(world['memory_id'], memory['memory_id'],
                                     'cast', '参与推演的群像', 0.65)

            if memory['kind'] == 'entity' and project_id:
                seed = by_project_seed.get(project_id)
                if seed:
                    store.add_strand(seed['memory_id'], memory['memory_id'],
                                     'mentions', '现实种子中的实体', 0.7)

            if memory['kind'] == 'insight':
                for source_id in (memory.get('metadata') or {}).get('source_memory_ids') or []:
                    if source_id in {m['memory_id'] for m in memories}:
                        store.add_strand(memory['memory_id'], source_id,
                                         'derived_from', '蒸馏自这段记忆', 0.8)

        cls._add_resonance_strands(store, memories)

    @classmethod
    def _add_resonance_strands(cls, store: PensieveStore,
                               memories: List[Dict[str, Any]]) -> None:
        keyword_sets: Dict[str, set] = {}
        inverted: Dict[str, List[str]] = defaultdict(list)
        for memory in memories:
            keywords = set(extract_keywords(
                f"{memory['title']}\n{memory['summary']}\n{memory['content']}",
                memory.get('entities'), memory.get('tags'), limit=12))
            if not keywords:
                continue
            keyword_sets[memory['memory_id']] = keywords
            for keyword in keywords:
                inverted[keyword].append(memory['memory_id'])

        # 太普遍的词（出现在 40% 以上记忆里）没有区分度，直接跳过
        total = max(1, len(keyword_sets))
        max_df = max(20, int(total * 0.4))
        pair_shared: Counter = Counter()
        for keyword, ids in inverted.items():
            if len(ids) < 2 or len(ids) > max_df:
                continue
            for i in range(len(ids)):
                a = ids[i]
                for b in ids[i + 1:]:
                    if a == b:
                        continue
                    pair_shared[(a, b) if a < b else (b, a)] += 1

        candidates = []
        for (a, b), shared in pair_shared.items():
            if shared < 2:
                continue
            union = len(keyword_sets[a] | keyword_sets[b]) or 1
            weight = round(shared / union, 3)
            if weight < 0.12:
                continue
            candidates.append((weight, a, b))
        candidates.sort(reverse=True)

        per_memory: Counter = Counter()
        for weight, a, b in candidates:
            if per_memory[a] >= 4 or per_memory[b] >= 4:
                continue
            store.add_strand(a, b, 'resonates', '关键词共振', weight)
            per_memory[a] += 1
            per_memory[b] += 1

    # ------------------------------------------------------------ 查询 / 回放
    @classmethod
    def overview(cls) -> Dict[str, Any]:
        store = cls.store()
        stats = store.stats()

        projects = []
        for project in cls._iter_projects():
            project_id = project.get('project_id') or ''
            projects.append({
                'project_id': project_id,
                'name': project.get('name') or project_id,
                'status': project.get('status') or '',
                'created_at': project.get('created_at') or '',
                'memory_count': store.count_memories(project_id=project_id),
            })

        simulations = []
        sim_project_map = cls._simulation_project_map()
        for sim_id, sim_dir, state in cls._iter_simulations():
            simulations.append({
                'simulation_id': sim_id,
                'project_id': state.get('project_id') or sim_project_map.get(sim_id, ''),
                'status': state.get('status') or '',
                'created_at': state.get('created_at') or '',
                'memory_count': store.count_memories(simulation_id=sim_id),
            })

        reports = []
        for report_id, meta in cls._iter_reports():
            outline = meta.get('outline') or {}
            reports.append({
                'report_id': report_id,
                'title': outline.get('title') or report_id,
                'simulation_id': meta.get('simulation_id') or '',
                'status': meta.get('status') or '',
                'created_at': meta.get('created_at') or '',
                'memory_count': store.count_memories(report_id=report_id),
            })

        for items in (projects, simulations, reports):
            items.sort(key=lambda item: item.get('created_at') or '', reverse=True)

        return {
            'stats': stats,
            'projects': projects[:50],
            'simulations': simulations[:50],
            'reports': reports[:50],
        }

    @classmethod
    def get_memory_detail(cls, memory_id: str) -> Optional[Dict[str, Any]]:
        store = cls.store()
        memory = store.get_memory(memory_id)
        if not memory:
            return None
        strands = store.list_strands_for_memory(memory_id)
        related = []
        seen = set()
        for strand in strands:
            other = strand['memory']
            if other['memory_id'] not in seen:
                seen.add(other['memory_id'])
                related.append({**other, 'relation': strand['relation'],
                                'weight': strand['weight']})
        return {'memory': memory, 'strands': strands, 'related': related}

    @staticmethod
    def _citation_of(memory: Dict[str, Any]) -> Dict[str, Any]:
        """把一条记忆还原成可点击的来源引用（报告章节 / 模拟回合 / 项目）。

        回放里的每一段都应该能回答「这是从哪来的」，而不是一段无出处的旁白。
        """
        metadata = memory.get('metadata') or {}
        report_id = memory.get('report_id') or ''
        simulation_id = memory.get('simulation_id') or ''
        project_id = memory.get('project_id') or ''
        memory_id = memory.get('memory_id') or ''
        english = get_locale() == 'en'
        label = ''
        detail = ''
        route = ''
        report_title = ''

        # 预言球/问卜/应验回流出来的记忆，优先指回预言厅
        orb_id = metadata.get('orb_id') or ''
        if orb_id:
            route = f"/prophecy?orb={orb_id}"
            label = (f"Prophecy orb · {memory.get('title') or orb_id}" if english
                     else f"预言球 · {memory.get('title') or orb_id}")
        elif report_id:
            route = f"/report/{report_id}"
            report_title = metadata.get('report_title') or ''
            external_key = memory.get('external_key') or ''
            if not report_title:
                # 章节/分支的标题后缀是章节名或分支名，不能当成报告标题
                if ':section:' in external_key or ':scenario:' in external_key:
                    report_title = report_id
                else:
                    raw_title = memory.get('title') or ''
                    report_title = (raw_title.split(' · ', 1)[1]
                                    if ' · ' in raw_title else report_id)
            section_index = metadata.get('section_index')
            section_title = metadata.get('section_title') or ''
            if section_index is None and ':section:' in (memory.get('external_key') or ''):
                # 旧记忆没有 section_index，从「预言残页 · 第 N 章」标题兜底
                raw_title = memory.get('title') or ''
                section_title = raw_title.split(' · ', 1)[1] if ' · ' in raw_title else ''
            if section_index is not None:
                chapter = int(section_index) + 1
                if english:
                    label = f"Report · Chapter {chapter}"
                    if section_title:
                        label += f" · {section_title}"
                else:
                    label = f"报告 · 第 {chapter} 章"
                    if section_title:
                        label += f" · {section_title}"
            else:
                scenario_name = metadata.get('scenario_name') or ''
                if not scenario_name and ':scenario:' in (memory.get('external_key') or ''):
                    raw_title = memory.get('title') or ''
                    scenario_name = (raw_title.split(' · ', 1)[1]
                                     if ' · ' in raw_title else '')
                if scenario_name:
                    label = (f"Report · Branch {scenario_name}"
                             if english else f"报告 · 分支 {scenario_name}")
                elif ':summary' in (memory.get('external_key') or ''):
                    label = (f"Report · Overview"
                             if english else f"报告 · 总纲")
                else:
                    label = "Report" if english else "报告"
            # 报告全名放在 tooltip 里，避免每条引用都重复一长串标题
            detail = f"Report {report_title}" if english else f"报告《{report_title}》"
        elif simulation_id:
            route = f"/simulation/{simulation_id}"
            bits = [f"Simulation {simulation_id}" if english else f"模拟 {simulation_id}"]
            platform = metadata.get('platform') or ''
            if platform:
                bits.append(str(platform))
            round_num = metadata.get('round')
            if round_num is not None:
                bits.append(f"Round {round_num}" if english else f"第 {round_num} 轮")
            action = metadata.get('action_type') or ''
            if action:
                bits.append(str(action))
            kind = memory.get('kind') or ''
            if kind == 'voice':
                bits.append('Interview' if english else '访谈回声')
            elif kind == 'agent':
                bits.append('Persona' if english else '群像')
            elif kind == 'world':
                bits.append('World' if english else '世界配置')
            label = ' · '.join(bits)
        elif project_id:
            route = f"/process/{project_id}"
            label = (f"Project {project_id}" if english else f"项目 {project_id}")
        else:
            label = (f"Memory {memory_id}" if english else f"记忆 {memory_id}")

        if not detail and memory.get('summary'):
            detail = _truncate(memory['summary'], 90)
        if detail and memory.get('summary') and report_id and memory.get('summary') != detail:
            detail = f"{detail} · {_truncate(memory['summary'], 70)}"

        return {
            'memory_id': memory_id,
            'source_type': memory.get('source_type') or '',
            'source_id': memory.get('source_id') or '',
            'report_id': report_id,
            'report_title': report_title,
            'simulation_id': simulation_id,
            'project_id': project_id,
            'route': route,
            'label': label,
            'detail': detail,
        }

    @classmethod
    def replay(cls, memory_id: str) -> Optional[Dict[str, Any]]:
        store = cls.store()
        memory = store.get_memory(memory_id)
        if not memory:
            return None

        strands = store.list_strands_for_memory(memory_id, limit=16)
        related = []
        for strand in strands:
            other = store.get_memory(strand['memory']['memory_id'])
            if other:
                related.append(other)

        # 回放要像一段连续的记忆，而不是把所有「关键词撞上」的跨项目片段
        # 一起倒进盆里：优先保留同报告/同模拟/同项目的丝线。
        def same_scope(other: Dict[str, Any]) -> bool:
            return any(
                memory.get(key) and memory.get(key) == other.get(key)
                for key in ('report_id', 'simulation_id', 'project_id')
            )

        scoped = [item for item in related if same_scope(item)]
        if scoped:
            related = scoped

        timeline_pool = [memory] + [
            item for item in related
            if item['kind'] in ('episode', 'voice', 'prophecy', 'world')
        ]
        seen = set()
        timeline = []
        for item in timeline_pool:
            if item['memory_id'] in seen:
                continue
            seen.add(item['memory_id'])
            timeline.append({
                'memory_id': item['memory_id'],
                'kind': item['kind'],
                'title': item['title'],
                'content': _truncate(item['content'], 220),
                'occurred_at': item.get('occurred_at') or item.get('created_at') or '',
                'citation': cls._citation_of(item),
            })
        timeline.sort(key=lambda item: item.get('occurred_at') or '')

        cast = _dedupe(
            list(memory.get('entities') or [])
            + [entity for item in related for entity in (item.get('entities') or [])]
        )[:12]

        setting = cls._setting_label(memory)
        narration = cls._narrate(setting, cast, timeline, memory)

        citations = []
        seen_citations = set()
        for item in timeline:
            citation = item.get('citation') or {}
            # 同一份报告的多个情景分支可能译出相同的展示文案；
            # 引用条按「文案 + 跳转地址」去重，避免回放顶部堆一排重复来源。
            key = f"{citation.get('label') or ''}|{citation.get('route') or ''}"
            if citation.get('memory_id') and key not in seen_citations:
                seen_citations.add(key)
                citations.append(citation)

        return {
            'memory': memory,
            'setting': setting,
            'cast': cast,
            'timeline': timeline,
            'narration': narration,
            'citations': citations,
            'citation_count': len(citations),
            'related': [{
                'memory_id': item['memory_id'],
                'kind': item['kind'],
                'title': item['title'],
                'summary': item['summary'],
                'citation': cls._citation_of(item),
            } for item in related],
        }

    @staticmethod
    def _setting_label(memory: Dict[str, Any]) -> str:
        if memory.get('report_id'):
            return f"报告 {memory['report_id']}"
        if memory.get('simulation_id'):
            return f"模拟 {memory['simulation_id']}"
        if memory.get('project_id'):
            return f"项目 {memory['project_id']}"
        return memory.get('title') or '一段无名记忆'

    @staticmethod
    def _narrate(setting: str, cast: List[str], timeline: List[Dict[str, Any]],
                 memory: Dict[str, Any]) -> str:
        english = get_locale() == 'en'
        if english:
            lines = [f"You lower yourself into the silver memory. {setting} unfolds at the bottom of the basin."]
            if cast:
                lines.append(f"Figures drift out of the mist: {', '.join(cast[:6])}.")
            if timeline:
                for item in timeline[:6]:
                    lines.append(f"— {item['title']}: {item['content']}")
            else:
                lines.append(f"— {memory['title']}: {_truncate(memory['content'], 240)}")
            return '\n\n'.join(lines)

        lines = [f"你俯身没入银色的记忆。{setting}在盆底缓缓展开，像一层被搅动的月光。"]
        if cast:
            lines.append(f"雾中浮出一些身影：{'、'.join(cast[:6])}。")
        if timeline:
            for item in timeline[:6]:
                lines.append(f"—— {item['title']}：{item['content']}")
        else:
            lines.append(f"—— {memory['title']}：{_truncate(memory['content'], 240)}")
        return '\n\n'.join(lines)

    # ------------------------------------------------------------ 蒸馏
    @classmethod
    def distill(cls, memory_ids: List[str], question: str = '') -> Dict[str, Any]:
        ids = [str(i) for i in (memory_ids or []) if str(i).strip()]
        ids = list(dict.fromkeys(ids))[:12]
        if not ids:
            raise ValueError(t('api.pensieveSelectMemory'))

        store = cls.store()
        memories = [store.get_memory(memory_id) for memory_id in ids]
        memories = [m for m in memories if m]
        if not memories:
            raise ValueError(t('api.pensieveNoUsableMemory'))

        question = (question or '').strip()
        used_llm = False
        title = question or f"记忆蒸馏 · {memories[0]['title']}"
        content = ''

        try:
            client = LLMClient()
            payload = '\n\n'.join(
                f"【{m['kind']}】{m['title']}\n{m['content'][:1800]}"
                for m in memories
            )
            prompt = (
                "你是冥想盆里的记忆蒸馏器。请把下面几段记忆融合成一条新的洞察。\n"
                "要求：1) 指出它们共同指向的主题与矛盾；2) 给出可执行的结论/提醒；"
                "3) 不要逐条复述原文；4) 用与用户问题相同的语言回答。\n\n"
                f"用户问题：{question or '这些记忆共同说明了什么？'}\n\n"
                f"记忆：\n{payload}\n\n"
                "只返回 JSON：{\"title\": \"简短标题\", \"content\": \"洞察正文\"}"
            )
            data = client.chat_json([
                {"role": "system", "content": "你只输出有效 JSON。"},
                {"role": "user", "content": prompt},
            ], temperature=0.5, max_tokens=2000)
            if isinstance(data, dict) and str(data.get('content') or '').strip():
                title = str(data.get('title') or title).strip() or title
                content = str(data.get('content')).strip()
                used_llm = True
        except Exception as exc:
            logger.warning("记忆蒸馏的 LLM 调用失败，使用本地降级: %s", exc)

        if not used_llm:
            content = cls._fallback_distill(memories, question)

        vial = store.add_vial(
            title=title,
            content=content,
            question=question,
            memory_ids=[m['memory_id'] for m in memories],
            used_llm=used_llm,
        )

        # 蒸馏结果本身也成为一段洞察记忆，回到盆里继续参与共振
        insight, _ = store.upsert_memory({
            'external_key': f"vial:{vial['vial_id']}",
            'kind': 'insight',
            'title': f"洞察 · {title}",
            'content': content,
            'summary': _summary_of(content),
            'source_type': 'vial',
            'source_id': vial['vial_id'],
            'project_id': memories[0].get('project_id') or '',
            'simulation_id': memories[0].get('simulation_id') or '',
            'report_id': memories[0].get('report_id') or '',
            'tags': ['insight', 'distilled'],
            'entities': _dedupe(
                entity for m in memories for entity in (m.get('entities') or [])
            )[:12],
            'importance': 88,
            'metadata': {'source_memory_ids': [m['memory_id'] for m in memories]},
        })
        for source in memories:
            store.add_strand(insight['memory_id'], source['memory_id'],
                             'derived_from', '蒸馏自这段记忆', 0.8)

        return {**vial, 'used_llm': used_llm, 'memory_id': insight['memory_id']}

    @classmethod
    def _fallback_distill(cls, memories: List[Dict[str, Any]], question: str) -> str:
        keyword_counter: Counter = Counter()
        for memory in memories:
            for keyword in extract_keywords(
                    f"{memory['title']}\n{memory['summary']}\n{memory['content']}",
                    memory.get('entities'), memory.get('tags'), limit=8):
                keyword_counter[keyword] += 1

        shared = [word for word, count in keyword_counter.most_common(8) if count >= 2]
        english = get_locale() == 'en'
        if english:
            heading = "**Shared threads:** " + (', '.join(shared) if shared else 'the same source world')
            lines = [heading]
            if question:
                lines.append(f"**Question:** {question}")
            for memory in memories:
                lines.append(f"- *{memory['title']}* — {memory['summary'] or _truncate(memory['content'], 180)}")
            lines.append("**Distilled insight:** these memories converge on the same situation from "
                         "different angles; compare the actors and triggers across them before acting.")
            return '\n\n'.join(lines)

        heading = "**共同丝线**：" + ('、'.join(shared) if shared else '同一段模拟世界')
        lines = [heading]
        if question:
            lines.append(f"**问题**：{question}")
        for memory in memories:
            lines.append(f"- 《{memory['title']}》——{memory['summary'] or _truncate(memory['content'], 180)}")
        lines.append("**蒸馏结论**：这些记忆从不同角度指向同一处局势。先对照它们中的关键角色与触发条件，"
                     "再决定下一步动作；被多段记忆同时提到的词，就是最值得优先验证的信号。")
        return '\n\n'.join(lines)

    # ------------------------------------------------------------ 外部回流
    @classmethod
    def record_external_memory(cls, **kwargs) -> Optional[Dict[str, Any]]:
        """供预言球等模块写入记忆。失败只告警，不影响调用方主流程。"""
        try:
            memory, _ = cls.store().upsert_memory(kwargs)
            return memory
        except Exception as exc:
            logger.warning("外部记忆回流失败: %s", exc)
            return None
