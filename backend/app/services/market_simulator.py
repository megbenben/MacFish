"""
预测市场模拟器（LMSR）
=====================

P1-3 的第一部分：在不改动 OASIS 引擎的前提下，给每次社会模拟加一个
**二元预测市场**作为派生平台。

设计取舍
--------
- 不额外调用 LLM：每轮把 Twitter/Reddit 的帖子与评论当作"交易信号"。
  内容偏正面 → 买入 YES；偏负面 → 买入 NO。价格用 LMSR 更新。
- 增量处理：按 `actions.jsonl` 的字节偏移做游标，监控线程每轮调用一次，
  不会重复吃同一条动作。
- 可解释：每笔交易都记录触发的原文、平台、Agent、轮次与价格前后值。

这比"让每个 Agent 额外跑一次交易决策"便宜一个数量级，也避免把模拟成本
再翻一倍。市场不是独立 OASIS 平台，而是社会模拟的**观测/聚合层**——
UI 与 API 里都会明确标注。

存储：`<sim>/market/market.db`（SQLite，WAL）
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

from ..config import Config
from ..utils.logger import get_logger

logger = get_logger('mirofish.market')

#: 只把这些动作当成交易信号
_TRADABLE_ACTIONS = {'CREATE_POST', 'CREATE_COMMENT', 'QUOTE_POST'}

#: 正/负面词表。强词权重 2，弱词权重 1；命中正负相抵为 0 时不交易。
_POSITIVE_STRONG = (
    '上涨', '增长', '提升', '改善', '成功', '达成', '利好', '突破', '加速',
    '扩大', '支持', '看好', '回暖', '复苏', '超预期', '落地',
)
# 「会 / 将 / 可能」在中文里太常见，会把中性叙述也判成看多，故只保留有立场含义的词
_POSITIVE_WEAK = ('机会', '优势', '认可', '推进')
_NEGATIVE_STRONG = (
    '下跌', '下降', '失败', '受阻', '风险', '恶化', '延期', '取消', '反对',
    '质疑', '危机', '放缓', '受限', '亏损', '崩盘', '不及预期',
)
_NEGATIVE_WEAK = ('不会', '难以', '担忧', '压力', '不确定', '谨慎', '观望', '挑战')


def lmsr_price(q_yes: float, q_no: float, b: float) -> float:
    """二元 LMSR 的 YES 价格。b 越大价格越"黏"。

    显示价格夹在 [0.01, 0.99]：预测市场不应该输出"100% 确定"，
    那只是在掩盖模型/信号的不确定性。
    """
    b = max(float(b or 1.0), 1e-6)
    diff = (float(q_yes) - float(q_no)) / b
    # 防止 exp 溢出
    if diff > 60:
        return 0.99
    if diff < -60:
        return 0.01
    price = 1.0 / (1.0 + math.exp(-diff))
    return min(0.99, max(0.01, price))


def stance_score(text: str) -> int:
    """把一段文本压成 [-N, N] 的立场分。确定性、可解释。"""
    content = str(text or '')
    if len(content) < 6:
        return 0
    score = 0
    for word in _POSITIVE_STRONG:
        if word in content:
            score += 2
    for word in _POSITIVE_WEAK:
        if word in content:
            score += 1
    for word in _NEGATIVE_STRONG:
        if word in content:
            score -= 2
    for word in _NEGATIVE_WEAK:
        if word in content:
            score -= 1
    return score


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class MarketSimulator:
    """按 simulation_id 维护一个二元预测市场。"""

    _locks: Dict[str, threading.Lock] = {}
    _locks_guard = threading.Lock()

    # ------------------------------------------------------------ paths / schema
    @classmethod
    def _sim_dir(cls, simulation_id: str) -> str:
        return os.path.join(Config.OASIS_SIMULATION_DATA_DIR, simulation_id)

    @classmethod
    def _db_path(cls, simulation_id: str) -> str:
        return os.path.join(cls._sim_dir(simulation_id), 'market', 'market.db')

    @classmethod
    def _lock_for(cls, simulation_id: str) -> threading.Lock:
        with cls._locks_guard:
            lock = cls._locks.get(simulation_id)
            if lock is None:
                lock = threading.Lock()
                cls._locks[simulation_id] = lock
            return lock

    @classmethod
    def _connect(cls, simulation_id: str) -> sqlite3.Connection:
        path = cls._db_path(simulation_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        conn = sqlite3.connect(path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA busy_timeout=30000')
        return conn

    @classmethod
    def _ensure_schema(cls, conn: sqlite3.Connection, simulation_id: str) -> None:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS market (
                simulation_id TEXT PRIMARY KEY,
                question TEXT NOT NULL DEFAULT '',
                initial_price REAL NOT NULL DEFAULT 0.5,
                liquidity_b REAL NOT NULL DEFAULT 20.0,
                q_yes REAL NOT NULL DEFAULT 0,
                q_no REAL NOT NULL DEFAULT 0,
                price_yes REAL NOT NULL DEFAULT 0.5,
                volume REAL NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS trades (
                trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
                round INTEGER NOT NULL DEFAULT 0,
                timestamp TEXT NOT NULL DEFAULT '',
                platform TEXT NOT NULL DEFAULT '',
                agent TEXT NOT NULL DEFAULT '',
                action_type TEXT NOT NULL DEFAULT '',
                side TEXT NOT NULL DEFAULT '',
                shares INTEGER NOT NULL DEFAULT 1,
                price_before REAL NOT NULL DEFAULT 0.5,
                price_after REAL NOT NULL DEFAULT 0.5,
                reason TEXT NOT NULL DEFAULT '',
                source_id TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS snapshots (
                snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                round INTEGER NOT NULL DEFAULT 0,
                price_yes REAL NOT NULL DEFAULT 0.5,
                volume REAL NOT NULL DEFAULT 0,
                yes_shares REAL NOT NULL DEFAULT 0,
                no_shares REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_market_trades_round ON trades(round);
            CREATE TABLE IF NOT EXISTS cursors (
                source TEXT PRIMARY KEY,
                offset INTEGER NOT NULL DEFAULT 0
            );
        """)

        # 机制版本升级：旧版用过高频弱词 + 固定 b，价格会饱和到 1.0。
        # 版本不一致时清空重建，避免留下不可解释的历史成交。
        version = conn.execute('PRAGMA user_version').fetchone()[0]
        if version < 2:
            for table in ('trades', 'snapshots', 'cursors', 'market'):
                try:
                    conn.execute(f'DELETE FROM {table}')
                except sqlite3.Error:
                    pass
            conn.execute('PRAGMA user_version=2')
            conn.commit()

        row = conn.execute(
            'SELECT simulation_id FROM market WHERE simulation_id=?', (simulation_id,)
        ).fetchone()
        if row:
            return

        question, initial_price, liquidity = cls._read_market_config(simulation_id)
        b = max(float(liquidity), 1.0)
        # 初始价格 p 用 q_yes 的偏移表达：p = sigmoid(q_yes / b)
        p = min(max(float(initial_price), 0.01), 0.99)
        q_yes = b * math.log(p / (1 - p))
        conn.execute(
            'INSERT INTO market (simulation_id, question, initial_price, liquidity_b, '
            'q_yes, q_no, price_yes, volume, updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
            (simulation_id, question, p, b, q_yes, 1e-9, lmsr_price(q_yes, 0, b), 0,
             time.strftime('%Y-%m-%dT%H:%M:%S')),
        )
        conn.commit()

    @classmethod
    def _read_market_config(cls, simulation_id: str) -> tuple:
        config_path = os.path.join(cls._sim_dir(simulation_id), 'simulation_config.json')
        requirement = ''
        market_cfg: Dict[str, Any] = {}
        try:
            with open(config_path, 'r', encoding='utf-8') as handle:
                config = json.load(handle)
            requirement = str(config.get('simulation_requirement') or '')
            market_cfg = config.get('market') or {}
        except (OSError, json.JSONDecodeError):
            pass

        if market_cfg.get('question'):
            question = str(market_cfg.get('question')).strip()
        elif requirement:
            question = requirement.strip()
            if not question.endswith(('?', '？')):
                question = f'模拟窗口内，以下结果是否会发生：{question}'
        else:
            question = '模拟需求所描述的结果是否会发生？'
        if len(question) > 220:
            question = question[:217] + '…'
        return (
            question,
            market_cfg.get('initial_probability', Config.MARKET_INITIAL_PROBABILITY),
            market_cfg.get('liquidity', Config.MARKET_LIQUIDITY),
        )

    # ------------------------------------------------------------ processing
    @classmethod
    def is_enabled(cls, simulation_id: str) -> bool:
        path = os.path.join(cls._sim_dir(simulation_id), 'state.json')
        try:
            with open(path, 'r', encoding='utf-8') as handle:
                return bool(json.load(handle).get('enable_market', True))
        except (OSError, json.JSONDecodeError):
            return True

    @classmethod
    def reset(cls, simulation_id: str) -> None:
        """模拟重跑时清空市场，避免旧成交污染新价格。"""
        if not cls.is_enabled(simulation_id):
            return
        with cls._lock_for(simulation_id):
            conn = cls._connect(simulation_id)
            try:
                conn.execute('DELETE FROM trades WHERE 1=1')
                conn.execute('DELETE FROM snapshots WHERE 1=1')
                conn.execute('DELETE FROM cursors WHERE 1=1')
                conn.execute('DELETE FROM market WHERE simulation_id=?', (simulation_id,))
                conn.commit()
                cls._ensure_schema(conn, simulation_id)
            finally:
                conn.close()

    @classmethod
    def process(cls, simulation_id: str) -> Dict[str, Any]:
        """增量处理新的社群动作，返回最新市场概览。"""
        if not cls.is_enabled(simulation_id):
            return {'enabled': False, 'simulation_id': simulation_id}
        with cls._lock_for(simulation_id):
            conn = cls._connect(simulation_id)
            try:
                cls._ensure_schema(conn, simulation_id)
                for platform in ('twitter', 'reddit'):
                    cls._process_platform(conn, simulation_id, platform)
                conn.commit()
                return cls._overview_from_conn(conn, simulation_id)
            finally:
                conn.close()

    @classmethod
    def _process_platform(cls, conn: sqlite3.Connection, simulation_id: str,
                          platform: str) -> None:
        path = os.path.join(cls._sim_dir(simulation_id), platform, 'actions.jsonl')
        if not os.path.isfile(path):
            return

        size = os.path.getsize(path)
        row = conn.execute(
            'SELECT offset FROM cursors WHERE source=?', (f'{platform}:actions',)
        ).fetchone()
        offset = _safe_int(row['offset']) if row else 0
        if size < offset:
            # 文件被清理/重写（模拟重跑）：市场也回到初始状态
            logger.info(f'检测到 {platform} actions 被重写，重置市场: {simulation_id}')
            conn.execute('DELETE FROM trades WHERE 1=1')
            conn.execute('DELETE FROM snapshots WHERE 1=1')
            conn.execute('DELETE FROM market WHERE simulation_id=?', (simulation_id,))
            conn.commit()
            cls._ensure_schema(conn, simulation_id)
            offset = 0

        if size == offset:
            return

        market = conn.execute(
            'SELECT * FROM market WHERE simulation_id=?', (simulation_id,)
        ).fetchone()
        if not market:
            return
        b = float(market['liquidity_b'])
        q_yes = float(market['q_yes'])
        q_no = float(market['q_no'])
        volume = float(market['volume'])
        snapshots: Dict[int, float] = {}

        with open(path, 'r', encoding='utf-8', errors='ignore') as handle:
            handle.seek(offset)
            while True:
                line = handle.readline()
                if not line:
                    break
                # 写入方可能正写到一半：没有换行就等下一轮，别把半行 JSON 当成坏数据吞掉
                if not line.endswith('\n'):
                    break
                line_end = handle.tell()
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    offset = line_end
                    continue
                offset = line_end
                if not isinstance(event, dict):
                    continue
                action_type = str(event.get('action_type') or '')
                if action_type not in _TRADABLE_ACTIONS:
                    continue
                args = event.get('action_args') or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {'content': args}
                content = str(
                    (args or {}).get('content')
                    or (args or {}).get('quote_content')
                    or ''
                ).strip()
                score = stance_score(content)
                if score == 0:
                    continue
                side = 'YES' if score > 0 else 'NO'
                shares = 1 + min(2, abs(score) // 3)
                # 市场深度随成交量增长：否则几百条同向帖子会把价格顶到 99%，
                # 后续信息就再也无法反映到价格里。
                b_effective = max(b, volume / 2.0)
                price_before = lmsr_price(q_yes, q_no, b_effective)
                if side == 'YES':
                    q_yes += shares
                else:
                    q_no += shares
                price_after = lmsr_price(q_yes, q_no, b_effective)
                volume += shares
                round_no = _safe_int(event.get('round'))
                source_id = f"{platform}:{line_end}"
                try:
                    conn.execute(
                        'INSERT INTO trades (round, timestamp, platform, agent, '
                        'action_type, side, shares, price_before, price_after, reason, source_id) '
                        'VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                        (round_no, str(event.get('timestamp') or ''), platform,
                         str(event.get('agent_name') or event.get('agent_id') or ''),
                         action_type, side, shares, price_before, price_after,
                         content[:500], source_id),
                    )
                except sqlite3.IntegrityError:
                    # 游标竞态下重复读同一行：跳过
                    continue
                snapshots[round_no] = price_after

        now = time.strftime('%Y-%m-%dT%H:%M:%S')
        conn.execute(
            'UPDATE market SET q_yes=?, q_no=?, price_yes=?, volume=?, updated_at=? '
            'WHERE simulation_id=?',
            (q_yes, q_no, lmsr_price(q_yes, q_no, max(b, volume / 2.0)),
             volume, now, simulation_id),
        )
        for round_no, price in sorted(snapshots.items()):
            existing = conn.execute(
                'SELECT snapshot_id FROM snapshots WHERE round=?', (round_no,)
            ).fetchone()
            if existing:
                conn.execute(
                    'UPDATE snapshots SET price_yes=?, volume=?, yes_shares=?, no_shares=? '
                    'WHERE round=?',
                    (price, volume, q_yes, q_no, round_no),
                )
            else:
                conn.execute(
                    'INSERT INTO snapshots (round, price_yes, volume, yes_shares, '
                    'no_shares, created_at) VALUES (?,?,?,?,?,?)',
                    (round_no, price, volume, q_yes, q_no, now),
                )
        conn.execute(
            'INSERT INTO cursors (source, offset) VALUES (?,?) '
            'ON CONFLICT(source) DO UPDATE SET offset=excluded.offset',
            (f'{platform}:actions', offset),
        )

    # ------------------------------------------------------------ read API
    @classmethod
    def _overview_from_conn(cls, conn: sqlite3.Connection,
                            simulation_id: str) -> Dict[str, Any]:
        market = conn.execute(
            'SELECT * FROM market WHERE simulation_id=?', (simulation_id,)
        ).fetchone()
        if not market:
            return {'enabled': False, 'simulation_id': simulation_id}
        trades = conn.execute(
            'SELECT * FROM trades ORDER BY trade_id DESC LIMIT 50'
        ).fetchall()
        history = conn.execute(
            'SELECT round, price_yes, volume, yes_shares, no_shares '
            'FROM snapshots ORDER BY round ASC LIMIT 500'
        ).fetchall()
        count = conn.execute('SELECT COUNT(*) AS c FROM trades').fetchone()['c']
        price_yes = float(market['price_yes'])
        return {
            'enabled': True,
            'simulation_id': simulation_id,
            'question': market['question'],
            'price_yes': round(price_yes, 4),
            'price_no': round(1.0 - price_yes, 4),
            'initial_price': float(market['initial_price']),
            'liquidity_b': float(market['liquidity_b']),
            'volume': float(market['volume']),
            'yes_shares': float(market['q_yes']),
            'no_shares': float(market['q_no']),
            'trades_count': int(count),
            'updated_at': market['updated_at'],
            'history': [dict(row) for row in history],
            'recent_trades': [dict(row) for row in trades],
            'note': '预测市场由社群言论驱动，不是独立的 OASIS 交易平台',
        }

    @classmethod
    def overview(cls, simulation_id: str, process: bool = True) -> Dict[str, Any]:
        if not os.path.isdir(cls._sim_dir(simulation_id)):
            return {'enabled': False, 'simulation_id': simulation_id}
        if process:
            try:
                return cls.process(simulation_id)
            except Exception as exc:
                logger.warning(f'市场增量处理失败，返回只读快照: {exc}')
        conn = cls._connect(simulation_id)
        try:
            cls._ensure_schema(conn, simulation_id)
            return cls._overview_from_conn(conn, simulation_id)
        finally:
            conn.close()
