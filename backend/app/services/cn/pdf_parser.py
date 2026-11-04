"""Bounded text-only PDF extraction. PDF code, attachments and links are never run.

Production invokes this module in a short-lived subprocess with a wall deadline.
Linux additionally limits address space/CPU; pypdf decoding budgets apply on all
platforms. Images are not OCR'd and table text is not certified cell extraction.
"""
import argparse
import hashlib
import io
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

from app.core.errors import AppError

PARSER_VERSION = "cn-pdf-text-v2"
MAX_STREAM = 8_000_000
HEADING = re.compile(r"^(?:第[一二三四五六七八九十百0-9]+[章节部分]|[一二三四五六七八九十]+[、．.]|[0-9]+[、．.]\s*\S)")


def section_heading(value):
    """Recognize narrative headings without promoting financial rows to titles."""
    value = value.strip()
    if not 3 <= len(value) <= 90 or not HEADING.match(value):
        return False
    # Ignore a numbered heading prefix, while retaining years with Chinese
    # suffixes. Multiple bare numeric columns, grouped amounts or decimal
    # amounts identify table rows rather than document sections.
    body = re.sub(r"^[0-9]+(?:\.[0-9]+)*[、．.]?\s*", "", value)
    if re.search(r"[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+\.[0-9]{2,}", body):
        return False
    numbers = re.findall(r"(?<![\w.])[+-]?[0-9]+(?:\.[0-9]+)?[%％]?(?![\w.])", body)
    return len(numbers) < 2


def parse_pdf(raw, *, max_pages=600, max_chars=2_000_000, max_chunks=4000):
    """Pure parser for explicit fixtures and the isolated child process."""
    from pypdf import PdfReader, apply_configuration
    if not isinstance(raw, bytes) or not raw.startswith(b"%PDF-"):
        raise AppError("CN_PDF_INVALID", "来源未返回有效PDF，未保存正文。", 422)
    try:
        with apply_configuration(
            maximum_declared_stream_length=MAX_STREAM, array_based_stream_maximum_output_length=MAX_STREAM,
            zlib_maximum_output_length=MAX_STREAM, lzw_maximum_output_length=MAX_STREAM,
            run_length_maximum_output_length=MAX_STREAM, brotli_maximum_output_length=MAX_STREAM,
            jbig2_maximum_output_length=MAX_STREAM, image_maximum_buffer_size=MAX_STREAM,
            stream_filters_maximum_length=4, stream_decoding_work_maximum_length=MAX_STREAM * 4,
            stream_parsing_work_maximum_count=500_000, xform_maximum_invocations_per_extraction=1000,
            page_tree_maximum_entries=max_pages * 10, page_tree_maximum_page_entries=max_pages * 1000,
            jbig2dec_binary=None,
        ):
            reader = PdfReader(io.BytesIO(raw), strict=False, root_object_recovery_limit=1000)
            if reader.is_encrypted:
                raise AppError("CN_PDF_ENCRYPTED", "加密PDF暂不支持解析，请查看原文。", 422)
            pages = len(reader.pages)
            if not 1 <= pages <= max_pages:
                raise AppError("CN_PDF_PAGE_LIMIT", "PDF页数超过解析上限，未截断保存。", 422)
            chunks, text_pages, total_chars, empty_pages = [], 0, 0, []
            section = "正文"
            for page_number, page in enumerate(reader.pages, 1):
                contents = page.get_contents()
                if contents is not None and len(contents.get_data()) > MAX_STREAM:
                    raise AppError("CN_PDF_RESOURCE_LIMIT", "PDF内容流超过解析上限。", 422)
                extracted = page.extract_text(extraction_mode="layout", layout_mode_space_vertically=False) or ""
                text = unicodedata.normalize("NFC", extracted).replace("\x00", "")
                text = "\n".join(line.rstrip() for line in text.splitlines()).strip()
                total_chars += len(text)
                if total_chars > max_chars:
                    raise AppError("CN_PDF_TEXT_LIMIT", "PDF正文超过解析上限，未截断保存。", 422)
                if len(text.strip()) < 12:
                    empty_pages.append(page_number)
                    continue
                text_pages += 1
                offset = 0
                while offset < len(text):
                    end = min(offset + 1400, len(text))
                    if end < len(text):
                        split = text.rfind("\n", offset + 700, end)
                        if split > offset:
                            end = split
                    content = text[offset:end].strip()
                    for line in content.splitlines():
                        if section_heading(line):
                            section = line.strip()
                            break
                    if content:
                        chunks.append({"chunk_index": len(chunks), "section": section,
                            "content": content, "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                            "location": {"page_start": page_number, "page_end": page_number,
                                         "text_start": offset, "text_end": end, "extraction": "pdf_text_layout"}})
                    if len(chunks) > max_chunks:
                        raise AppError("CN_PDF_CHUNK_LIMIT", "PDF段落超过解析上限，未截断保存。", 422)
                    if end == len(text):
                        break
                    offset = max(offset + 1, end - 100)
            if total_chars < 80 or not chunks:
                raise AppError("CN_PDF_OCR_REQUIRED", "PDF缺少可提取文字，可能为扫描件；当前未启用OCR。", 422)
            return {"page_count": pages, "chunks": chunks, "coverage": {
                "text_pages": text_pages, "empty_or_image_pages": empty_pages,
                "total_chars": total_chars, "ocr": False, "all_pages_have_text": not empty_pages,
                "text_extraction_quality": "not_independently_verified",
                "limitations": ["仅提取PDF文本层，未进行OCR；无文字页已明确列出。",
                                "表格按文本布局保留，不保证单元格结构或阅读顺序完全正确。"],
            }}
    except AppError:
        raise
    except (MemoryError, RecursionError):
        raise AppError("CN_PDF_RESOURCE_LIMIT", "PDF超过解析资源上限。", 422) from None
    except Exception:
        raise AppError("CN_PDF_INVALID", "PDF损坏、格式不受支持或超过安全解析预算。", 422) from None


def parse_isolated(raw, *, settings):
    command = [sys.executable, "-m", "app.services.cn.pdf_parser",
               "--max-pages", str(settings.cn_pdf_max_pages), "--max-chars", str(settings.cn_pdf_max_chars),
               "--max-chunks", str(settings.cn_pdf_max_chunks), "--max-bytes", str(settings.cn_pdf_max_bytes),
               "--cpu-seconds", str(int(settings.cn_pdf_parse_timeout_seconds))]
    try:
        result = subprocess.run(command, input=raw, capture_output=True, cwd=Path(__file__).resolve().parents[3],
                                timeout=settings.cn_pdf_parse_timeout_seconds, check=False)
    except subprocess.TimeoutExpired:
        raise AppError("CN_PDF_PARSE_TIMEOUT", "PDF解析超时，未保存不完整正文。", 422) from None
    except OSError:
        raise AppError("CN_PDF_PARSER_UNAVAILABLE", "PDF解析进程无法启动，请检查运行环境。", 503) from None
    if result.returncode != 0 or len(result.stdout) > settings.cn_pdf_max_chars * 12 + 2_000_000:
        raise AppError("CN_PDF_RESOURCE_LIMIT", "PDF解析进程未完成，未保存不完整正文。", 422)
    try:
        payload = json.loads(result.stdout)
        if "error" in payload:
            raise AppError(payload["error"]["code"], payload["error"]["message"], 422)
        if not isinstance(payload.get("chunks"), list) or not payload["chunks"]:
            raise ValueError
        return payload
    except (ValueError, TypeError, KeyError):
        raise AppError("CN_PDF_INVALID", "PDF解析输出无效。", 422) from None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-pages", type=int, required=True)
    parser.add_argument("--max-chars", type=int, required=True)
    parser.add_argument("--max-chunks", type=int, required=True)
    parser.add_argument("--max-bytes", type=int, required=True)
    parser.add_argument("--cpu-seconds", type=int, required=True)
    args = parser.parse_args()
    try:
        if sys.platform != "win32":
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (1_500_000_000, 1_500_000_000))
            resource.setrlimit(resource.RLIMIT_CPU, (args.cpu_seconds, args.cpu_seconds + 1))
        raw = sys.stdin.buffer.read(args.max_bytes + 1)
        if len(raw) > args.max_bytes:
            raise AppError("CN_PDF_TOO_LARGE", "PDF超过下载大小上限。", 422)
        output = parse_pdf(raw, max_pages=args.max_pages, max_chars=args.max_chars, max_chunks=args.max_chunks)
    except AppError as error:
        output = {"error": {"code": error.code, "message": error.message}}
    print(json.dumps(output, ensure_ascii=True))


if __name__ == "__main__":
    main()
