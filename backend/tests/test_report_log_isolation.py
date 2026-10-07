"""报告控制台日志的并发隔离（B7）。

`mirofish.report_agent` / `mirofish.zep_tools` 是模块级共享 logger，而每份报告都会
往它们上面挂一个指向自己目录的 handler。logger 会把一条记录派发给**所有** handler，
于是两份报告并发时 A 的 console_log.txt 里会混进 B 的日志行 —— 而这个文件存在的
唯一理由就是排查「这份报告为什么失败」。这里守住「只有本线程的日志进本文件」。

不需要网络。
"""
import logging
import os
import sys
import threading

import pytest

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from app.config import Config  # noqa: E402
from app.services.report_agent import ReportConsoleLogger  # noqa: E402


@pytest.fixture()
def tmp_uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, 'UPLOAD_FOLDER', str(tmp_path))
    return tmp_path


def _read(path):
    if not os.path.exists(path):
        return ''
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def test_concurrent_reports_do_not_cross_contaminate(tmp_uploads):
    """两个线程各自建一份报告日志，互相不应看到对方的行"""
    a_logged = threading.Event()
    b_logged = threading.Event()
    errors = []

    def run(report_id, marker, mine, other_done):
        try:
            console = ReportConsoleLogger(report_id)
            try:
                logger = logging.getLogger('mirofish.report_agent')
                logger.info(f'ONLY-{marker}')
                mine.set()
                # 等对方也写完，制造「两份日志同时挂着 handler」的窗口
                other_done.wait(timeout=5)
                logger.info(f'ONLY-{marker}-2')
            finally:
                console.close()
        except Exception as exc:  # pragma: no cover - 出错时让断言看见
            errors.append(exc)

    t_a = threading.Thread(target=run, args=('report_aaa', 'A', a_logged, b_logged))
    t_b = threading.Thread(target=run, args=('report_bbb', 'B', b_logged, a_logged))
    t_a.start()
    t_b.start()
    t_a.join(timeout=15)
    t_b.join(timeout=15)

    assert not errors, errors

    text_a = _read(os.path.join(str(tmp_uploads), 'reports', 'report_aaa', 'console_log.txt'))
    text_b = _read(os.path.join(str(tmp_uploads), 'reports', 'report_bbb', 'console_log.txt'))

    assert 'ONLY-A' in text_a
    assert 'ONLY-B' not in text_a, f'A 的日志被 B 污染了:\n{text_a}'
    assert 'ONLY-B' in text_b
    assert 'ONLY-A' not in text_b, f'B 的日志被 A 污染了:\n{text_b}'


def test_handler_is_detached_on_close(tmp_uploads):
    logger = logging.getLogger('mirofish.report_agent')
    before = len(logger.handlers)

    console = ReportConsoleLogger('report_close')
    assert len(logger.handlers) == before + 1

    console.close()
    assert len(logger.handlers) == before


def test_close_is_idempotent(tmp_uploads):
    console = ReportConsoleLogger('report_twice')
    console.close()
    console.close()  # 不该抛
