"""小规模 Markdown / 文本的确定性清洗；不调用模型改写制度，不生成检索片段。"""

import re

from enterprise_agent.knowledge.schemas import DocumentBlock, NormalizedDocument

MAX_DOCUMENT_BYTES = 128 * 1024
HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)\s*$")
CHINESE_HEADING = re.compile(r"^第[一二三四五六七八九十百零〇0-9]+([章节条])\s*.*$")
LIST = re.compile(r"^\s*(?:[-+*]|\d+[.)、])\s+")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def decode_original(raw: bytes) -> str:
    """限定 UTF-8 / UTF-8 BOM；错误编码应修正输入，不能替换乱码后发布。"""
    if not raw or len(raw) > MAX_DOCUMENT_BYTES:
        raise ValueError("制度原件不能为空，且不得超过 128 KiB")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("制度原件必须采用 UTF-8 编码") from exc


def clean_original(raw: bytes) -> str:
    """清洗规则集中复用，保证结构识别与后续检索片段使用完全相同的正文和行号。"""
    text = decode_original(raw).removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    return "".join(c for c in text if c in "\n\t" or (ord(c) >= 32 and ord(c) != 127))


def normalize_document(raw: bytes, document_format: str) -> NormalizedDocument:
    """保留事实与行映射，只做编码校验、换行统一和无意义控制字符清理。

    不使用 NFKC、全局空白压缩或模型润色，避免金额、单位、缩进和表格关系变化。
    原字节另存数据库；这里每个块的行范围对应清洗前的同一组物理行。
    """
    if document_format not in {"markdown", "text"}:
        raise ValueError("本步只支持 Markdown 和文本制度")
    text = clean_original(raw)
    lines = text.split("\n")
    blocks: list[DocumentBlock] = []
    headings: list[tuple[int, str]] = []
    index = 0

    def heading(line: str) -> tuple[int, str] | None:
        """识别受支持的标题结构；不识别的格式原样保留为正文，不猜测其含义。"""
        match = HEADING.match(line) if document_format == "markdown" else None
        if match:
            # 只有空格分隔的尾部 # 才是 Markdown 闭合标记，C# 等正文不可被裁掉。
            return len(match[1]), re.sub(r"[ \t]+#+$", "", match[2])
        chinese = CHINESE_HEADING.match(line)
        if chinese and chinese[1] != "条":
            # 章 / 节位于文档主标题之下；条款正文另存 paragraph，不能被标题替代。
            return {"章": 2, "节": 3}[chinese[1]], line.strip()
        return None

    def kind(line: str) -> str:
        """只用于保留结构，不负责渲染 HTML 或进行语义切分。"""
        if heading(line):
            return "heading"
        if document_format == "markdown" and FENCE.match(line):
            return "code"
        if document_format == "markdown" and "|" in line:
            return "table"
        return "list" if LIST.match(line) else "paragraph"

    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        start = index
        block_kind = kind(lines[index])
        title = heading(lines[index])
        if title:
            level, label = title
            headings = [item for item in headings if item[0] < level]
            headings.append((level, label))
            index += 1
            block_text = label
        elif block_kind == "code":
            # 围栏内的 #、表格符号等均是正文，不能被误解析成制度章节。
            fence = FENCE.match(lines[index])[1]
            index += 1
            while index < len(lines):
                closing = lines[index].strip()
                index += 1
                if len(closing) >= len(fence) and set(closing) == {fence[0]}:
                    break
            else:
                raise ValueError("Markdown 代码围栏未闭合，请修正后登记")
            block_text = "\n".join(lines[start:index])
        else:
            index += 1
            while (
                index < len(lines)
                and lines[index].strip()
                and kind(lines[index]) == block_kind
                and not CHINESE_HEADING.match(lines[index])
            ):
                index += 1
            block_text = "\n".join(lines[start:index])
        blocks.append(
            DocumentBlock(
                id=f"line-{start + 1}",
                kind=block_kind,
                text=block_text,
                heading_path=[label for _, label in headings],
                source_start_line=start + 1,
                source_end_line=index,
            )
        )
        if len(blocks) > 1000:
            raise ValueError("制度结构块超过 1000 个，请缩小本次接入范围")
    # 只有标题、空白或表格分隔符的文档不能成为正式问答依据。
    if not any(b.kind != "heading" and any(c.isalnum() for c in b.text) for b in blocks):
        raise ValueError("制度缺少有效正文，不能登记或发布")
    return NormalizedDocument(blocks=blocks)
