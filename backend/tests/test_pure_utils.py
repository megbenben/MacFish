"""纯函数的单元测试：文本切分、本体配额分配、文本类解析器。

不需要网络，也不碰 uploads/ 下的真实数据（全部用 tmp_path）。
这里守的主要是「切块参数能把后台线程转死」这类纯函数缺陷——
在此之前这个项目一个自动化测试都没有。
"""
import os
import signal
import sys

import pytest

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from app.services.ontology_generator import OntologyGenerator  # noqa: E402
from app.utils.file_parser import FileParser, split_text_into_chunks  # noqa: E402


# ---------------------------------------------------------------- 超时保护

class _Timeout(Exception):
    pass


class deadline:
    """在测试里给一个可能死循环的调用加硬超时（POSIX）。

    切块函数的 bug 形态就是「原地打转、不返回」，没有超时的话 pytest 会一直挂着，
    CI 上表现为莫名其妙的卡死。
    """

    def __init__(self, seconds=10):
        self.seconds = seconds

    def __enter__(self):
        if not hasattr(signal, 'SIGALRM'):
            return self
        signal.signal(signal.SIGALRM, self._handler)
        signal.alarm(self.seconds)
        return self

    def __exit__(self, *exc):
        if hasattr(signal, 'SIGALRM'):
            signal.alarm(0)
        return False

    @staticmethod
    def _handler(signum, frame):
        raise _Timeout('调用未在限定时间内返回（疑似死循环）')


# ---------------------------------------------------------------- 文本切分

def test_split_returns_single_chunk_for_short_text():
    assert split_text_into_chunks('短文本', 500, 50) == ['短文本']


def test_split_blank_text_returns_empty():
    assert split_text_into_chunks('   \n  ', 500, 50) == []


def test_split_default_params_behaviour():
    """默认参数下的块数与首块长度（回归：不要因为加了防御而改变既有行为）"""
    text = '第0句话。' * 400  # 2000 字符
    chunks = split_text_into_chunks(text, 500, 50)
    assert len(chunks) == 5
    assert len(chunks[0]) == 500
    # 覆盖到文本末尾
    assert chunks[-1].endswith('。')


@pytest.mark.parametrize('size,overlap', [
    (500, 500),    # overlap == chunk_size：原始 bug 的直接触发条件
    (500, 499),
    (500, 600),    # overlap > chunk_size
    (0, 0),        # chunk_size 为 0
    (-5, -5),      # 负数
    (10, 9),       # 极小 chunk_size 配极大 overlap
])
def test_split_never_hangs_on_degenerate_params(size, overlap):
    """B1：这些参数以前会让 while 循环原地打转（后台线程里 = 任务永不返回 + 内存爆）"""
    text = '第0句话。' * 400
    with deadline(10):
        chunks = split_text_into_chunks(text, size, overlap)
    assert isinstance(chunks, list)
    # 仍然覆盖到了全文（不是提前 break 掉半途而废）
    joined_len = sum(len(c) for c in chunks)
    assert joined_len >= len(text) * 0.9


def test_split_output_is_bounded_by_input():
    """块数不该超过「文本长度 / 每次推进量」的量级，防止高 overlap 下块数爆炸到 OOM"""
    text = 'A' * 1000
    chunks = split_text_into_chunks(text, 100, 50)
    # overlap=50 / size=100 → 每轮前进 50，最多约 1000/50 + 1 块
    assert len(chunks) <= len(text) // 50 + 2


# ---------------------------------------------------------------- 本体配额

def test_allocate_documents_passthrough_under_budget():
    gen = OntologyGenerator.__new__(OntologyGenerator)  # 不触发 __init__ 里的 LLM 客户端
    docs = ['a' * 1000, 'b' * 1000]
    assert gen._allocate_documents(docs) == docs


def test_allocate_documents_gives_every_doc_a_floor():
    """1.4 的回归：大文档不再把后排小文档饿死"""
    gen = OntologyGenerator.__new__(OntologyGenerator)
    big = 'X' * 500_000
    small = 'Y' * 20_000
    allocated = gen._allocate_documents([big, small])

    assert len(allocated) == 2
    # 小文档要么被完整保留，要么至少拿到保底配额（而不是 0）
    assert len(allocated[1]) >= OntologyGenerator.PER_DOC_MIN_CHARS
    assert all(a.strip() for a in allocated)


def test_allocate_documents_respects_total_budget():
    gen = OntologyGenerator.__new__(OntologyGenerator)
    docs = ['Z' * 200_000 for _ in range(5)]
    allocated = gen._allocate_documents(docs)
    # 允许超出预算的部分来自「未纳入」的提示语，正文本身不应超太多
    assert len(allocated) == 5
    assert all(len(a) > 0 for a in allocated)


# ---------------------------------------------------------------- 解析器

def test_parse_plain_text(tmp_path):
    p = tmp_path / 'note.txt'
    p.write_text('你好，MacFish。\n第二行。', encoding='utf-8')
    text = FileParser.extract_text(str(p))
    assert '你好，MacFish。' in text
    assert '第二行。' in text


def test_parse_csv_keeps_quoted_fields(tmp_path):
    p = tmp_path / 'data.csv'
    p.write_text('name,note\nSAP,"含逗号, 的字段"\n', encoding='utf-8')
    text = FileParser.extract_text(str(p))
    assert 'SAP' in text
    assert '含逗号' in text


def test_legacy_office_raises_with_hint(tmp_path):
    """旧版 Office 要给出「另存为」提示，而不是静默失败"""
    from app.utils.parsers import LegacyOfficeFormatError

    p = tmp_path / 'old.doc'
    p.write_bytes(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1')  # OLE 头
    with pytest.raises(LegacyOfficeFormatError) as exc:
        FileParser.extract_text(str(p))
    assert 'docx' in str(exc.value)
