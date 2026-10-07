"""测试级公共夹具。"""
import logging
import os
import sys

import pytest

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


@pytest.fixture(autouse=True, scope='session')
def _mute_app_file_loggers():
    """测试期间的日志不要写进应用正式的 logs/*.log。

    `get_logger()` 在 import 时就给每个 logger 挂上了 FileHandler，所以测试跑出来的
    行（例如「已从磁盘恢复 1 个任务」）会混进用户排查问题时要看的那份日志里，
    读起来像是应用真的干了这些事。这里在测试会话期间把文件处理器摘掉，结束时还原。
    """
    import app.utils.logger  # noqa: F401  确保应用 logger 已经建好

    detached = []

    def _detach(logger):
        for handler in list(logger.handlers):
            if isinstance(handler, logging.FileHandler):
                logger.removeHandler(handler)
                detached.append((logger, handler))

    targets = [logging.getLogger()]
    for name in list(logging.root.manager.loggerDict):
        targets.append(logging.getLogger(name))

    for logger in targets:
        _detach(logger)

    yield

    for logger, handler in detached:
        logger.addHandler(handler)
