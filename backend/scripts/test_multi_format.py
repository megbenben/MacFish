"""
多格式解析自检脚本
----------------
程序化生成各类测试文件，逐个走 FileParser，断言关键结构出现在输出里。

用法（在 backend 目录下）：
    uv run python scripts/test_multi_format.py
    uv run python scripts/test_multi_format.py --keep   # 保留生成的临时文件

不联网、不调用 LLM。需要 OCR 的用例（图片、扫描页 PDF）会在引擎不可用时跳过。
"""

import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.utils.file_parser import FileParser  # noqa: E402
from app.utils.parsers import ExtractionError, LegacyOfficeFormatError  # noqa: E402

PASS = 'PASS'
FAIL = 'FAIL'
SKIP = 'SKIP'

_results = []


def check(name: str, path: str, must_contain, generate) -> None:
    """生成文件 → 解析 → 断言。must_contain 里的每一项都必须出现在输出中。"""
    try:
        generate(path)
    except Exception as exc:
        _results.append((SKIP, name, f'生成测试文件失败: {exc}'))
        return

    try:
        result = FileParser.extract_text_detailed(path)
    except Exception as exc:
        _results.append((FAIL, name, f'解析异常: {type(exc).__name__}: {exc}'))
        return

    missing = [item for item in must_contain if item not in result.text]
    if missing:
        preview = result.text[:400].replace('\n', '\\n')
        _results.append((FAIL, name, f'缺少 {missing}；输出预览: {preview}'))
        return

    notes = f'notes={result.notes}' if result.notes else ''
    _results.append((PASS, name, f'{len(result.text)} 字符 {notes}'))


# ---------------- 各格式的生成与断言 ----------------

def make_docx(path: str) -> None:
    from docx import Document

    doc = Document()
    doc.add_heading('电动车价格战分析报告', level=1)
    doc.add_heading('关键结论', level=2)
    doc.add_paragraph('比亚迪宣布全系降价 12%，特斯拉跟进。')
    doc.add_paragraph('渠道库存高企', style='List Bullet')
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = '品牌'
    table.cell(0, 1).text = '降幅'
    table.cell(1, 0).text = '比亚迪'
    table.cell(1, 1).text = '12%'
    doc.save(path)


def make_pptx(path: str) -> None:
    from pptx import Presentation

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = '2026 价格战'
    slide.placeholders[1].text = '推演提纲'

    slide2 = prs.slides.add_slide(prs.slide_layouts[1])
    slide2.shapes.title.text = '关键结论'
    slide2.placeholders[1].text = '特斯拉会跟进\n二线品牌承压'
    slide2.notes_slide.notes_text_frame.text = '备注：需核实口径'
    prs.save(path)


def make_xlsx(path: str) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = '销量'
    ws.append(['月份', '线上', '线下'])
    ws.append(['2026-07', 12000, 8300])
    ws.append(['2026-08', 13500, 7900])

    ws2 = wb.create_sheet('门店')
    ws2.append(['城市', '门店数'])
    ws2.append(['上海', 42])
    wb.save(path)


def make_csv(path: str) -> None:
    Path(path).write_text('品牌,降幅,备注\n比亚迪,12%,"含税, 不含补贴"\n特斯拉,8%,\n', encoding='utf-8')


def make_txt(path: str) -> None:
    Path(path).write_text('纯文本种子材料。\n第二行。\n', encoding='utf-8')


def make_image(path: str) -> None:
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new('RGB', (1000, 160), 'white')
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial Unicode.ttf', 40)
    draw.text((20, 30), '电动车价格战分析报告 2026', font=font, fill='black')
    draw.text((20, 95), 'Revenue grew 23.5% YoY.', font=font, fill='black')
    image.save(path)


def make_scanned_pdf(path: str) -> None:
    """把一张带文字的图放进 PDF，制造没有文字层的扫描件。"""
    import fitz
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new('RGB', (1000, 160), 'white')
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial Unicode.ttf', 40)
    draw.text((20, 40), '扫描件正文 比亚迪降价', font=font, fill='black')
    image_bytes = Path(path).with_suffix('.png')
    image.save(image_bytes)

    pdf = fitz.open()
    page = pdf.new_page(width=750, height=120)
    page.insert_image(fitz.Rect(0, 0, 750, 120), filename=str(image_bytes))
    pdf.save(path)
    pdf.close()


def make_text_pdf(path: str) -> None:
    import fitz

    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 100), 'Text layer page: EV price war analysis 2026.')
    pdf.save(path)
    pdf.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--keep', action='store_true', help='保留生成的临时文件')
    args = parser.parse_args()

    tmp_dir = tempfile.mkdtemp(prefix='macfish-formats-')
    base = Path(tmp_dir)
    print(f'临时目录: {tmp_dir}\n')

    check('docx 标题/表格', str(base / 't.docx'), ['# 电动车价格战分析报告', '## 关键结论', '| 品牌 | 降幅 |'], make_docx)
    check('pptx 页/备注', str(base / 't.pptx'), ['## Slide 2: 关键结论', '- 二线品牌承压', '### 备注'], make_pptx)
    check('xlsx 多表', str(base / 't.xlsx'), ['## 工作表: 销量', '| 月份 | 线上 | 线下 |', '## 工作表: 门店'], make_xlsx)
    check('csv 引号字段', str(base / 't.csv'), ['| 品牌 | 降幅 | 备注 |', '含税, 不含补贴'], make_csv)
    check('txt 原样', str(base / 't.txt'), ['纯文本种子材料。'], make_txt)
    check('pdf 文字层', str(base / 'text.pdf'), ['## 第 1 页', 'EV price war analysis'], make_text_pdf)
    # 只断言稳定可得的片段：OCR 对个别字形（如 战→成）可能识别偏差，不应据此判失败
    check('图片 OCR', str(base / 't.png'), ['电动车价格', 'YoY'], make_image)
    check('扫描版 PDF OCR', str(base / 'scanned.pdf'), ['比亚迪降价'], make_scanned_pdf)

    _check_legacy(base)

    # ---- 汇总 ----
    for status, name, detail in _results:
        mark = {PASS: '✓', FAIL: '✗', SKIP: '–'}[status]
        print(f'{mark} [{status}] {name}: {detail}')

    failed = sum(1 for s, _, _ in _results if s == FAIL)
    skipped = sum(1 for s, _, _ in _results if s == SKIP)
    print(f'\n通过 {sum(1 for s, _, _ in _results if s == PASS)} / {len(_results)}'
          f'，失败 {failed}，跳过 {skipped}')

    if args.keep:
        print(f'临时文件保留在: {tmp_dir}')

    return 1 if failed else 0


def _check_legacy(base: Path) -> None:
    """旧版 Office 必须给出可操作的「另存为」提示。"""
    source = base / 't.docx'
    if not source.exists():
        _results.append((SKIP, 'legacy 提示', 'docx 样本未生成'))
        return

    for ext, target in (('.doc', '.docx'), ('.ppt', '.pptx'), ('.xls', '.xlsx')):
        legacy_path = base / f'legacy{ext}'
        legacy_path.write_bytes(source.read_bytes())
        try:
            FileParser.extract_text(str(legacy_path))
        except LegacyOfficeFormatError as exc:
            if target in str(exc):
                _results.append((PASS, f'legacy 提示 {ext}', str(exc)[:60] + '…'))
            else:
                _results.append((FAIL, f'legacy 提示 {ext}', f'提示里没提到 {target}: {exc}'))
        except Exception as exc:
            _results.append((FAIL, f'legacy 提示 {ext}', f'抛出了 {type(exc).__name__}: {exc}'))
        else:
            _results.append((FAIL, f'legacy 提示 {ext}', '居然解析成功了，本应报错'))


if __name__ == '__main__':
    raise SystemExit(main())
