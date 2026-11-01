"""报告导出（Markdown / HTML / DOCX）的离线测试。"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.report_agent import (  # noqa: E402
    Report, ReportOutline, ReportSection, ReportStatus,
)
from app.services import report_export  # noqa: E402


def _report():
    return Report(
        report_id='report_test',
        simulation_id='sim_test',
        graph_id='graph_test',
        simulation_requirement='测试需求',
        status=ReportStatus.COMPLETED,
        outline=ReportOutline(
            title='测试报告',
            summary='摘要',
            sections=[ReportSection(title='第一节', content='正文')],
        ),
        markdown_content='# 测试报告\n\n## 第一节\n\n关键结论 [S1]。\n\n<script>alert(1)</script>',
        completed_at='2026-01-01T00:00:00',
        citations=[{
            'citation_id': 'S1', 'source_id': 'twitter:post:1', 'kind': 'post',
            'platform': 'twitter', 'agent': 'Alice', 'round': 3,
            'content': '原始帖子内容',
        }],
    )


def test_markdown_includes_citation_appendix():
    text = report_export.to_markdown(_report())
    assert '## 证据与引用' in text
    assert '**[S1]**' in text
    assert '原始帖子内容' in text


def test_html_escapes_script_and_renders_citation():
    page = report_export.to_html(_report())
    assert '<script>alert(1)</script>' not in page
    assert '&lt;script&gt;' in page
    assert '<span class="cite">[S1]</span>' in page
    assert '测试报告' in page


def test_docx_is_a_valid_zip_package():
    blob = report_export.to_docx(_report())
    assert blob[:2] == b'PK'
    assert len(blob) > 1000
