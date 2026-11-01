"""
冥想盆本地存储 (SQLite)

把 MacFish 的项目、模拟、报告收割成「记忆」(memories)，用「丝线」(strands)
表达记忆之间的血缘与共振关系，并把多次记忆蒸馏出的结论存成「记忆瓶」(vials)。

存储遵循 LocalGraphStore 的并发模式：每个线程一条连接、WAL、Row factory。
默认数据库路径在实例化时从 Config.UPLOAD_FOLDER 解析，避免像
ProjectManager.PROJECTS_DIR 那样在 import 时被冻结，导致测试无法重定向。
"""
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ..config import Config
from ..utils.logger import get_logger

logger = get_logger('macfish.pensieve_store')


def _now() -> str:
    return datetime.now().isoformat()


def _default_db_path() -> str:
    return os.path.abspath(os.path.join(Config.UPLOAD_FOLDER, 'pensieve.db'))


def _dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def _loads(value: Any, default: Any):
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class PensieveStore:
    """记忆 / 丝线 / 记忆瓶的本地 SQLite 存储，线程安全。"""

    _instances: Dict[str, "PensieveStore"] = {}
    _lock = threading.Lock()

    def __init__(self, db_path: str = ""):
        self.db_path = os.path.abspath(db_path or _default_db_path())
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._local = threading.local()
        self._init_db()

    @classmethod
    def get_instance(cls, db_path: str = "") -> "PensieveStore":
        path = os.path.abspath(db_path or _default_db_path())
        with cls._lock:
            if path not in cls._instances:
                cls._instances[path] = cls(path)
            return cls._instances[path]

    # ------------------------------------------------------------------ 连接
    def _get_conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, 'conn', None)
        if conn is None:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    def _init_db(self):
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                memory_id TEXT PRIMARY KEY,
                external_key TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL DEFAULT 'seed',
                title TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                source_type TEXT NOT NULL DEFAULT '',
                source_id TEXT NOT NULL DEFAULT '',
                project_id TEXT NOT NULL DEFAULT '',
                graph_id TEXT NOT NULL DEFAULT '',
                simulation_id TEXT NOT NULL DEFAULT '',
                report_id TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '[]',
                entities TEXT NOT NULL DEFAULT '[]',
                importance INTEGER NOT NULL DEFAULT 50,
                occurred_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                metadata TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_pensieve_memories_kind ON memories(kind);
            CREATE INDEX IF NOT EXISTS idx_pensieve_memories_project ON memories(project_id);
            CREATE INDEX IF NOT EXISTS idx_pensieve_memories_sim ON memories(simulation_id);
            CREATE INDEX IF NOT EXISTS idx_pensieve_memories_report ON memories(report_id);

            CREATE TABLE IF NOT EXISTS strands (
                strand_id TEXT PRIMARY KEY,
                from_memory TEXT NOT NULL,
                to_memory TEXT NOT NULL,
                relation TEXT NOT NULL DEFAULT 'resonates',
                label TEXT NOT NULL DEFAULT '',
                weight REAL NOT NULL DEFAULT 0.5,
                created_at TEXT NOT NULL DEFAULT '',
                UNIQUE(from_memory, to_memory, relation)
            );
            CREATE INDEX IF NOT EXISTS idx_pensieve_strands_from ON strands(from_memory);
            CREATE INDEX IF NOT EXISTS idx_pensieve_strands_to ON strands(to_memory);

            CREATE TABLE IF NOT EXISTS vials (
                vial_id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                question TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                memory_ids TEXT NOT NULL DEFAULT '[]',
                used_llm INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT ''
            );
        """)
        conn.commit()

        # 全文检索：trigram 分词对中文子串友好（LIKE 只能整体前缀/子串，
        # 且无法按相关度排序）。FTS5 在极少数 SQLite 构建里不可用，
        # 此时静默退回 LIKE，不影响主流程。
        self._fts_available = False
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5("
                "memory_id UNINDEXED, title, content, summary, tags, entities, "
                "tokenize='trigram')"
            )
            self._fts_available = True
            indexed = conn.execute("SELECT COUNT(*) AS c FROM memories_fts").fetchone()["c"]
            total = conn.execute("SELECT COUNT(*) AS c FROM memories").fetchone()["c"]
            if indexed == 0 and total > 0:
                conn.execute(
                    "INSERT INTO memories_fts (memory_id, title, content, summary, tags, entities) "
                    "SELECT memory_id, title, content, summary, tags, entities FROM memories"
                )
                logger.info(f"已为 {total} 条历史记忆建立全文索引")
            conn.commit()
        except sqlite3.Error as exc:
            logger.warning(f"FTS5 不可用，记忆检索退回 LIKE: {exc}")

    def _reindex_memory(self, memory_id: str) -> None:
        if not getattr(self, "_fts_available", False):
            return
        try:
            conn = self._get_conn()
            conn.execute("DELETE FROM memories_fts WHERE memory_id=?", (memory_id,))
            conn.execute(
                "INSERT INTO memories_fts (memory_id, title, content, summary, tags, entities) "
                "SELECT memory_id, title, content, summary, tags, entities "
                "FROM memories WHERE memory_id=?",
                (memory_id,),
            )
            conn.commit()
        except sqlite3.Error as exc:
            logger.debug(f"全文索引更新失败 {memory_id}: {exc}")

    @staticmethod
    def _fts_query(q: str) -> str:
        # trigram 分词下，把整段查询作为一个 phrase 最稳；双引号需转义。
        return '"' + q.replace('"', '""') + '"'

    # --------------------------------------------------------------- 记忆 CRUD
    @staticmethod
    def _row_to_memory(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "memory_id": row["memory_id"],
            "external_key": row["external_key"],
            "kind": row["kind"],
            "title": row["title"],
            "content": row["content"],
            "summary": row["summary"],
            "source_type": row["source_type"],
            "source_id": row["source_id"],
            "project_id": row["project_id"],
            "graph_id": row["graph_id"],
            "simulation_id": row["simulation_id"],
            "report_id": row["report_id"],
            "tags": _loads(row["tags"], []),
            "entities": _loads(row["entities"], []),
            "importance": row["importance"],
            "occurred_at": row["occurred_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "metadata": _loads(row["metadata"], {}),
        }

    def upsert_memory(self, memory: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
        """按 external_key 幂等写入记忆，返回 (记忆, 是否新建)。"""
        external_key = (memory.get("external_key") or "").strip()
        if not external_key:
            raise ValueError("memory.external_key 不能为空")

        conn = self._get_conn()
        existing = conn.execute(
            "SELECT memory_id, created_at FROM memories WHERE external_key=?",
            (external_key,)
        ).fetchone()
        now = _now()

        values = {
            "kind": memory.get("kind") or "seed",
            "title": memory.get("title") or "",
            "content": memory.get("content") or "",
            "summary": memory.get("summary") or "",
            "source_type": memory.get("source_type") or "",
            "source_id": memory.get("source_id") or "",
            "project_id": memory.get("project_id") or "",
            "graph_id": memory.get("graph_id") or "",
            "simulation_id": memory.get("simulation_id") or "",
            "report_id": memory.get("report_id") or "",
            "tags": _dumps(memory.get("tags") or []),
            "entities": _dumps(memory.get("entities") or []),
            "importance": int(memory.get("importance") or 50),
            "occurred_at": memory.get("occurred_at") or "",
            "metadata": _dumps(memory.get("metadata") or {}),
            "updated_at": now,
        }

        if existing:
            memory_id = existing["memory_id"]
            with conn:
                conn.execute(
                    """UPDATE memories SET kind=?, title=?, content=?, summary=?,
                       source_type=?, source_id=?, project_id=?, graph_id=?,
                       simulation_id=?, report_id=?, tags=?, entities=?,
                       importance=?, occurred_at=?, updated_at=?, metadata=?
                       WHERE memory_id=?""",
                    (values["kind"], values["title"], values["content"], values["summary"],
                     values["source_type"], values["source_id"], values["project_id"],
                     values["graph_id"], values["simulation_id"], values["report_id"],
                     values["tags"], values["entities"], values["importance"],
                     values["occurred_at"], values["updated_at"], values["metadata"],
                     memory_id)
                )
            created = False
        else:
            memory_id = memory.get("memory_id") or f"mem_{uuid.uuid4().hex[:16]}"
            with conn:
                conn.execute(
                    """INSERT INTO memories
                       (memory_id, external_key, kind, title, content, summary,
                        source_type, source_id, project_id, graph_id, simulation_id,
                        report_id, tags, entities, importance, occurred_at,
                        created_at, updated_at, metadata)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (memory_id, external_key, values["kind"], values["title"],
                     values["content"], values["summary"], values["source_type"],
                     values["source_id"], values["project_id"], values["graph_id"],
                     values["simulation_id"], values["report_id"], values["tags"],
                     values["entities"], values["importance"], values["occurred_at"],
                     now, now, values["metadata"])
                )
            created = True

        self._reindex_memory(memory_id)
        row = conn.execute("SELECT * FROM memories WHERE memory_id=?", (memory_id,)).fetchone()
        return self._row_to_memory(row), created

    def get_memory(self, memory_id: str) -> Optional[Dict[str, Any]]:
        row = self._get_conn().execute(
            "SELECT * FROM memories WHERE memory_id=?", (memory_id,)
        ).fetchone()
        return self._row_to_memory(row) if row else None

    def get_memory_by_external_key(self, external_key: str) -> Optional[Dict[str, Any]]:
        row = self._get_conn().execute(
            "SELECT * FROM memories WHERE external_key=?", (external_key,)
        ).fetchone()
        return self._row_to_memory(row) if row else None

    def _filters(self, q, kind, project_id, simulation_id, report_id, source_type):
        clauses = []
        params: List[Any] = []
        if q:
            # trigram 索引至少需要 3 个字符；更短的查询退回 LIKE。
            # 任何 FTS 语法错误都不应让检索 500，因此只在确定可用时使用。
            if getattr(self, "_fts_available", False) and len(q.strip()) >= 3:
                clauses.append(
                    "memory_id IN (SELECT memory_id FROM memories_fts "
                    "WHERE memories_fts MATCH ?)"
                )
                params.append(self._fts_query(q.strip()))
            else:
                like = f"%{q}%"
                clauses.append("(title LIKE ? OR content LIKE ? OR summary LIKE ? "
                               "OR tags LIKE ? OR entities LIKE ?)")
                params.extend([like] * 5)
        if kind:
            clauses.append("kind=?")
            params.append(kind)
        if project_id:
            clauses.append("project_id=?")
            params.append(project_id)
        if simulation_id:
            clauses.append("simulation_id=?")
            params.append(simulation_id)
        if report_id:
            clauses.append("report_id=?")
            params.append(report_id)
        if source_type:
            clauses.append("source_type=?")
            params.append(source_type)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, params

    def list_memories(self, q: str = "", kind: str = "", project_id: str = "",
                      simulation_id: str = "", report_id: str = "",
                      source_type: str = "", limit: int = 100,
                      offset: int = 0) -> List[Dict[str, Any]]:
        where, params = self._filters(q, kind, project_id, simulation_id,
                                      report_id, source_type)
        limit = max(1, min(int(limit or 100), 500))
        offset = max(0, int(offset or 0))
        rows = self._get_conn().execute(
            f"SELECT * FROM memories{where} ORDER BY importance DESC, created_at DESC "
            f"LIMIT ? OFFSET ?",
            (*params, limit, offset)
        ).fetchall()
        return [self._row_to_memory(r) for r in rows]

    def count_memories(self, q: str = "", kind: str = "", project_id: str = "",
                       simulation_id: str = "", report_id: str = "",
                       source_type: str = "") -> int:
        where, params = self._filters(q, kind, project_id, simulation_id,
                                      report_id, source_type)
        row = self._get_conn().execute(
            f"SELECT COUNT(*) AS c FROM memories{where}", params
        ).fetchone()
        return int(row["c"]) if row else 0

    def all_memories(self, limit: int = 5000) -> List[Dict[str, Any]]:
        rows = self._get_conn().execute(
            "SELECT * FROM memories ORDER BY created_at ASC LIMIT ?",
            (max(1, int(limit)),)
        ).fetchall()
        return [self._row_to_memory(r) for r in rows]

    def delete_memory(self, memory_id: str) -> bool:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT memory_id FROM memories WHERE memory_id=?", (memory_id,)
        ).fetchone()
        if not row:
            return False
        with conn:
            conn.execute(
                "DELETE FROM strands WHERE from_memory=? OR to_memory=?",
                (memory_id, memory_id)
            )
            conn.execute("DELETE FROM memories WHERE memory_id=?", (memory_id,))
            if getattr(self, "_fts_available", False):
                conn.execute("DELETE FROM memories_fts WHERE memory_id=?", (memory_id,))
        return True

    # --------------------------------------------------------------- 丝线
    def clear_strands(self) -> None:
        conn = self._get_conn()
        with conn:
            conn.execute("DELETE FROM strands")

    def add_strand(self, from_memory: str, to_memory: str, relation: str,
                   label: str = "", weight: float = 0.5) -> None:
        if not from_memory or not to_memory or from_memory == to_memory:
            return
        conn = self._get_conn()
        with conn:
            conn.execute(
                """INSERT OR IGNORE INTO strands
                   (strand_id, from_memory, to_memory, relation, label, weight, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (f"strand_{uuid.uuid4().hex[:16]}", from_memory, to_memory,
                 relation, label, float(weight), _now())
            )

    def list_strands_for_memory(self, memory_id: str, limit: int = 24) -> List[Dict[str, Any]]:
        rows = self._get_conn().execute(
            """SELECT s.*,
                      m.memory_id AS other_id, m.kind AS other_kind,
                      m.title AS other_title, m.summary AS other_summary,
                      m.source_type AS other_source_type
               FROM strands s
               JOIN memories m ON m.memory_id =
                    CASE WHEN s.from_memory=? THEN s.to_memory ELSE s.from_memory END
               WHERE s.from_memory=? OR s.to_memory=?
               ORDER BY s.weight DESC
               LIMIT ?""",
            (memory_id, memory_id, memory_id, max(1, int(limit)))
        ).fetchall()
        result = []
        for row in rows:
            result.append({
                "strand_id": row["strand_id"],
                "relation": row["relation"],
                "label": row["label"],
                "weight": row["weight"],
                "direction": "out" if row["from_memory"] == memory_id else "in",
                "memory": {
                    "memory_id": row["other_id"],
                    "kind": row["other_kind"],
                    "title": row["other_title"],
                    "summary": row["other_summary"],
                    "source_type": row["other_source_type"],
                },
            })
        return result

    def get_network(self, project_id: str = "", limit: int = 300) -> Dict[str, Any]:
        """返回一个可以直接喂给前端的丝线网络（节点 + 边）。"""
        conn = self._get_conn()
        limit = max(1, min(int(limit or 300), 800))
        if project_id:
            rows = conn.execute(
                "SELECT * FROM memories WHERE project_id=? "
                "ORDER BY importance DESC LIMIT ?",
                (project_id, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM memories ORDER BY importance DESC LIMIT ?",
                (limit,)
            ).fetchall()

        memories = [self._row_to_memory(r) for r in rows]
        ids = {m["memory_id"] for m in memories}
        nodes = [{
            "memory_id": m["memory_id"],
            "kind": m["kind"],
            "title": m["title"],
            "source_type": m["source_type"],
            "project_id": m["project_id"],
            "simulation_id": m["simulation_id"],
            "report_id": m["report_id"],
            "importance": m["importance"],
        } for m in memories]

        strand_rows = conn.execute("SELECT * FROM strands").fetchall()
        edges = []
        for row in strand_rows:
            if row["from_memory"] in ids and row["to_memory"] in ids:
                edges.append({
                    "from": row["from_memory"],
                    "to": row["to_memory"],
                    "relation": row["relation"],
                    "label": row["label"],
                    "weight": row["weight"],
                })
        return {"nodes": nodes, "edges": edges}

    # --------------------------------------------------------------- 记忆瓶
    @staticmethod
    def _row_to_vial(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "vial_id": row["vial_id"],
            "title": row["title"],
            "question": row["question"],
            "content": row["content"],
            "memory_ids": _loads(row["memory_ids"], []),
            "used_llm": bool(row["used_llm"]),
            "created_at": row["created_at"],
        }

    def add_vial(self, title: str, content: str, question: str = "",
                 memory_ids: Optional[List[str]] = None,
                 used_llm: bool = False) -> Dict[str, Any]:
        vial_id = f"vial_{uuid.uuid4().hex[:16]}"
        now = _now()
        conn = self._get_conn()
        with conn:
            conn.execute(
                """INSERT INTO vials
                   (vial_id, title, question, content, memory_ids, used_llm, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (vial_id, title, question, content,
                 _dumps(memory_ids or []), 1 if used_llm else 0, now)
            )
        row = conn.execute("SELECT * FROM vials WHERE vial_id=?", (vial_id,)).fetchone()
        return self._row_to_vial(row)

    def list_vials(self, limit: int = 50) -> List[Dict[str, Any]]:
        rows = self._get_conn().execute(
            "SELECT * FROM vials ORDER BY created_at DESC LIMIT ?",
            (max(1, int(limit)),)
        ).fetchall()
        return [self._row_to_vial(r) for r in rows]

    def get_vial(self, vial_id: str) -> Optional[Dict[str, Any]]:
        row = self._get_conn().execute(
            "SELECT * FROM vials WHERE vial_id=?", (vial_id,)
        ).fetchone()
        return self._row_to_vial(row) if row else None

    def delete_vial(self, vial_id: str) -> bool:
        conn = self._get_conn()
        row = conn.execute("SELECT vial_id FROM vials WHERE vial_id=?", (vial_id,)).fetchone()
        if not row:
            return False
        with conn:
            conn.execute("DELETE FROM vials WHERE vial_id=?", (vial_id,))
        return True

    # --------------------------------------------------------------- 统计
    def stats(self) -> Dict[str, Any]:
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) AS c FROM memories").fetchone()["c"]
        strand_count = conn.execute("SELECT COUNT(*) AS c FROM strands").fetchone()["c"]
        vial_count = conn.execute("SELECT COUNT(*) AS c FROM vials").fetchone()["c"]
        by_kind = {
            row["kind"]: row["c"]
            for row in conn.execute(
                "SELECT kind, COUNT(*) AS c FROM memories GROUP BY kind"
            ).fetchall()
        }
        source_row = conn.execute(
            """SELECT
                 COUNT(DISTINCT CASE WHEN project_id<>'' THEN project_id END) AS projects,
                 COUNT(DISTINCT CASE WHEN simulation_id<>'' THEN simulation_id END) AS simulations,
                 COUNT(DISTINCT CASE WHEN report_id<>'' THEN report_id END) AS reports
               FROM memories"""
        ).fetchone()
        return {
            "memories": int(total),
            "strands": int(strand_count),
            "vials": int(vial_count),
            "by_kind": by_kind,
            "projects": int(source_row["projects"] or 0),
            "simulations": int(source_row["simulations"] or 0),
            "reports": int(source_row["reports"] or 0),
        }
