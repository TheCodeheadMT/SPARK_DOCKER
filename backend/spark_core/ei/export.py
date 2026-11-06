"""Export EI plans and workbooks.

markdown_to_pdf_bytes() is ported unchanged from utils/markdown_pdf.py (ReportLab;
headings, paragraphs, bullet lists, fenced code, bold/italic), plus numbered lists.
markdown_to_html() is new: a self-contained, printable HTML page. Raw HTML in the
LLM output is NOT rendered (html=False), so a model reply cannot inject markup.
"""
from __future__ import annotations

import html as _html
import re
from io import BytesIO

from markdown_it import MarkdownIt
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import ListFlowable, ListItem, Paragraph, Preformatted, SimpleDocTemplate, Spacer

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)")
_CODE_RE = re.compile(r"`([^`]+)`")
_NUM_RE = re.compile(r"^\s*\d+[.)]\s+")


def _md_inline_to_rl(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = _BOLD_RE.sub(r"<b>\1</b>", text)
    text = _ITALIC_RE.sub(r"<i>\1</i>", text)
    return _CODE_RE.sub(r'<font face="Courier">\1</font>', text)


def markdown_to_pdf_bytes(md: str, title: str | None = None, subtitle: str | None = None, pagesize=letter) -> bytes:
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("H1", parent=styles["Heading1"], spaceAfter=10)
    h2 = ParagraphStyle("H2", parent=styles["Heading2"], spaceAfter=8)
    h3 = ParagraphStyle("H3", parent=styles["Heading3"], spaceAfter=6)
    body = ParagraphStyle("Body", parent=styles["BodyText"], leading=14, spaceAfter=6)
    small = ParagraphStyle("Small", parent=body, fontSize=8.5, textColor="#555555")
    code_style = ParagraphStyle("CodeBlock", parent=styles["BodyText"], fontName="Courier", fontSize=9.5, leading=11)

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=pagesize, leftMargin=0.9 * inch, rightMargin=0.9 * inch,
                            topMargin=0.8 * inch, bottomMargin=0.8 * inch, title=title or "Document")
    story: list = []
    if title:
        story.append(Paragraph(_md_inline_to_rl(title), h1))
    if subtitle:
        story.append(Paragraph(_md_inline_to_rl(subtitle), small))
    if title or subtitle:
        story.append(Spacer(1, 8))

    in_code, code_lines, para, bullets, numbered = False, [], [], [], []

    def flush_para():
        nonlocal para
        if para:
            text = " ".join(ln.strip() for ln in para).strip()
            if text:
                story.append(Paragraph(_md_inline_to_rl(text), body))
            para = []

    def flush_lists():
        nonlocal bullets, numbered
        for items, kind in ((bullets, "bullet"), (numbered, "1")):
            if items:
                story.append(ListFlowable([ListItem(Paragraph(_md_inline_to_rl(i), body)) for i in items],
                                          bulletType=kind, leftIndent=16))
                story.append(Spacer(1, 4))
        bullets, numbered = [], []

    for line in md.splitlines():
        if line.strip().startswith("```"):
            if in_code:
                in_code = False
                if code_lines:
                    story.append(Preformatted("\n".join(code_lines).rstrip("\n"), code_style))
                    story.append(Spacer(1, 6))
                code_lines = []
            else:
                flush_para()
                flush_lists()
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue
        if not line.strip():
            flush_para()
            flush_lists()
            continue
        stripped = line.lstrip("#")
        level = len(line) - len(stripped)
        if 1 <= level <= 6 and stripped.startswith(" "):
            flush_para()
            flush_lists()
            story.append(Paragraph(_md_inline_to_rl(stripped.strip()), {1: h1, 2: h2}.get(level, h3)))
            continue
        if line.strip() in ("---", "***", "___"):
            flush_para()
            flush_lists()
            story.append(Spacer(1, 6))
            continue
        ls = line.lstrip()
        if ls.startswith(("- ", "* ", "+ ")):
            flush_para()
            item = ls[2:].strip()
            item = re.sub(r"^\[( |x|X)\]\s*", lambda m: "☐ " if m.group(1) == " " else "☑ ", item)
            bullets.append(item)
            continue
        if _NUM_RE.match(line):
            flush_para()
            numbered.append(_NUM_RE.sub("", line).strip())
            continue
        flush_lists()
        para.append(line)
    flush_para()
    flush_lists()
    if in_code and code_lines:
        story.append(Preformatted("\n".join(code_lines), code_style))
    doc.build(story)
    return buf.getvalue()


_md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False}).enable(["table", "strikethrough"])

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{ font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; color: #1b2430; max-width: 780px;
         margin: 2rem auto; padding: 0 1.25rem; line-height: 1.55; }}
  header {{ border-bottom: 3px solid #0b2a4a; margin-bottom: 1.5rem; padding-bottom: .5rem; }}
  header h1 {{ margin: 0; color: #0b2a4a; }} header p {{ margin: .25rem 0 0; color: #6a7686; font-size: .9rem; }}
  h1, h2, h3 {{ color: #0b2a4a; line-height: 1.25; }} h2 {{ border-bottom: 1px solid #e2e7ee; padding-bottom: .2rem; }}
  pre {{ background: #f5f7fa; border: 1px solid #e2e7ee; border-radius: 6px; padding: .75rem; overflow-x: auto; }}
  code {{ font-family: ui-monospace, Menlo, Consolas, monospace; font-size: .9em; }}
  table {{ border-collapse: collapse; }} td, th {{ border: 1px solid #e2e7ee; padding: .3rem .6rem; }}
  blockquote {{ border-left: 4px solid #f5b72e; margin: 0; padding-left: 1rem; color: #3b4656; }}
  footer {{ margin-top: 2rem; color: #6a7686; font-size: .8rem; border-top: 1px solid #e2e7ee; padding-top: .5rem; }}
  @media print {{ body {{ margin: 0; }} a {{ color: inherit; }} }}
</style></head>
<body><header><h1>{title}</h1><p>{subtitle}</p></header>
<main>{body}</main>
<footer>Generated with SPARK. Review before sharing with the student.</footer>
</body></html>"""


def markdown_to_html(md: str, title: str, subtitle: str = "") -> str:
    return HTML_TEMPLATE.format(title=_html.escape(title), subtitle=_html.escape(subtitle), body=_md.render(md or ""))
