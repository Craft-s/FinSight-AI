"""Plain Markdown export: server-owned evidence, escaped untrusted strings."""
import html
import json
import re

from app.services.research.validator import safe_citation_url

_MARKDOWN = re.compile(r"([\\`*_{}\[\]()#+.!|>~\-])")


def escape_markdown(value) -> str:
    text = html.escape(str(value), quote=False)
    return _MARKDOWN.sub(r"\\\1", text).replace("\r", "").replace("\n", " ")


def _section(lines, title):
    lines.extend(["", f"## {title}", ""])


def write_markdown(result: dict, context: dict | None = None) -> str:
    context = context or {}
    english = result.get("language", context.get("language")) == "en-US"
    def label(zh, en):
        return en if english else zh
    company = context.get("company", {})
    market = context.get("market", result.get("market", "US"))
    task_labels = {"summary": label("单份财报总结", "Filing summary"), "comparison": label("两期财报对比", "Filing comparison"),
                   "event": label("事件解读", "Event analysis"), "question": label("财报问答", "Filing question")}
    lines = [f"# {escape_markdown(result.get('title') or label('财报研究', 'Filing research'))}", ""]
    lines.append(escape_markdown(company.get("name", "")))
    if market == "CN":
        lines.append(escape_markdown(company.get("symbol", result.get("symbol", ""))))
    _section(lines, label("研究范围与方法", "Scope and method"))
    lines.append(f"- {label('研究类型', 'Task')}: {escape_markdown(task_labels.get(result.get('task_type'), result.get('task_type', '')))}")
    lines.append(f"- {label('检索方式', 'Retrieval')}: {escape_markdown(result.get('retrieval_mode', context.get('retrieval_mode', '')))}")
    lines.append(f"- {label('来源模式', 'Source mode')}: {escape_markdown(result.get('source_kind', context.get('source_kind', '')))}")
    if context.get("question"):
        lines.append(f"- {label('问题', 'Question')}: {escape_markdown(context['question'])}")
    for filing in context.get("filings", []):
        keys = ("form_type", "id", "filing_date", "report_date") if market == "CN" else ("form_type", "accession", "filing_date", "report_date")
        fields = " · ".join(str(filing.get(key, "")) for key in keys)
        lines.append(f"- {label('已选申报', 'Selected filing')}: {escape_markdown(fields)}")
    generation = context.get("generation", {})
    if generation:
        public_generation = {key: generation[key] for key in ("provider", "model_id", "config_version", "prompt_version", "duration_ms") if key in generation}
        lines.append(f"- {label('生成配置', 'Generation')}: {escape_markdown(json.dumps(public_generation, ensure_ascii=False, sort_keys=True))}")
    lines.extend(["", escape_markdown(label(
        "引用存在性、摘录匹配与复制数值已核对；模型推论的语义支持未自动验证。",
        "References, extractive matches and copied numbers were checked; semantic support of model inference was not automatically verified.",
    ))])
    if result.get("insufficient_evidence"):
        lines.extend(["", f"**{label('证据不足，不能完整回答当前任务。', 'Insufficient evidence to answer the task fully.')}**"])
    for kind, title in (("disclosed_fact", label("披露摘录与结构化事实", "Disclosed excerpts and structured facts")),
                        ("inference", label("模型推论（未自动验证语义）", "Model inferences (semantic support unverified)"))):
        _section(lines, title)
        claims = [claim for claim in result.get("claims", []) if claim.get("kind") == kind]
        if not claims:
            lines.append(label("暂无可用条目。", "No supported entries available."))
        for claim in claims:
            lines.append(f"- {escape_markdown(claim['text'])}")
            references = claim.get("citation_ids", []) + claim.get("metric_refs", [])
            lines.append(f"  {label('引用', 'References')}: {', '.join(escape_markdown(ref) for ref in references)}")
    _section(lines, label("仍未知与证据缺口", "Unknowns and evidence gaps"))
    for uncertainty in result.get("uncertainties", []):
        lines.append(f"- {escape_markdown(uncertainty)}")
    if not result.get("uncertainties"):
        lines.append(label("未新增模型说明；仍受下方方法限制约束。", "No additional model notes; the method limitations below still apply."))
    _section(lines, label("冻结财务事实", "Frozen financial facts"))
    lines.extend([
        "| ID | " + label("指标 | 期间 | 精确数值 | 单位 |", "Metric | Period | Exact value | Unit |"),
        "|---|---|---|---|---|",
    ])
    for fact in result.get("facts", []):
        # Values are validated decimal strings, rendered without a float round trip.
        value = fact.get("value")
        exact = "N/A" if value is None else str(value)
        rendered_value = f"`{exact}`" if value is None or re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?", exact) else escape_markdown(exact)
        lines.append(f"| {escape_markdown(fact['id'])} | {escape_markdown(fact['label'])} | {escape_markdown(fact['period'])} | {rendered_value} | {escape_markdown(fact['unit'])} |")
    _section(lines, label("引用与原始出处", "Citations and original sources"))
    for citation in result.get("citations", []):
        ref = escape_markdown(citation.get("citation_id", ""))
        keys = ("form_type", "announcement_id", "filing_date", "section") if market == "CN" else ("form_type", "accession", "filing_date", "section")
        details = " · ".join(str(citation.get(key, "")) for key in keys)
        lines.extend([f"### {ref}", "", escape_markdown(details), ""])
        page = (citation.get("location") or {}).get("page_start") if market == "CN" else None
        url = safe_citation_url(citation.get("source_url"), citation.get("accession"), market=market, page=page)
        source_label = label("巨潮 PDF 原文", "Original CNINFO PDF") if market == "CN" else label("SEC 原文", "Original SEC filing")
        lines.append(f"[{source_label}]({url})" if url else label("来源链接未通过校验，未生成可点击链接。", "The source URL failed validation and is not linked."))
        lines.extend(["", "> " + escape_markdown(citation.get("excerpt", "")), ""])
        identity = {key: citation.get(key) for key in ("chunk_id", "document_sha256", "location")}
        lines.append(escape_markdown(json.dumps(identity, ensure_ascii=False, sort_keys=True)))
    _section(lines, label("资料与验证限制", "Data and validation limitations"))
    for limitation in result.get("limitations", []):
        lines.append(f"- {escape_markdown(limitation)}")
    lines.extend(["", escape_markdown(result.get("disclaimer", label("仅供研究和教育，不构成投资建议。", "For research and education only. Not investment advice."))), ""])
    return "\n".join(lines)
