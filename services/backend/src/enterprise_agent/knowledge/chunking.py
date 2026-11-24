"""将统一文档切为可追溯条款与子片段；仅在建索引时运行，查询不重新切全文。"""

import re
from hashlib import sha256
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from enterprise_agent.knowledge.normalization import CHINESE_HEADING, clean_original
from enterprise_agent.knowledge.schemas import DocumentBlock, NormalizedDocument

RULE_VERSION = "policy-chunks-v1"
TARGET_CHUNK_CHARS = 800
# 只去除常见提问套话；同一套词项规则也用于文档，规则改变时必须升级 RULE_VERSION。
STOP_PHRASES = (
    "请问",
    "一下",
    "多少",
    "什么",
    "怎么",
    "如何",
    "是否",
    "可以",
    "能否",
    "我想",
    "查询",
    "关于",
    "有关",
    "相关",
    "报销",
    "制度",
    "规定",
    "标准",
    "条款",
    "费用",
    "的",
    "了",
    "吗",
    "呢",
)


def terms(text: str) -> set[str]:
    """中文采用相邻双字词，英文 / 数字按词匹配；不声称具备语义或同义词召回能力。"""
    text = text.lower()
    for phrase in STOP_PHRASES:
        text = text.replace(phrase, " ")
    result: set[str] = set()
    for match in re.finditer(r"[\u4e00-\u9fff]+|[a-z]+|\d+(?:\.\d+)?", text):
        word = match[0]
        if re.fullmatch(r"[\u4e00-\u9fff]+", word):
            result.update(word[i : i + 2] for i in range(len(word) - 1))
        else:
            result.add(word)
    return result


class Chunk(BaseModel):
    """子片段保存字符区间和原件行号，金额及小数点不会按固定字符数生硬截断。"""

    model_config = ConfigDict(extra="forbid")
    id: str
    sequence: int
    start_char: int
    end_char: int
    source_start_line: int
    source_end_line: int
    terms: list[str]


class EvidenceUnit(BaseModel):
    """同一条款及其后续说明 / 表格作为完整证据保留，避免检索切分遗漏限制条件。"""

    model_config = ConfigDict(extra="forbid")
    id: str
    text: str
    heading_path: list[str]
    source_start_line: int
    source_end_line: int
    chunks: list[Chunk] = Field(min_length=1)


def build_units(
    document_id: UUID, content_hash: str, raw: bytes, normalized: NormalizedDocument
) -> list[EvidenceUnit]:
    """先按章节 / 中文条款分组，再将长条款按段落与句子组成子片段。

    标题保存在路径中，不单独作为事实条款。没有条款编号的文档按所在章节分组。
    子片段仅用于匹配，返回证据时会带回整个组的正文和表格，保留完整条件。
    """
    lines = clean_original(raw).split("\n")
    groups: list[list[DocumentBlock]] = []
    current: list[DocumentBlock] = []
    for block in normalized.blocks:
        clause = CHINESE_HEADING.match(block.text)
        if block.kind == "heading" or (clause and clause[1] == "条"):
            if current:
                groups.append(current)
                current = []
        if block.kind != "heading":
            current.append(block)
    if current:
        groups.append(current)
    units: list[EvidenceUnit] = []
    for group in groups:
        first, last = group[0], group[-1]
        text = "\n".join(lines[first.source_start_line - 1 : last.source_end_line])
        prefix = f"{document_id}:{content_hash}:{RULE_VERSION}"
        unit_id = sha256(f"{prefix}:{first.id}:{last.id}:{text}".encode()).hexdigest()
        # 每一行的字符起点用于将块 / 句子边界映射回同一版原文，不使用模糊文本查找。
        line_offsets = [0]
        for line in text.split("\n"):
            line_offsets.append(line_offsets[-1] + len(line) + 1)
        boundaries = [0]
        for block in group:
            start = line_offsets[block.source_start_line - first.source_start_line]
            end = min(
                len(text), line_offsets[block.source_end_line - first.source_start_line + 1] - 1
            )
            if block.kind in {"paragraph", "list"} and end - start > TARGET_CHUNK_CHARS:
                # 句号后必须是空白 / 中文，避免把 400.00 拆成两个数字；无可用边界时保留长句。
                boundaries.extend(
                    start + m.end()
                    for m in re.finditer(
                        r"[。！？；]|[.!?;](?=\s|[\u4e00-\u9fff])", text[start:end]
                    )
                )
            boundaries.append(end)
        boundaries.append(len(text))
        atoms = sorted(set(boundaries))
        ranges: list[tuple[int, int]] = []
        start = 0
        for left, right in zip(atoms, atoms[1:], strict=False):
            if right - start > TARGET_CHUNK_CHARS and left > start:
                ranges.append((start, left))
                start = left
        if start < len(text):
            ranges.append((start, len(text)))
        chunks = []
        for sequence, (start, end) in enumerate(ranges):
            chunk_id = sha256(f"{unit_id}:{sequence}:{start}:{end}".encode()).hexdigest()
            chunks.append(
                Chunk(
                    id=chunk_id,
                    sequence=sequence,
                    start_char=start,
                    end_char=end,
                    source_start_line=first.source_start_line + text[:start].count("\n"),
                    source_end_line=first.source_start_line + text[: end - 1].count("\n"),
                    terms=sorted(terms(text[start:end]) | terms(" ".join(first.heading_path))),
                )
            )
        units.append(
            EvidenceUnit(
                id=unit_id,
                text=text,
                heading_path=first.heading_path,
                source_start_line=first.source_start_line,
                source_end_line=last.source_end_line,
                chunks=chunks,
            )
        )
    if not units or sum(len(unit.chunks) for unit in units) > 4096:
        raise ValueError("制度没有可检索条款，或子片段超过本次 4096 个上限")
    return units
