"""
模拟分支与 A/B 对比
===================

P1-3 的第二部分：从一个已有模拟克隆出**同源分支**，在指定轮次注入一个事件，
再和原分支做确定性对比。

诚实说明
--------
OASIS 没有可序列化的中途检查点，所以这里的分支是「同一初始条件 + 同一套
人设/配置，从第 0 轮重跑，并在第 N 轮注入差异事件」，不是「从第 N 轮的状态
继续」。两者都能回答"如果当时发生 X 会怎样"，但前者更可复现，也不需要改动
OASIS 内部状态。

产物：
- 新模拟目录（复制 config + profiles，状态为 READY）
- `fork.json`：父模拟、事件、注入轮次、注入状态
- `GET /api/simulation/<id>/fork` 读元数据
- `GET /api/simulation/<id>/compare?other_id=...` 做 A/B 对比
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..config import Config
from ..utils.logger import get_logger
from .market_simulator import MarketSimulator
from .simulation_config_generator import resolve_poster_agent_id
from .simulation_manager import SimulationManager, SimulationStatus
from .simulation_runner import SimulationRunner, RunnerStatus

logger = get_logger('mirofish.fork')

_FORK_FILE = 'fork.json'
_COPY_FILES = ('simulation_config.json', 'reddit_profiles.json', 'twitter_profiles.csv')


def _read_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: str, data: Dict[str, Any]) -> None:
    tmp = f'{path}.tmp'
    with open(tmp, 'w', encoding='utf-8') as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


class SimulationForkService:
    """创建分支、定时注入事件、对比两个模拟。"""

    @classmethod
    def _sim_dir(cls, simulation_id: str) -> str:
        return os.path.join(Config.OASIS_SIMULATION_DATA_DIR, simulation_id)

    @classmethod
    def get_fork(cls, simulation_id: str) -> Optional[Dict[str, Any]]:
        return _read_json(os.path.join(cls._sim_dir(simulation_id), _FORK_FILE))

    @classmethod
    def list_forks(cls, parent_id: str) -> List[Dict[str, Any]]:
        root = Config.OASIS_SIMULATION_DATA_DIR
        results = []
        if not os.path.isdir(root):
            return results
        for name in sorted(os.listdir(root)):
            meta = _read_json(os.path.join(root, name, _FORK_FILE))
            if meta and meta.get('parent_simulation_id') == parent_id:
                meta['simulation_id'] = name
                results.append(meta)
        return results

    @classmethod
    def fork(
        cls,
        source_id: str,
        *,
        label: str = '',
        event: str = '',
        event_round: int = 0,
        auto_start: bool = False,
        platform: str = '',
        max_rounds: Optional[int] = None,
    ) -> Dict[str, Any]:
        manager = SimulationManager()
        source = manager.get_simulation(source_id)
        if not source:
            raise LookupError(f'模拟不存在: {source_id}')
        source_dir = cls._sim_dir(source_id)
        config_path = os.path.join(source_dir, 'simulation_config.json')
        if not os.path.isfile(config_path):
            raise ValueError('源模拟尚未完成 prepare（缺少 simulation_config.json），无法分支')
        if not (source.enable_twitter or source.enable_reddit):
            raise ValueError('源模拟没有启用任何平台，无法分支')

        new_state = manager.create_simulation(
            project_id=source.project_id,
            graph_id=source.graph_id,
            enable_twitter=source.enable_twitter,
            enable_reddit=source.enable_reddit,
            enable_market=source.enable_market,
        )
        new_id = new_state.simulation_id
        new_dir = cls._sim_dir(new_id)
        os.makedirs(new_dir, exist_ok=True)

        copied = []
        for filename in _COPY_FILES:
            src = os.path.join(source_dir, filename)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(new_dir, filename))
                copied.append(filename)
        if 'simulation_config.json' not in copied:
            raise ValueError('复制 simulation_config.json 失败')
        if not any(name in copied for name in ('reddit_profiles.json', 'twitter_profiles.csv')):
            raise ValueError('源模拟缺少人设文件（reddit_profiles.json / twitter_profiles.csv）')

        # 配置里带着源模拟的 simulation_id；不改的话新进程日志/元数据会指向父模拟
        config_path_new = os.path.join(new_dir, 'simulation_config.json')
        config_data = _read_json(config_path_new) or {}
        config_data['simulation_id'] = new_id
        _write_json(config_path_new, config_data)

        new_state.entities_count = source.entities_count
        new_state.profiles_count = source.profiles_count
        new_state.entity_types = list(source.entity_types)
        new_state.entities_truncated = source.entities_truncated
        new_state.config_generated = True
        new_state.config_reasoning = source.config_reasoning
        new_state.status = SimulationStatus.READY
        manager._save_simulation_state(new_state)

        if not platform:
            platform = 'parallel' if (source.enable_twitter and source.enable_reddit) else (
                'twitter' if source.enable_twitter else 'reddit'
            )

        fork_meta = {
            'parent_simulation_id': source_id,
            'label': (label or f'{source_id} 的分支').strip()[:120],
            'event': (event or '').strip(),
            'event_round': max(0, int(event_round or 0)),
            'platform': platform,
            'auto_start': bool(auto_start),
            'injection_status': 'pending' if event else 'none',
            'created_at': datetime.now().isoformat(),
            'copied_files': copied,
        }
        _write_json(os.path.join(new_dir, _FORK_FILE), fork_meta)

        if auto_start:
            SimulationRunner.start_simulation(
                new_id,
                platform=platform,
                max_rounds=max_rounds,
            )
        if event:
            # 即使用户选择稍后手动启动，也先挂一个 watcher：它在 2 小时内等到
            # 目标轮次就会注入，避免"事件一直 pending"。
            thread = threading.Thread(
                target=cls._schedule_injection,
                args=(new_id, event, fork_meta['event_round'], platform),
                daemon=True,
            )
            thread.start()

        return {
            'simulation_id': new_id,
            'state': new_state.to_dict(),
            'fork': fork_meta,
        }

    @classmethod
    def _resolve_agent_id(cls, simulation_id: str) -> int:
        config = _read_json(os.path.join(cls._sim_dir(simulation_id), 'simulation_config.json')) or {}
        agents = config.get('agent_configs') or []
        agent_id = resolve_poster_agent_id('', agents)
        return int(agent_id or 0)

    @classmethod
    def _schedule_injection(cls, simulation_id: str, event: str,
                            event_round: int, platform: str) -> None:
        """等模拟推进到 event_round 再注入；模拟提前结束则标记 skipped。"""
        deadline = time.monotonic() + 2 * 3600
        fork_path = os.path.join(cls._sim_dir(simulation_id), _FORK_FILE)
        while time.monotonic() < deadline:
            meta = _read_json(fork_path) or {}
            run_state = SimulationRunner.get_run_state(simulation_id)
            if run_state is None:
                time.sleep(2)
                continue
            status = run_state.runner_status
            if status in (RunnerStatus.COMPLETED, RunnerStatus.STOPPED,
                          RunnerStatus.FAILED, RunnerStatus.STALLED):
                meta.update({
                    'injection_status': 'skipped',
                    'injection_note': f'模拟在注入前已结束: {status.value}',
                })
                _write_json(fork_path, meta)
                return
            if status in (RunnerStatus.RUNNING, RunnerStatus.PAUSED) and \
                    int(run_state.current_round or 0) >= int(event_round):
                try:
                    agent_id = cls._resolve_agent_id(simulation_id)
                    result = SimulationRunner.inject_event(
                        simulation_id=simulation_id,
                        agent_id=agent_id,
                        content=event,
                        platform=None,
                    )
                    meta.update({
                        'injection_status': 'injected' if result.get('success') else 'failed',
                        'injection_result': result,
                        'injected_at': datetime.now().isoformat(),
                    })
                except Exception as exc:
                    meta.update({
                        'injection_status': 'failed',
                        'injection_error': str(exc),
                        'injected_at': datetime.now().isoformat(),
                    })
                _write_json(fork_path, meta)
                return
            time.sleep(2)

        meta = _read_json(fork_path) or {}
        meta.update({'injection_status': 'timeout', 'injection_note': '等待 2 小时仍未到达注入轮次'})
        _write_json(fork_path, meta)

    # ------------------------------------------------------------ compare
    @classmethod
    def _action_stats(cls, simulation_id: str) -> Dict[str, Any]:
        counts = {}
        types = {}
        agents = {}
        per_platform = {}
        for platform in ('twitter', 'reddit'):
            path = os.path.join(cls._sim_dir(simulation_id), platform, 'actions.jsonl')
            platform_count = 0
            if os.path.isfile(path):
                try:
                    with open(path, 'r', encoding='utf-8', errors='ignore') as handle:
                        for line in handle:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                event = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            action_type = str(event.get('action_type') or '')
                            if not action_type:
                                continue
                            platform_count += 1
                            types[action_type] = types.get(action_type, 0) + 1
                            agent = str(event.get('agent_name') or '')
                            if agent:
                                agents[agent] = agents.get(agent, 0) + 1
                except OSError:
                    pass
            per_platform[platform] = platform_count
            counts[platform] = platform_count
        return {
            'total_actions': sum(counts.values()),
            'per_platform': per_platform,
            'action_types': dict(sorted(types.items(), key=lambda item: item[1], reverse=True)),
            'top_agents': [
                {'agent': name, 'actions': count}
                for name, count in sorted(agents.items(), key=lambda item: item[1], reverse=True)[:10]
            ],
        }

    @classmethod
    def compare(cls, a_id: str, b_id: str) -> Dict[str, Any]:
        manager = SimulationManager()
        a_state = manager.get_simulation(a_id)
        b_state = manager.get_simulation(b_id)
        if not a_state or not b_state:
            raise LookupError('对比的模拟不存在')

        def _one(sim_id: str, state) -> Dict[str, Any]:
            run_state = SimulationRunner.get_run_state(sim_id)
            market = MarketSimulator.overview(sim_id)
            return {
                'simulation_id': sim_id,
                'status': state.status.value,
                'runner_status': run_state.runner_status.value if run_state else None,
                'current_round': run_state.current_round if run_state else 0,
                'total_rounds': run_state.total_rounds if run_state else 0,
                'entities_count': state.entities_count,
                'profiles_count': state.profiles_count,
                'fork': cls.get_fork(sim_id),
                'actions': cls._action_stats(sim_id),
                'market': {k: market.get(k) for k in (
                    'enabled', 'question', 'price_yes', 'price_no', 'volume', 'trades_count'
                )},
            }

        a = _one(a_id, a_state)
        b = _one(b_id, b_state)
        delta = {
            'actions': b['actions']['total_actions'] - a['actions']['total_actions'],
            'price_yes': (
                round((b['market'].get('price_yes') or 0) - (a['market'].get('price_yes') or 0), 4)
                if a['market'].get('enabled') and b['market'].get('enabled') else None
            ),
            'volume': (
                round((b['market'].get('volume') or 0) - (a['market'].get('volume') or 0), 2)
                if a['market'].get('enabled') and b['market'].get('enabled') else None
            ),
        }
        return {'a': a, 'b': b, 'delta': delta}
