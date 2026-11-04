import hashlib
import re

from bs4 import BeautifulSoup, Comment, Tag

from app.core.errors import AppError

PARSER_VERSION = "sec-html-v1"
MAX_CHUNK_CHARS = 2200
BLOCK_TAGS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "pre"}
ITEM_HEADING = re.compile(r"^(?:PART\s+[IVX]+\b|ITEM\s+\d{1,2}(?:\.\d{1,2}|[A-Z])?\b)", re.I)


def clean_space(value):
    return re.sub(r"[\t\r\n\u00a0 ]+", " ", value).strip()


def pieces(text, maximum=MAX_CHUNK_CHARS):
    """Split very large blocks without dropping content or using artificial overlaps."""
    while len(text) > maximum:
        split = text.rfind(" ", maximum // 2, maximum)
        if split < 0:
            split = maximum
        yield text[:split].strip()
        text = text[split:].lstrip()
    if text:
        yield text


def visible_blocks(parent):
    """Retain mixed inline text surrounding nested blocks without duplicating it."""
    pending = []
    for child in parent.children:
        if not isinstance(child, Tag):
            pending.append(str(child))
            continue
        child_blocks = child.name in BLOCK_TAGS or child.find(list(BLOCK_TAGS)) is not None
        if not child_blocks:
            pending.append(child.get_text(" ", strip=True))
            continue
        if clean_space(" ".join(pending)):
            yield parent, clean_space(" ".join(pending))
        pending = []
        if child.name == "table" or child.find(list(BLOCK_TAGS)) is None:
            yield child, clean_space(child.get_text(" ", strip=True))
        else:
            yield from visible_blocks(child)
    if clean_space(" ".join(pending)):
        yield parent, clean_space(" ".join(pending))


def parse_html(raw: bytes, max_chunks=4000):
    if not isinstance(raw, bytes) or not raw or b"\x00" in raw:
        raise AppError("DOCUMENT_INVALID", "财报正文为空或格式无效。", 422)
    soup = BeautifulSoup(raw, "lxml")
    title = clean_space(soup.title.get_text(" ", strip=True))[:500] if soup.title else "SEC filing"
    for element in list(soup.find_all(string=lambda value: isinstance(value, Comment))):
        element.extract()
    for element in list(soup.find_all(True)):
        if element.parent is None:
            continue
        name = element.name.lower()
        style = re.sub(r"\s+", "", element.get("style", "").lower())
        if (name in {"script", "style", "noscript", "nav", "header", "footer", "iframe", "object", "form", "ix:header", "ix:hidden"}
                or element.has_attr("hidden") or element.get("aria-hidden", "").lower() == "true"
                or "display:none" in style or "visibility:hidden" in style):
            element.decompose()
    root = soup.body or soup
    section = "Filing content"
    chunks = []
    block_index = 0
    table_index = 0

    def append(text, location, prefix=""):
        for part_index, part in enumerate(pieces(text)):
            content = (prefix + part).strip()
            if not content:
                continue
            chunks.append({"section": section, "chunk_index": len(chunks), "content": content,
                           "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                           "location": {**location, "part_index": part_index}})
            if len(chunks) > max_chunks:
                raise AppError("DOCUMENT_TOO_COMPLEX", "财报段落数量超出允许范围。", 422)

    for element, text in visible_blocks(root):
        if not text:
            continue
        block_index += 1
        anchor = element.get("id") or element.get("name")
        location = {"block_index": block_index, "tag": element.name, "anchor": str(anchor)[:300] if anchor else None}
        heading = (element.name in {"h1", "h2", "h3", "h4", "h5", "h6"} or ITEM_HEADING.search(text))
        if heading and len(text) <= 240:
            section = text
        if element.name == "table":
            table_index += 1
            caption = element.find("caption")
            caption_text = clean_space(caption.get_text(" ", strip=True)) if caption else section
            rows = element.find_all("tr")
            headers = []
            for row_index, row in enumerate(rows):
                cells = row.find_all(["td", "th"], recursive=False)
                if not cells:
                    continue
                values = [clean_space(cell.get_text(" ", strip=True)) for cell in cells]
                if not any(values):
                    continue
                if not headers and (row.find("th") or row_index == 0):
                    headers = values
                header_text = " | ".join(headers)
                prefix = f"Table: {caption_text[:240]}\nColumns: {header_text[:600]}\n"
                append(" | ".join(values), {**location, "table_index": table_index, "row_index": row_index}, prefix)
        else:
            append(text, location)
    if not chunks:
        # Valid minimal HTML may only contain text and inline spans.
        text = clean_space(root.get_text(" ", strip=True))
        if text:
            append(text, {"block_index": 1, "tag": "body", "anchor": None})
    if not chunks:
        raise AppError("DOCUMENT_EMPTY", "清洗后的财报没有可检索正文。", 422)
    return {"title": title, "chunks": chunks, "parser_version": PARSER_VERSION}
