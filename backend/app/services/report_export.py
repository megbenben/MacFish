"""
报告导出
========

同一个报告三种载体：

- ``markdown``：原文 + 引用附录，最忠实
- ``html``：自包含样式，浏览器可直接打印成 PDF
- ``docx``：python-docx 已在依赖里，不额外引入重量级 PDF 引擎

正文只做**最小 Markdown 解析**（标题/列表/引用/粗体/行内代码），
因为报告本来就是这套受限语法生成的；不追求完整 CommonMark。
"""

from __future__ import annotations

import html as html_lib
import io
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..utils.logger import get_logger

logger = get_logger('mirofish.report_export')


def _citation_appendix(citations: Iterable[Dict[str, Any]]) -> str:
    items = list(citations or [])
    if not items:
        return ''
    lines = ['', '---', '', '## 证据与引用', '']
    for item in items:
        meta = ' · '.join(filter(None, [
            str(item.get('kind') or ''),
            str(item.get('platform') or ''),
            str(item.get('agent') or ''),
            f"round {item['round']}" if item.get('round') is not None else '',
        ]))
        lines.append(f"**[{item.get('citation_id')}]** {meta}")
        lines.append('')
        lines.append(str(item.get('content') or ''))
        lines.append('')
    return '\n'.join(lines)


def to_markdown(report: Any) -> str:
    body = getattr(report, 'markdown_content', '') or ''
    return body + _citation_appendix(getattr(report, 'citations', []) or [])


def _inline_html(text: str) -> str:
    text = html_lib.escape(text)
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<em>\1</em>', text)
    return text


def _iter_blocks(markdown: str) -> Iterable[Tuple[str, str]]:
    """把 Markdown 拆成 (kind, text)。kind ∈ heading1..4 / quote / bullet / number / hr / para。"""
    paragraph: List[str] = []

    def flush() -> Iterable[Tuple[str, str]]:
        nonlocal paragraph
        if paragraph:
            yield 'para', ' '.join(paragraph).strip()
            paragraph = []

    for raw in (markdown or '').splitlines():
        line = raw.rstrip()
        if not line.strip():
            yield from flush()
            continue
        match = re.match(r'^(#{1,4})\s+(.*)$', line)
        if match:
            yield from flush()
            yield f'heading{len(match.group(1))}', match.group(2).strip()
            continue
        if re.match(r'^-{3,}$', line.strip()):
            yield from flush()
            yield 'hr', ''
            continue
        if line.lstrip().startswith('>'):
            yield from flush()
            yield 'quote', line.lstrip()[1:].strip()
            continue
        bullet = re.match(r'^\s*[-*]\s+(.*)$', line)
        if bullet:
            yield from flush()
            yield 'bullet', bullet.group(1).strip()
            continue
        numbered = re.match(r'^\s*\d+[.)]\s+(.*)$', line)
        if numbered:
            yield from flush()
            yield 'number', numbered.group(1).strip()
            continue
        paragraph.append(line.strip())
    yield from flush()


_HTML_CSS = """
body{max-width:820px;margin:48px auto;padding:0 24px;color:#111827;
font:16px/1.75 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans SC",sans-serif}
h1{font-size:30px;line-height:1.3;margin:0 0 8px}
h2{font-size:22px;margin:36px 0 12px;border-bottom:1px solid #E5E7EB;padding-bottom:6px}
h3{font-size:18px;margin:28px 0 10px}
h4{font-size:16px;margin:22px 0 8px}
blockquote{border-left:3px solid #D1D5DB;margin:16px 0;padding:8px 16px;color:#4B5563;background:#F9FAFB}
code{background:#F3F4F6;padding:1px 5px;border-radius:3px;font-size:13px}
li{margin:4px 0}
hr{border:0;border-top:1px solid #E5E7EB;margin:28px 0}
.cite{color:#C2410C;font-weight:600;font-size:12px}
.meta{color:#6B7280;font-size:13px;margin-bottom:28px}
@media print{body{margin:0;max-width:none}}
"""


def to_html(report: Any) -> str:
    markdown = to_markdown(report)
    title = ''
    outline = getattr(report, 'outline', None)
    if outline is not None:
        title = getattr(outline, 'title', '') or ''
    body: List[str] = []
    for kind, text in _iter_blocks(markdown):
        if kind == 'hr':
            body.append('<hr>')
        elif kind.startswith('heading'):
            level = int(kind[-1])
            body.append(f'<h{level}>{_inline_html(text)}</h{level}>')
        elif kind == 'quote':
            body.append(f'<blockquote>{_inline_html(text)}</blockquote>')
        elif kind == 'bullet':
            body.append(f'<li>{_inline_html(text)}</li>')
        elif kind == 'number':
            body.append(f'<li>{_inline_html(text)}</li>')
        elif kind == 'para':
            # 把 S1 / [S1] 标记成可读的小标签
            rendered = _inline_html(text)
            rendered = re.sub(r'\[?(S\d{1,3})\]?', r'<span class="cite">[\1]</span>', rendered)
            body.append(f'<p>{rendered}</p>')
    # 列表项连续出现时包一层 <ul>
    html_body = '\n'.join(body)
    html_body = re.sub(r'(<li>.*?</li>)(?:\s*(<li>.*?</li>))*', lambda m: f'<ul>{m.group(0)}</ul>', html_body)
    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html_lib.escape(title or getattr(report, 'report_id', 'MacFish Report'))}</title>
<style>{_HTML_CSS}</style>
</head>
<body>
<header>
<h1>{html_lib.escape(title or 'MacFish 预测报告')}</h1>
<p class="meta">{html_lib.escape(str(getattr(report, 'simulation_id', '')))} · {html_lib.escape(str(getattr(report, 'completed_at', '')))}</p>
</header>
{html_body}
</body>
</html>"""


def to_docx(report: Any) -> bytes:
    """生成 .docx 字节流。python-docx 是既有依赖，不需要新增安装。"""
    from docx import Document
    from docx.shared import Pt

    document = Document()
    document.styles['Normal'].font.size = Pt(11)

    outline = getattr(report, 'outline', None)
    title = getattr(outline, 'title', '') if outline is not None else ''
    document.add_heading(title or 'MacFish 预测报告', level=0)
    document.add_paragraph(
        f"{getattr(report, 'simulation_id', '')} · {getattr(report, 'completed_at', '')}"
    )

    for kind, text in _iter_blocks(to_markdown(report)):
        if kind == 'hr':
            document.add_paragraph('—' * 20)
        elif kind.startswith('heading'):
            level = min(4, int(kind[-1]))
            document.add_heading(text, level=level)
        elif kind == 'quote':
            paragraph = document.add_paragraph(text)
            paragraph.style = document.styles['Intense Quote']
        elif kind in ('bullet', 'number'):
            document.add_paragraph(text, style='List Bullet')
        elif kind == 'para':
            document.add_paragraph(text)

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
