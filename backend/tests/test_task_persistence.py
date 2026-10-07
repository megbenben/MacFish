"""任务表的落盘与恢复（A4）。

任务表以前是纯内存的：后端一重启，所有 task_id 失效，而 /graph/task/{id} 又没有
「扫产物推断」的补偿，于是图谱构建期间重启会让前端永远转圈。这里守住落盘语义。

不需要网络。
"""
import json
import os
import sys

import pytest

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from app.config import Config  # noqa: E402
from app.models.task import Task, TaskManager, TaskStatus  # noqa: E402


@pytest.fixture()
def manager(tmp_path, monkeypatch):
    """干净的 TaskManager + 临时落盘目录"""
    monkeypatch.setattr(Config, 'UPLOAD_FOLDER', str(tmp_path))
    tm = TaskManager()
    tm._tasks.clear()
    tm._hydrated = False
    yield tm
    tm._tasks.clear()
    tm._hydrated = False


def test_task_is_written_to_disk(manager, tmp_path):
    task_id = manager.create_task('构建图谱: demo')
    path = os.path.join(str(tmp_path), 'tasks', f'{task_id}.json')

    assert os.path.exists(path)
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    assert data['task_id'] == task_id
    assert data['task_type'] == '构建图谱: demo'
    assert data['status'] == 'pending'


def test_updates_are_persisted(manager, tmp_path):
    task_id = manager.create_task('t')
    manager.update_task(task_id, progress=42, message='一半了')

    path = os.path.join(str(tmp_path), 'tasks', f'{task_id}.json')
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    assert data['progress'] == 42
    assert data['message'] == '一半了'


def test_survives_a_restart(manager):
    """模拟进程重启：清空内存 + 重置 hydrated 标记，任务应能从磁盘读回"""
    task_id = manager.create_task('构建图谱: restart')
    manager.update_task(task_id, progress=60, message='构建中')
    manager.complete_task(task_id, result={'graph_id': 'g1'})

    # "重启"
    manager._tasks.clear()
    manager._hydrated = False

    task = manager.get_task(task_id)
    assert task is not None
    assert task.status == TaskStatus.COMPLETED
    assert task.progress == 100
    assert task.result == {'graph_id': 'g1'}


def test_list_tasks_after_restart(manager):
    a = manager.create_task('type_a')
    b = manager.create_task('type_b')
    manager._tasks.clear()
    manager._hydrated = False

    all_tasks = manager.list_tasks()
    assert {t['task_id'] for t in all_tasks} == {a, b}

    only_a = manager.list_tasks(task_type='type_a')
    assert [t['task_id'] for t in only_a] == [a]


def test_unknown_task_returns_none(manager):
    assert manager.get_task('does-not-exist') is None


def test_corrupt_task_file_is_skipped(manager, tmp_path):
    good = manager.create_task('ok')
    bad_dir = os.path.join(str(tmp_path), 'tasks')
    with open(os.path.join(bad_dir, 'broken.json'), 'w', encoding='utf-8') as f:
        f.write('{ not json')

    manager._tasks.clear()
    manager._hydrated = False

    assert manager.get_task(good) is not None
    assert manager.get_task('broken') is None  # 坏文件不该把整个恢复流程带崩


def test_cleanup_removes_memory_and_files(manager, tmp_path):
    task_id = manager.create_task('old')
    manager.complete_task(task_id, result={})
    # 手动把创建时间挪到过去
    from datetime import datetime, timedelta
    manager._tasks[task_id].created_at = datetime.now() - timedelta(hours=48)
    manager._persist(manager._tasks[task_id])

    manager.cleanup_old_tasks(max_age_hours=24)

    assert manager.get_task(task_id) is None
    assert not os.path.exists(os.path.join(str(tmp_path), 'tasks', f'{task_id}.json'))


def test_cleanup_keeps_running_tasks(manager):
    """还在跑的任务不能被清理掉，否则前端立刻失去进度"""
    task_id = manager.create_task('running')
    manager.update_task(task_id, status=TaskStatus.PROCESSING)

    from datetime import datetime, timedelta
    manager._tasks[task_id].created_at = datetime.now() - timedelta(hours=48)

    manager.cleanup_old_tasks(max_age_hours=24)
    assert manager.get_task(task_id) is not None


def test_task_from_dict_tolerates_bad_timestamps():
    task = Task.from_dict({
        'task_id': 'x',
        'task_type': 't',
        'status': 'completed',
        'created_at': 'not-a-date',
        'updated_at': None,
    })
    assert task.task_id == 'x'
    assert task.status == TaskStatus.COMPLETED
    assert task.created_at is not None
