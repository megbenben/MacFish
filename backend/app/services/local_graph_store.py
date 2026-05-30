"""
本地知识图谱存储 (SQLite)
替代 Zep Cloud，实体/关系数据全部本地化存储
"""
import json
import os
import sqlite3
import threading
import uuid as uuid_mod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# --- Dataclass-compatible objects mimicking zep_cloud types ---

class LocalNode:
    """兼容 zep_cloud 节点对象"""
    def __init__(self, uuid_: str, name: str, labels: list, summary: str,
                 attributes: dict, graph_id: str, created_at: str = ""):
        self.uuid_ = uuid_
        self.name = name
        self.labels = labels or []
        self.summary = summary or ""
        self.attributes = attributes or {}
        self.graph_id = graph_id
        self.created_at = created_at

    def to_dict(self):
        return {
            "uuid_": self.uuid_, "name": self.name, "labels": self.labels,
            "summary": self.summary, "attributes": self.attributes,
            "graph_id": self.graph_id, "created_at": self.created_at,
        }


class LocalEdge:
    """兼容 zep_cloud 边对象"""
    def __init__(self, uuid_: str, name: str, fact: str, source_node_uuid: str,
                 target_node_uuid: str, attributes: dict, graph_id: str,
                 created_at: str = "", valid_at: str = "",
                 invalid_at: str = "", expired_at: str = ""):
        self.uuid_ = uuid_
        self.name = name
        self.fact = fact
        self.source_node_uuid = source_node_uuid
        self.target_node_uuid = target_node_uuid
        self.attributes = attributes or {}
        self.graph_id = graph_id
        self.created_at = created_at
        self.valid_at = valid_at
        self.invalid_at = invalid_at
        self.expired_at = expired_at

    def to_dict(self):
        return {
            "uuid_": self.uuid_, "name": self.name, "fact": self.fact,
            "source_node_uuid": self.source_node_uuid,
            "target_node_uuid": self.target_node_uuid,
            "attributes": self.attributes, "graph_id": self.graph_id,
            "created_at": self.created_at, "valid_at": self.valid_at,
            "invalid_at": self.invalid_at, "expired_at": self.expired_at,
        }


class LocalEpisode:
    """兼容 zep_cloud EpisodeData / Episode 对象"""
    def __init__(self, uuid_: str, data: str, type_: str = "text",
                 processed: bool = True, graph_id: str = ""):
        self.uuid_ = uuid_
        self.uuid = uuid_   # 兼容两种访问方式
        self.data = data
        self.type = type_
        self.processed = processed
        self.graph_id = graph_id


class LocalSearchResult:
    """兼容 zep_cloud 搜索结果"""
    def __init__(self, edges: List[LocalEdge], nodes: List[LocalNode]):
        self.edges = edges or []
        self.nodes = nodes or []


# --- SQLite 存储引擎 ---

class LocalGraphStore:
    """本地 SQLite 图存储，线程安全"""

    _instances: Dict[str, "LocalGraphStore"] = {}
    _lock = threading.Lock()

    def __init__(self, db_path: str = ""):
        if not db_path:
            db_path = os.path.join(os.path.dirname(__file__), "..", "..", "uploads", "graphs.db")
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._local = threading.local()
        self._init_db()

    @classmethod
    def get_instance(cls, db_path: str = "") -> "LocalGraphStore":
        key = db_path or "default"
        with cls._lock:
            if key not in cls._instances:
                cls._instances[key] = LocalGraphStore(db_path)
            return cls._instances[key]

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA foreign_keys=ON")
        return self._local.conn

    def _init_db(self):
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS graphs (
                graph_id TEXT PRIMARY KEY,
                name TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                ontology TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS nodes (
                uuid_ TEXT PRIMARY KEY,
                graph_id TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                labels TEXT NOT NULL DEFAULT '[]',
                summary TEXT NOT NULL DEFAULT '',
                attributes TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (graph_id) REFERENCES graphs(graph_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS edges (
                uuid_ TEXT PRIMARY KEY,
                graph_id TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                fact TEXT NOT NULL DEFAULT '',
                source_node_uuid TEXT NOT NULL DEFAULT '',
                target_node_uuid TEXT NOT NULL DEFAULT '',
                attributes TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                valid_at TEXT NOT NULL DEFAULT '',
                invalid_at TEXT NOT NULL DEFAULT '',
                expired_at TEXT NOT NULL DEFAULT '',
                episodes TEXT NOT NULL DEFAULT '[]',
                FOREIGN KEY (graph_id) REFERENCES graphs(graph_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS episodes (
                uuid_ TEXT PRIMARY KEY,
                graph_id TEXT NOT NULL,
                data TEXT NOT NULL DEFAULT '',
                type TEXT NOT NULL DEFAULT 'text',
                processed INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (graph_id) REFERENCES graphs(graph_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_nodes_graph ON nodes(graph_id);
            CREATE INDEX IF NOT EXISTS idx_edges_graph ON edges(graph_id);
            CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_node_uuid);
            CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_node_uuid);
            CREATE INDEX IF NOT EXISTS idx_episodes_graph ON episodes(graph_id);
        """)
        conn.commit()

    # --- Graph CRUD ---

    def create_graph(self, graph_id: str, name: str = "", description: str = ""):
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO graphs (graph_id, name, description) VALUES (?,?,?)",
            (graph_id, name, description)
        )
        conn.commit()

    def delete_graph(self, graph_id: str):
        conn = self._get_conn()
        conn.execute("DELETE FROM episodes WHERE graph_id=?", (graph_id,))
        conn.execute("DELETE FROM edges WHERE graph_id=?", (graph_id,))
        conn.execute("DELETE FROM nodes WHERE graph_id=?", (graph_id,))
        conn.execute("DELETE FROM graphs WHERE graph_id=?", (graph_id,))
        conn.commit()

    def set_ontology(self, graph_id: str, ontology: dict):
        conn = self._get_conn()
        conn.execute(
            "UPDATE graphs SET ontology=? WHERE graph_id=?",
            (json.dumps(ontology, ensure_ascii=False), graph_id)
        )
        conn.commit()

    def get_ontology(self, graph_id: str) -> dict:
        conn = self._get_conn()
        row = conn.execute("SELECT ontology FROM graphs WHERE graph_id=?", (graph_id,)).fetchone()
        if row:
            return json.loads(row["ontology"])
        return {}

    # --- Episode / Chunk 存储 ---

    def add_episodes(self, graph_id: str, episodes: list) -> List[LocalEpisode]:
        """批量添加 episodes 并返回带 UUID 的 episode 列表"""
        conn = self._get_conn()
        result = []
        for ep in episodes:
            ep_uuid = str(uuid_mod.uuid4())
            data_text = ep.data if hasattr(ep, "data") else str(ep)
            type_text = ep.type if hasattr(ep, "type") else "text"
            conn.execute(
                "INSERT INTO episodes (uuid_, graph_id, data, type, processed) VALUES (?,?,?,?,1)",
                (ep_uuid, graph_id, data_text, type_text)
            )
            result.append(LocalEpisode(uuid_=ep_uuid, data=data_text, type_=type_text, graph_id=graph_id))
        conn.commit()
        return result

    def add_episode(self, graph_id: str, data: str, type_: str = "text") -> LocalEpisode:
        """添加单个 episode"""
        return self.add_episodes(graph_id, [LocalEpisode(uuid_="", data=data, type_=type_)])[0]

    def get_episode(self, uuid_: str) -> Optional[LocalEpisode]:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM episodes WHERE uuid_=?", (uuid_,)).fetchone()
        if row:
            return LocalEpisode(uuid_=row["uuid_"], data=row["data"],
                               type_=row["type"], processed=bool(row["processed"]),
                               graph_id=row["graph_id"])
        return None

    # --- Node CRUD ---

    def upsert_node(self, node: LocalNode) -> LocalNode:
        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO nodes (uuid_, graph_id, name, labels, summary, attributes, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (node.uuid_, node.graph_id, node.name,
             json.dumps(node.labels, ensure_ascii=False),
             node.summary,
             json.dumps(node.attributes, ensure_ascii=False),
             node.created_at or datetime.now(timezone.utc).isoformat())
        )
        conn.commit()
        return node

    def get_node(self, uuid_: str) -> Optional[LocalNode]:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM nodes WHERE uuid_=?", (uuid_,)).fetchone()
        if row:
            return self._row_to_node(row)
        return None

    def get_nodes_by_graph(self, graph_id: str, limit: int = 100,
                           uuid_cursor: str = "") -> List[LocalNode]:
        conn = self._get_conn()
        if uuid_cursor:
            rows = conn.execute(
                "SELECT * FROM nodes WHERE graph_id=? AND uuid_>? ORDER BY uuid_ LIMIT ?",
                (graph_id, uuid_cursor, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM nodes WHERE graph_id=? ORDER BY uuid_ LIMIT ?",
                (graph_id, limit)
            ).fetchall()
        return [self._row_to_node(r) for r in rows]

    def get_entity_edges(self, node_uuid: str) -> List[LocalEdge]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM edges WHERE source_node_uuid=? OR target_node_uuid=?",
            (node_uuid, node_uuid)
        ).fetchall()
        return [self._row_to_edge(r) for r in rows]

    # --- Edge CRUD ---

    def upsert_edge(self, edge: LocalEdge) -> LocalEdge:
        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO edges
               (uuid_, graph_id, name, fact, source_node_uuid, target_node_uuid,
                attributes, created_at, valid_at, invalid_at, expired_at, episodes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (edge.uuid_, edge.graph_id, edge.name, edge.fact,
             edge.source_node_uuid, edge.target_node_uuid,
             json.dumps(edge.attributes, ensure_ascii=False),
             edge.created_at or datetime.now(timezone.utc).isoformat(),
             edge.valid_at, edge.invalid_at, edge.expired_at,
             json.dumps(getattr(edge, 'episodes', []), ensure_ascii=False))
        )
        conn.commit()
        return edge

    def get_edge(self, uuid_: str) -> Optional[LocalEdge]:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM edges WHERE uuid_=?", (uuid_,)).fetchone()
        if row:
            return self._row_to_edge(row)
        return None

    def get_edges_by_graph(self, graph_id: str, limit: int = 100,
                           uuid_cursor: str = "") -> List[LocalEdge]:
        conn = self._get_conn()
        if uuid_cursor:
            rows = conn.execute(
                "SELECT * FROM edges WHERE graph_id=? AND uuid_>? ORDER BY uuid_ LIMIT ?",
                (graph_id, uuid_cursor, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM edges WHERE graph_id=? ORDER BY uuid_ LIMIT ?",
                (graph_id, limit)
            ).fetchall()
        return [self._row_to_edge(r) for r in rows]

    def get_all_nodes(self, graph_id: str) -> List[LocalNode]:
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM nodes WHERE graph_id=?", (graph_id,)).fetchall()
        return [self._row_to_node(r) for r in rows]

    def get_all_edges(self, graph_id: str) -> List[LocalEdge]:
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM edges WHERE graph_id=?", (graph_id,)).fetchall()
        return [self._row_to_edge(r) for r in rows]

    def search_nodes(self, graph_id: str, query: str, limit: int = 10) -> List[LocalNode]:
        """关键词搜索节点（简单 LIKE 匹配，语义搜索在 DeepSeekGraphExtractor 中完成）"""
        conn = self._get_conn()
        like_q = f"%{query}%"
        rows = conn.execute(
            """SELECT * FROM nodes WHERE graph_id=?
               AND (name LIKE ? OR summary LIKE ? OR labels LIKE ?)
               LIMIT ?""",
            (graph_id, like_q, like_q, like_q, limit)
        ).fetchall()
        return [self._row_to_node(r) for r in rows]

    def search_edges(self, graph_id: str, query: str, limit: int = 10) -> List[LocalEdge]:
        """关键词搜索边"""
        conn = self._get_conn()
        like_q = f"%{query}%"
        rows = conn.execute(
            """SELECT * FROM edges WHERE graph_id=?
               AND (fact LIKE ? OR name LIKE ?)
               LIMIT ?""",
            (graph_id, like_q, like_q, limit)
        ).fetchall()
        return [self._row_to_edge(r) for r in rows]

    # --- 统计 ---

    def get_node_count(self, graph_id: str) -> int:
        conn = self._get_conn()
        row = conn.execute("SELECT COUNT(*) as c FROM nodes WHERE graph_id=?", (graph_id,)).fetchone()
        return row["c"]

    def get_edge_count(self, graph_id: str) -> int:
        conn = self._get_conn()
        row = conn.execute("SELECT COUNT(*) as c FROM edges WHERE graph_id=?", (graph_id,)).fetchone()
        return row["c"]

    # --- Helpers ---

    def _row_to_node(self, row) -> LocalNode:
        return LocalNode(
            uuid_=row["uuid_"], name=row["name"],
            labels=json.loads(row["labels"]) if row["labels"] else [],
            summary=row["summary"],
            attributes=json.loads(row["attributes"]) if row["attributes"] else {},
            graph_id=row["graph_id"], created_at=row["created_at"],
        )

    def _row_to_edge(self, row) -> LocalEdge:
        return LocalEdge(
            uuid_=row["uuid_"], name=row["name"], fact=row["fact"],
            source_node_uuid=row["source_node_uuid"],
            target_node_uuid=row["target_node_uuid"],
            attributes=json.loads(row["attributes"]) if row["attributes"] else {},
            graph_id=row["graph_id"], created_at=row["created_at"],
            valid_at=row["valid_at"], invalid_at=row["invalid_at"],
            expired_at=row["expired_at"],
        )
