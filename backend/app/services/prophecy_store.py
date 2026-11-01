"""
水晶预言球本地存储 (SQLite)

一颗预言球 = 一条被长期追踪的预测：预言正文、置信度、期限、情景分支、
先兆指标、问卜记录与最终应验结果。
"""
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..config import Config
from ..utils.logger import get_logger

logger = get_logger('macfish.prophecy_store')


def _now() -> str:
    return datetime.now().isoformat()


def _default_db_path() -> str:
    return os.path.abspath(os.path.join(Config.UPLOAD_FOLDER, 'prophecy.db'))


def _dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def _loads(value: Any, default: Any):
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class ProphecyStore:
    """预言球 / 问卜记录的本地 SQLite 存储，线程安全。"""

    _instances: Dict[str, "ProphecyStore"] = {}
    _lock = threading.Lock()

    def __init__(self, db_path: str = ""):
        self.db_path = os.path.abspath(db_path or _default_db_path())
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._local = threading.local()
        self._init_db()

    @classmethod
    def get_instance(cls, db_path: str = "") -> "ProphecyStore":
        path = os.path.abspath(db_path or _default_db_path())
        with cls._lock:
            if path not in cls._instances:
                cls._instances[path] = cls(path)
            return cls._instances[path]

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
            CREATE TABLE IF NOT EXISTS orbs (
                orb_id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                prophecy TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'sealed',
                confidence INTEGER NOT NULL DEFAULT 50,
                horizon_days INTEGER NOT NULL DEFAULT 90,
                deadline TEXT NOT NULL DEFAULT '',
                seer TEXT NOT NULL DEFAULT '',
                source_report_id TEXT NOT NULL DEFAULT '',
                source_simulation_id TEXT NOT NULL DEFAULT '',
                project_id TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '[]',
                entities TEXT NOT NULL DEFAULT '[]',
                branches TEXT NOT NULL DEFAULT '[]',
                indicators TEXT NOT NULL DEFAULT '[]',
                payload TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '',
                opened_at TEXT NOT NULL DEFAULT '',
                verified_at TEXT NOT NULL DEFAULT '',
                outcome TEXT NOT NULL DEFAULT '',
                accuracy INTEGER,
                notes TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_prophecy_orbs_report
                ON orbs(source_report_id);
            CREATE INDEX IF NOT EXISTS idx_prophecy_orbs_status ON orbs(status);

            CREATE TABLE IF NOT EXISTS scryings (
                scrying_id TEXT PRIMARY KEY,
                orb_id TEXT NOT NULL,
                question TEXT NOT NULL DEFAULT '',
                answer TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_prophecy_scryings_orb
                ON scryings(orb_id);
        """)
        conn.commit()

    # --------------------------------------------------------------- ORBS
    @staticmethod
    def _row_to_orb(row: sqlite3.Row, scryings: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        orb = {
            "orb_id": row["orb_id"],
            "title": row["title"],
            "prophecy": row["prophecy"],
            "summary": row["summary"],
            "status": row["status"],
            "confidence": row["confidence"],
            "horizon_days": row["horizon_days"],
            "deadline": row["deadline"],
            "seer": row["seer"],
            "source_report_id": row["source_report_id"],
            "source_simulation_id": row["source_simulation_id"],
            "project_id": row["project_id"],
            "tags": _loads(row["tags"], []),
            "entities": _loads(row["entities"], []),
            "branches": _loads(row["branches"], []),
            "indicators": _loads(row["indicators"], []),
            "payload": _loads(row["payload"], {}),
            "created_at": row["created_at"],
            "opened_at": row["opened_at"],
            "verified_at": row["verified_at"],
            "outcome": row["outcome"],
            "accuracy": row["accuracy"],
            "notes": row["notes"],
        }
        if scryings is not None:
            orb["scryings"] = scryings
        return orb

    def upsert_orb(self, orb: Dict[str, Any]) -> Dict[str, Any]:
        orb_id = orb.get("orb_id") or f"orb_{uuid.uuid4().hex[:16]}"
        conn = self._get_conn()
        existing = conn.execute(
            "SELECT orb_id, created_at FROM orbs WHERE orb_id=?", (orb_id,)
        ).fetchone()
        now = _now()
        values = (
            orb_id,
            orb.get("title") or "",
            orb.get("prophecy") or "",
            orb.get("summary") or "",
            orb.get("status") or "sealed",
            int(orb.get("confidence") or 50),
            int(orb.get("horizon_days") or 90),
            orb.get("deadline") or "",
            orb.get("seer") or "",
            orb.get("source_report_id") or "",
            orb.get("source_simulation_id") or "",
            orb.get("project_id") or "",
            _dumps(orb.get("tags") or []),
            _dumps(orb.get("entities") or []),
            _dumps(orb.get("branches") or []),
            _dumps(orb.get("indicators") or []),
            _dumps(orb.get("payload") or {}),
            orb.get("created_at") or (existing["created_at"] if existing else now),
            orb.get("opened_at") or "",
            orb.get("verified_at") or "",
            orb.get("outcome") or "",
            orb.get("accuracy"),
            orb.get("notes") or "",
        )
        with conn:
            if existing:
                conn.execute(
                    """UPDATE orbs SET title=?, prophecy=?, summary=?, status=?,
                       confidence=?, horizon_days=?, deadline=?, seer=?,
                       source_report_id=?, source_simulation_id=?, project_id=?,
                       tags=?, entities=?, branches=?, indicators=?, payload=?,
                       opened_at=?, verified_at=?, outcome=?, accuracy=?, notes=?
                       WHERE orb_id=?""",
                    values[1:17] + values[18:] + (orb_id,)
                )
            else:
                conn.execute(
                    """INSERT INTO orbs
                       (orb_id, title, prophecy, summary, status, confidence,
                        horizon_days, deadline, seer, source_report_id,
                        source_simulation_id, project_id, tags, entities,
                        branches, indicators, payload, created_at, opened_at,
                        verified_at, outcome, accuracy, notes)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    values
                )
        row = conn.execute("SELECT * FROM orbs WHERE orb_id=?", (orb_id,)).fetchone()
        return self._row_to_orb(row)

    def get_orb(self, orb_id: str, with_scryings: bool = True) -> Optional[Dict[str, Any]]:
        row = self._get_conn().execute(
            "SELECT * FROM orbs WHERE orb_id=?", (orb_id,)
        ).fetchone()
        if not row:
            return None
        scryings = self.list_scryings(orb_id) if with_scryings else None
        return self._row_to_orb(row, scryings)

    def get_orb_by_report(self, report_id: str) -> Optional[Dict[str, Any]]:
        row = self._get_conn().execute(
            "SELECT * FROM orbs WHERE source_report_id=? ORDER BY created_at DESC LIMIT 1",
            (report_id,)
        ).fetchone()
        return self._row_to_orb(row) if row else None

    def list_orbs(self, status: str = "", source_report_id: str = "",
                  limit: int = 200) -> List[Dict[str, Any]]:
        clauses = []
        params: List[Any] = []
        if status:
            clauses.append("status=?")
            params.append(status)
        if source_report_id:
            clauses.append("source_report_id=?")
            params.append(source_report_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._get_conn().execute(
            f"SELECT * FROM orbs{where} ORDER BY created_at DESC LIMIT ?",
            (*params, max(1, min(int(limit or 200), 500)))
        ).fetchall()
        return [self._row_to_orb(row) for row in rows]

    def delete_orb(self, orb_id: str) -> bool:
        conn = self._get_conn()
        row = conn.execute("SELECT orb_id FROM orbs WHERE orb_id=?", (orb_id,)).fetchone()
        if not row:
            return False
        with conn:
            conn.execute("DELETE FROM scryings WHERE orb_id=?", (orb_id,))
            conn.execute("DELETE FROM orbs WHERE orb_id=?", (orb_id,))
        return True

    # --------------------------------------------------------------- SCRYINGS
    @staticmethod
    def _row_to_scrying(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "scrying_id": row["scrying_id"],
            "orb_id": row["orb_id"],
            "question": row["question"],
            "answer": row["answer"],
            "created_at": row["created_at"],
        }

    def add_scrying(self, orb_id: str, question: str, answer: str) -> Dict[str, Any]:
        scrying_id = f"scry_{uuid.uuid4().hex[:16]}"
        conn = self._get_conn()
        with conn:
            conn.execute(
                """INSERT INTO scryings
                   (scrying_id, orb_id, question, answer, created_at)
                   VALUES (?,?,?,?,?)""",
                (scrying_id, orb_id, question, answer, _now())
            )
        row = conn.execute(
            "SELECT * FROM scryings WHERE scrying_id=?", (scrying_id,)
        ).fetchone()
        return self._row_to_scrying(row)

    def list_scryings(self, orb_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        rows = self._get_conn().execute(
            "SELECT * FROM scryings WHERE orb_id=? ORDER BY created_at ASC LIMIT ?",
            (orb_id, max(1, int(limit)))
        ).fetchall()
        return [self._row_to_scrying(row) for row in rows]

    # --------------------------------------------------------------- 统计
    def stats(self) -> Dict[str, Any]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT status, COUNT(*) AS c, AVG(accuracy) AS avg_accuracy "
            "FROM orbs GROUP BY status"
        ).fetchall()
        by_status = {row["status"]: row["c"] for row in rows}
        verified = conn.execute(
            "SELECT COUNT(*) AS c, AVG(accuracy) AS avg_accuracy "
            "FROM orbs WHERE accuracy IS NOT NULL"
        ).fetchone()
        total = conn.execute("SELECT COUNT(*) AS c FROM orbs").fetchone()["c"]
        return {
            "total": int(total),
            "by_status": by_status,
            "sealed": int(by_status.get("sealed", 0)),
            "opened": int(by_status.get("opened", 0)),
            "fulfilled": int(by_status.get("fulfilled", 0)),
            "partial": int(by_status.get("partial", 0)),
            "shattered": int(by_status.get("shattered", 0)),
            "verified": int(verified["c"] or 0),
            "average_accuracy": (round(float(verified["avg_accuracy"]), 1)
                                 if verified["avg_accuracy"] is not None else None),
        }
