"""Small, safe Markdown renderer for operator-authored station copy."""
import html
import re
from markupsafe import Markup


_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^\s)]+)\)")
_SAFE_LINK_RE = re.compile(r"^(?:https?://|mailto:|/|#)", re.IGNORECASE)


def _inline(value):
    """Render a deliberately small inline Markdown subset with escaped HTML."""
    code_spans = []

    def store_code(match):
        code_spans.append(f"<code>{html.escape(match.group(1), quote=False)}</code>")
        return f"\x00CODE{len(code_spans) - 1}\x00"

    value = re.sub(r"`([^`\n]+)`", store_code, value)
    value = html.escape(value, quote=False)

    def link(match):
        label, target = match.groups()
        if not _SAFE_LINK_RE.match(html.unescape(target)):
            return match.group(0)
        return (
            f'<a href="{html.escape(html.unescape(target), quote=True)}" '
            f'rel="nofollow noopener">{label}</a>'
        )

    value = _LINK_RE.sub(link, value)
    value = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", value)
    value = re.sub(r"__(.+?)__", r"<strong>\1</strong>", value)
    value = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", value)
    value = re.sub(r"(?<!_)_([^_\n]+)_(?!_)", r"<em>\1</em>", value)
    for index, rendered in enumerate(code_spans):
        value = value.replace(f"\x00CODE{index}\x00", rendered)
    return value


def render_markdown(value):
    """Render safe headings, lists, quotes, code, links, and emphasis."""
    lines = str(value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    output = []
    paragraph = []
    list_kind = None
    in_code = False
    code_lines = []

    def flush_paragraph():
        if paragraph:
            output.append("<p>" + "<br>".join(_inline(line) for line in paragraph) + "</p>")
            paragraph.clear()

    def close_list():
        nonlocal list_kind
        if list_kind:
            output.append(f"</{list_kind}>")
            list_kind = None

    for line in lines:
        if line.strip().startswith("```"):
            flush_paragraph()
            close_list()
            if in_code:
                output.append("<pre><code>" + html.escape("\n".join(code_lines)) + "</code></pre>")
                code_lines.clear()
            in_code = not in_code
            continue
        if in_code:
            code_lines.append(line)
            continue
        if not line.strip():
            flush_paragraph()
            close_list()
            continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        item = re.match(r"^\s*([-*])\s+(.+)$", line)
        numbered = re.match(r"^\s*\d+[.)]\s+(.+)$", line)
        quote = re.match(r"^>\s?(.*)$", line)
        if heading:
            flush_paragraph()
            close_list()
            level = len(heading.group(1)) + 2
            output.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
        elif item or numbered:
            flush_paragraph()
            wanted = "ul" if item else "ol"
            if list_kind != wanted:
                close_list()
                output.append(f"<{wanted}>")
                list_kind = wanted
            output.append(f"<li>{_inline((item or numbered).group(2 if item else 1))}</li>")
        elif quote:
            flush_paragraph()
            close_list()
            output.append(f"<blockquote>{_inline(quote.group(1))}</blockquote>")
        else:
            close_list()
            paragraph.append(line)

    if in_code:
        output.append("<pre><code>" + html.escape("\n".join(code_lines)) + "</code></pre>")
    flush_paragraph()
    close_list()
    return Markup("\n".join(output))
