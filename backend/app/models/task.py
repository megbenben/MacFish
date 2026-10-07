"""
任务状态管理
用于跟踪长时间运行的任务（如图谱构建）
"""

import os
import json
import uuid
import threading
from datetime import datetime
from enum import Enum
from typing import Dict, Any, Optional
from dataclasses import dataclass, field

from ..config import Config
from ..utils.locale import t
from ..utils.logger import get_logger

logger = get_logger('mirofish.task')


class TaskStatus(str, Enum):
    """任务状态枚举"""
    PENDING = "pending"          # 等待中
    PROCESSING = "processing"    # 处理中
    COMPLETED = "completed"      # 已完成
    FAILED = "failed"            # 失败


@dataclass
class Task:
    """任务数据类"""
    task_id: str
    task_type: str
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
    progress: int = 0              # 总进度百分比 0-100
    message: str = ""              # 状态消息
    result: Optional[Dict] = None  # 任务结果
    error: Optional[str] = None    # 错误信息
    metadata: Dict = field(default_factory=dict)  # 额外元数据
    progress_detail: Dict = field(default_factory=dict)  # 详细进度信息
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "progress": self.progress,
            "message": self.message,
            "progress_detail": self.progress_detail,
            "result": self.result,
            "error": self.error,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Task":
        """从落盘的字典还原（时间戳容错，坏值退回当前时间）"""
        def _parse_dt(value):
            try:
                return datetime.fromisoformat(value)
            except (TypeError, ValueError):
                return datetime.now()

        return cls(
            task_id=data["task_id"],
            task_type=data.get("task_type", ""),
            status=TaskStatus(data.get("status", "pending")),
            created_at=_parse_dt(data.get("created_at")),
            updated_at=_parse_dt(data.get("updated_at")),
            progress=int(data.get("progress") or 0),
            message=data.get("message") or "",
            result=data.get("result"),
            error=data.get("error"),
            metadata=data.get("metadata") or {},
            progress_detail=data.get("progress_detail") or {},
        )


class TaskManager:
    """
    任务管理器
    线程安全的任务状态管理
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        """单例模式"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._tasks: Dict[str, Task] = {}
                    cls._instance._task_lock = threading.Lock()
                    cls._instance._hydrated = False
        return cls._instance

    # ---- 落盘 ----
    # 任务表以前是纯内存的：后端一重启，所有 task_id 失效。而 /graph/task/{id}
    # 又没有像 /simulation/prepare/status、/report/generate/status 那样的
    # 「扫产物推断状态」补偿，于是图谱构建期间重启会让前端永远转圈。
    # 这里把任务写进 uploads/tasks/，重启后原样读回，比逐接口补推断更彻底。

    @property
    def _persist_dir(self) -> str:
        # 用属性而不是构造时算好：测试里 monkeypatch Config.UPLOAD_FOLDER 才能生效
        return os.path.join(Config.UPLOAD_FOLDER, 'tasks')

    def _task_file(self, task_id: str) -> str:
        return os.path.join(self._persist_dir, f'{task_id}.json')

    def _persist(self, task: Task) -> None:
        """把任务写到磁盘。失败只告警——落盘不该影响任务本身。"""
        try:
            os.makedirs(self._persist_dir, exist_ok=True)
            path = self._task_file(task.task_id)
            tmp = f'{path}.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(task.to_dict(), f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)  # 原子替换，避免读到写了一半的 JSON
        except Exception as e:
            logger.warning(f"任务落盘失败（不影响任务本身）: {task.task_id}, error={e}")

    def _hydrate(self) -> None:
        """首次访问时把磁盘上的任务读回内存（进程重启后 task_id 不再全部失效）"""
        with self._task_lock:
            if self._hydrated:
                return
            self._hydrated = True

            directory = self._persist_dir
            if not os.path.isdir(directory):
                return

            loaded = 0
            for name in os.listdir(directory):
                if not name.endswith('.json'):
                    continue
                try:
                    with open(os.path.join(directory, name), 'r', encoding='utf-8') as f:
                        task = Task.from_dict(json.load(f))
                except Exception as e:
                    logger.warning(f"任务文件读取失败，已跳过: {name}, error={e}")
                    continue
                self._tasks.setdefault(task.task_id, task)
                loaded += 1

            if loaded:
                logger.info(f"已从磁盘恢复 {loaded} 个任务")

    def create_task(self, task_type: str, metadata: Optional[Dict] = None) -> str:
        """
        创建新任务
        
        Args:
            task_type: 任务类型
            metadata: 额外元数据
            
        Returns:
            任务ID
        """
        task_id = str(uuid.uuid4())
        now = datetime.now()
        
        task = Task(
            task_id=task_id,
            task_type=task_type,
            status=TaskStatus.PENDING,
            created_at=now,
            updated_at=now,
            metadata=metadata or {}
        )
        
        with self._task_lock:
            self._tasks[task_id] = task

        self._persist(task)
        return task_id

    def get_task(self, task_id: str) -> Optional[Task]:
        """获取任务（内存未命中时回读磁盘，兼容进程重启）"""
        self._hydrate()
        with self._task_lock:
            return self._tasks.get(task_id)
    
    def update_task(
        self,
        task_id: str,
        status: Optional[TaskStatus] = None,
        progress: Optional[int] = None,
        message: Optional[str] = None,
        result: Optional[Dict] = None,
        error: Optional[str] = None,
        progress_detail: Optional[Dict] = None
    ):
        """
        更新任务状态
        
        Args:
            task_id: 任务ID
            status: 新状态
            progress: 进度
            message: 消息
            result: 结果
            error: 错误信息
            progress_detail: 详细进度信息
        """
        with self._task_lock:
            task = self._tasks.get(task_id)
            if task:
                task.updated_at = datetime.now()
                if status is not None:
                    task.status = status
                if progress is not None:
                    task.progress = progress
                if message is not None:
                    task.message = message
                if result is not None:
                    task.result = result
                if error is not None:
                    task.error = error
                if progress_detail is not None:
                    task.progress_detail = progress_detail

        # 落盘放在锁外：文件 IO 不该占着任务锁
        if task:
            self._persist(task)

    def complete_task(self, task_id: str, result: Dict):
        """标记任务完成"""
        self.update_task(
            task_id,
            status=TaskStatus.COMPLETED,
            progress=100,
            message=t('progress.taskComplete'),
            result=result
        )
    
    def fail_task(self, task_id: str, error: str):
        """标记任务失败"""
        self.update_task(
            task_id,
            status=TaskStatus.FAILED,
            message=t('progress.taskFailed'),
            error=error
        )
    
    def list_tasks(self, task_type: Optional[str] = None) -> list:
        """列出任务"""
        self._hydrate()
        with self._task_lock:
            tasks = list(self._tasks.values())
            if task_type:
                tasks = [t for t in tasks if t.task_type == task_type]
            return [t.to_dict() for t in sorted(tasks, key=lambda x: x.created_at, reverse=True)]

    def cleanup_old_tasks(self, max_age_hours: int = 24):
        """清理旧任务（内存与磁盘一起清）"""
        from datetime import timedelta
        cutoff = datetime.now() - timedelta(hours=max_age_hours)

        self._hydrate()
        with self._task_lock:
            old_ids = [
                tid for tid, task in self._tasks.items()
                if task.created_at < cutoff and task.status in [TaskStatus.COMPLETED, TaskStatus.FAILED]
            ]
            for tid in old_ids:
                del self._tasks[tid]

        for tid in old_ids:
            try:
                os.remove(self._task_file(tid))
            except FileNotFoundError:
                pass
            except Exception as e:
                logger.warning(f"清理任务文件失败: {tid}, error={e}")

