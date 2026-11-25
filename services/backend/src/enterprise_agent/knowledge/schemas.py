"""制度服务的命令与只读协议；调用方不能填写租户、责任人或发布状态。"""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RegisterDocument(BaseModel):
    """仅供受控后端命令使用；责任人与租户由已加载的管理者注入。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    source_key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    title: str = Field(min_length=1, max_length=200)
    version: int = Field(strict=True, ge=1)
    format: Literal["markdown", "text"]
    effective_from: date
    effective_until: date | None = None
    visibility: Literal["tenant", "owner"] = "tenant"

    @model_validator(mode="after")
    def valid_dates(self):
        """适用日期用于展示及后续检索，禁止保存倒置的区间。"""
        if self.effective_until is not None and self.effective_until < self.effective_from:
            raise ValueError("适用结束日期不能早于开始日期")
        return self


class DocumentBlock(BaseModel):
    """结构块不是检索 chunk；行号始终映射到同一版本原件，采用一基闭区间。"""

    model_config = ConfigDict(extra="forbid")
    id: str
    kind: Literal["heading", "paragraph", "list", "table", "code"]
    text: str
    heading_path: list[str]
    source_start_line: int = Field(ge=1)
    source_end_line: int = Field(ge=1)


class NormalizedDocument(BaseModel):
    """清洗规则版本随内容一起保存，后续规则升级不能悄悄改写旧版依据。"""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["policy-text-v1"] = "policy-text-v1"
    blocks: list[DocumentBlock] = Field(min_length=1, max_length=1000)


class DocumentSummary(BaseModel):
    """员工可读的制度元数据，不公开内部租户标识和管理命令。"""

    id: UUID
    source_key: str
    title: str
    version: int
    owner_name: str
    format: Literal["markdown", "text"]
    effective_from: date
    effective_until: date | None
    visibility: Literal["tenant", "owner"]
    status: Literal["published"] = "published"


class DocumentDetail(DocumentSummary):
    """同时提供原文与章节结构，浏览器按纯文本展示，不能直接执行文档 HTML。"""

    original_text: str
    content_hash: str
    normalized_document: NormalizedDocument


class DocumentListQuery(BaseModel):
    """限制列表大小并拒绝 tenant_id 等未定义筛选参数。"""

    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=20, ge=1, le=50)
    offset: int = Field(default=0, ge=0, le=10000)


class DocumentListResponse(BaseModel):
    """只返回当前用户可读的已发布版本；原文页仍会独立检查权限。"""

    items: list[DocumentSummary]
    total: int
    limit: int
    offset: int


class SearchDocumentsRequest(BaseModel):
    """只接受问题、适用日期与条数；租户 / 权限 / 发布状态不由调用者指定。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=300)
    as_of: date | None = None
    limit: int = Field(default=5, strict=True, ge=1, le=10)


class SearchHit(BaseModel):
    """返回完整条款作为证据；匹配子片段单独标识，不把残缺句子当作完整适用条件。"""

    document: DocumentSummary
    citation_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    matched_chunk_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    rule_version: Literal["policy-chunks-v1"] = "policy-chunks-v1"
    text: str
    heading_path: list[str]
    source_start_line: int = Field(ge=1)
    source_end_line: int = Field(ge=1)


class SearchDocumentsResponse(BaseModel):
    """候选条款最多返回 limit 条；空数组明确表示本次基线检索未命中，不等于制度无规定。"""

    query: str
    as_of: date
    limit: int
    items: list[SearchHit]


class CitationQuery(BaseModel):
    """引用由路径唯一确定；拒绝通过查询参数指定身份、版本、行号或其他引用内容。"""

    model_config = ConfigDict(extra="forbid")


class CitationDetail(BaseModel):
    """固定版本的完整引用证据；哈希供后续原文页核对，子片段和内部身份不向外暴露。"""

    document: DocumentSummary
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    citation_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    rule_version: Literal["policy-chunks-v1"] = "policy-chunks-v1"
    text: str = Field(min_length=1, max_length=131072)
    heading_path: list[str]
    source_start_line: int = Field(strict=True, ge=1)
    source_end_line: int = Field(strict=True, ge=1)

    @model_validator(mode="after")
    def ordered_lines(self):
        """原文范围采用一基闭区间，不能发布倒置或与条款行数不符的映射。"""
        if (
            self.source_end_line < self.source_start_line
            or self.text.count("\n") + 1 != self.source_end_line - self.source_start_line + 1
        ):
            raise ValueError("引用原文范围与正文不一致")
        return self
