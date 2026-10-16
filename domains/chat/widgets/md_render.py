"""轻量 Markdown → Qt 富文本 HTML 渲染器（为 QTextBrowser 裁剪，避免使用 <div flex>/<pre>/<blockquote>）。"""
from __future__ import annotations
import re
from html import escape as _htmlesc

_CODE_BLOCK_RE = re.compile(r"```([a-zA-Z0-9_+\-]*)\n(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"(?<!`)`([^`\n]+?)`(?!`)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_HR_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$", re.MULTILINE)
_BLOCKQUOTE_RE = re.compile(r"((?:^>\s?.*(?:\n|$))+)", re.MULTILINE)
_UNORDERED_RE = re.compile(r"((?:^\s*[-*+]\s+.*(?:\n|$))+)", re.MULTILINE)
_ORDERED_RE = re.compile(r"((?:^\s*\d+\.\s+.*(?:\n|$))+)", re.MULTILINE)
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)\"']+)\)")
_BOLD_RE = re.compile(r"(\*\*|__)(?=\S)(.+?[*_]*)(?<=\S)\1")
_ITALIC_RE = re.compile(r"(?<![\*_])([\*_])(?=\S)(.+?)(?<=\S)\1(?![\*_])")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\((https?://[^\s)\"']+)\)")

_CODE_SENT = "\uE000CB\uE001"
_INLINE_SENT = "\uE000IC\uE001"

def _cs(idx): return f"{_CODE_SENT}:{idx}:"
def _isidx(idx): return f"{_INLINE_SENT}:{idx}:"

_RE_CS = re.compile(re.escape(_CODE_SENT) + r":(\d+):\s*")
_RE_IS = re.compile(re.escape(_INLINE_SENT) + r":(\d+):")


def _palette(skin):
    b = skin == "breeze"
    return dict(
        code_bg="#f1f5f9" if b else "#0a0f20",
        code_fg="#0f172a" if b else "#c9f6ff",
        header_bg="#e2e8f0" if b else "#131b34",
        tag_bg="#cbd5e1" if b else "#3b2a66",
        tag_fg="#1e293b" if b else "#e0c8ff",
        copy_color="#2563eb" if b else "#7afcff",
        body_color="#0f172a" if b else "#e8f9ff",
        link_color="#2563eb" if b else "#7ec0ff",
        border_c="#cbd5e1" if b else "#2a3556",
        bq_color="#d97706" if b else "#ffd166",
        bq_txt="#78350f" if b else "#e6e1c2",
        hr_c="#cbd5e1" if b else "#3a4670",
        heading_c="#1e40af" if b else "#7afcff",
        ic_bg="#eef2ff" if b else "#131b34",
        ic_fg="#4338ca" if b else "#ffd166",
    )


def md_to_html(text, code_dark=True, skin="cyberpunk"):
    if not text: return ""
    code_blocks = []
    def _rep_code(m):
        code_blocks.append((m.group(1).strip(), m.group(2).rstrip("\n")))
        return _cs(len(code_blocks) - 1) + "\n"
    t = _CODE_BLOCK_RE.sub(_rep_code, text)
    paragraphs = re.split(r"\n{2,}", t)
    p = _palette(skin)
    parts = []
    for para in paragraphs:
        para = para.rstrip("\n")
        if not para.strip(): continue
        mm = _RE_CS.fullmatch(para)
        if mm:
            idx = int(mm.group(1))
            lang, raw = code_blocks[idx]
            parts.append(_render_code_block(idx, lang or "code", raw, p))
            continue
        ln_out = []
        for ln in para.splitlines():
            if _HR_RE.fullmatch(ln):
                ln_out.append(f'<p style="margin:10px 0;"><hr style="border-top:1px dashed {p["hr_c"]};"/></p>')
                continue
            mh = _HEADING_RE.match(ln)
            if mh:
                lv = min(len(mh.group(1)), 6)
                title = mh.group(2)
                sizes = {1:22,2:19,3:17,4:15,5:14,6:13}
                ln_out.append(
                    f'<p style="margin:10px 0 6px 0;"><span style="font-size:{sizes[lv]}px;'
                    f'font-weight:900;color:{p["heading_c"]};">{_htmlesc(title)}</span></p>'
                )
                continue
            ln_out.append(ln)
        para = "\n".join(ln_out)

        # blockquote → 带背景表格
        def _bq(mb):
            inner = re.sub(r"^>\s?", "", mb.group(0), flags=re.MULTILINE).rstrip()
            inner_html = _inline(inner, p)
            return (
                f'<table border="0" cellspacing="0" cellpadding="0" style="margin:6px 0;">'
                f'<tr><td width="4" bgcolor="{p["bq_color"]}"></td>'
                f'<td width="8"></td>'
                f'<td style="background:{p["bq_color"] if False else "transparent"};">'
                f'<p style="margin:0;color:{p["bq_txt"]};background:{p["bq_color"][:7] if False else "rgba(0,0,0,0)"};">'
                f'<span style="color:{p["bq_txt"]};">{inner_html}</span></p></td></tr></table>'
            )
        para = _BLOCKQUOTE_RE.sub(_bq, para)

        def _ul(m):
            items = re.sub(r"^\s*[-*+]\s+", "", m.group(0), flags=re.MULTILINE).splitlines()
            rows = "".join(
                f'<tr><td valign="top" style="color:{p["body_color"]};">•</td>'
                f'<td width="6"></td><td valign="top"><p style="margin:2px 0;">{_inline(it, p)}</p></td></tr>'
                for it in items if it.strip()
            )
            return f'<table border="0" cellspacing="0" cellpadding="0" style="margin:4px 0 4px 2px;">{rows}</table>'
        para = _UNORDERED_RE.sub(_ul, para)

        def _ol(m):
            items = re.sub(r"^\s*\d+\.\s+", "", m.group(0), flags=re.MULTILINE).splitlines()
            rows = "".join(
                f'<tr><td valign="top" style="color:{p["body_color"]};">{i}.</td>'
                f'<td width="6"></td><td valign="top"><p style="margin:2px 0;">{_inline(it, p)}</p></td></tr>'
                for i, it in enumerate(items, 1) if it.strip()
            )
            return f'<table border="0" cellspacing="0" cellpadding="0" style="margin:4px 0 4px 2px;">{rows}</table>'
        para = _ORDERED_RE.sub(_ol, para)

        # 剩下的段内 inline
        para = _inline(para, p)
        parts.append(f'<p style="margin:4px 0;white-space:pre-wrap;">{para}</p>')

    body = "\n".join(parts)
    font_fam = (
        '"PingFang SC","Microsoft YaHei",-apple-system,BlinkMacSystemFont,'
        'Segoe UI,Roboto,sans-serif'
    )
    return (
        f'<body style="color:{p["body_color"]};font-family:{font_fam};line-height:1.65em;">'
        f"{body}</body>"
    )


def _render_code_block(idx, lang, raw, p):
    # 语言标签 + 复制按钮 + 代码行（每行<p white-space:pre>保证保留缩进换行）
    lang_html = (
        f'<span style="background:{p["tag_bg"]};color:{p["tag_fg"]};'
        f'padding:1px 8px;border-radius:10px;font-size:11px;font-weight:700;">{_htmlesc(lang)}</span>'
    )
    copy_html = (
        f'<a href="copy-code-{idx}" style="color:{p["copy_color"]};text-decoration:none;'
        f'font-size:11px;font-weight:700;">[ 📋 复制代码 ]</a>'
    )
    header_row = (
        f'<tr><td colspan="2" style="background:{p["header_bg"]};">'
        f'<table border="0" width="100%"><tr><td>{lang_html}</td>'
        f'<td align="right">{copy_html}</td></tr></table></td></tr>'
    )
    # 代码行
    lines = raw.split("\n")
    cell_rows = []
    for ln in lines:
        esc = _htmlesc(ln).replace(" ", "&nbsp;")
        cell_rows.append(
            f'<tr><td valign="top" style="color:{p["code_fg"][:7] if True else "#000"};'
            f'font-size:11px;padding:1px 4px;">&nbsp;</td>'
            f'<td valign="top" nowrap="nowrap"><p style="margin:0;white-space:pre;">'
            f'<span style="color:{p["code_fg"]};font-family:ui-monospace,SF Mono,Menlo,Consolas,monospace;'
            f'font-size:13px;">{esc or "&nbsp;"}</span></p></td></tr>'
        )
    body_rows = "".join(cell_rows)
    style = (
        f'border:1px solid {p["border_c"]};border-collapse:separate;border-radius:10px;'
        f'overflow:hidden;margin:8px 0;'
    )
    # 代码区整体背景
    body_bg_row = (
        f'<tr><td colspan="2" style="background:{p["code_bg"]};">'
        f'<table border="0" cellspacing="0" cellpadding="0" width="100%">{body_rows}</table></td></tr>'
    )
    return (
        f'<table border="0" cellspacing="0" cellpadding="0" style="{style}">'
        f'{header_row}{body_bg_row}</table>'
    )


def _inline(s, p):
    if not s: return ""
    inlines = []
    def _ic(m):
        inlines.append(m.group(1))
        return _isidx(len(inlines) - 1)
    s = _INLINE_CODE_RE.sub(_ic, s)

    def _img(m):
        alt = _htmlesc(m.group(1))
        return f'<img src="{m.group(2)}" alt="{alt}" style="max-width:100%;"/>'
    s = _IMAGE_RE.sub(_img, s)

    def _link(m):
        return (f'<a href="{m.group(2)}" style="color:{p["link_color"]};'
                f'text-decoration:underline;">{m.group(1)}</a>')
    s = _LINK_RE.sub(_link, s)

    def _b(m):
        return f"<b>{m.group(2)}</b>"
    s = _BOLD_RE.sub(_b, s)

    def _i(m):
        return f"<i>{m.group(2)}</i>"
    s = _ITALIC_RE.sub(_i, s)

    def _restore(m2):
        code = _htmlesc(inlines[int(m2.group(1))])
        return (f'<code style="background:{p["ic_bg"]};color:{p["ic_fg"]};padding:1px 6px;'
                f'border-radius:5px;font-family:ui-monospace,Menlo,Consolas,monospace;'
                f'font-size:12px;margin:0 1px;">{code}</code>')
    s = _RE_IS.sub(_restore, s)
    return s


def extract_copy_code_idx(url):
    m = re.fullmatch(r"copy-code-(\d+)", url or "")
    return int(m.group(1)) if m else None
