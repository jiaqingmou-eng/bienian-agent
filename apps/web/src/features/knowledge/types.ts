/** Web 独立维护 BFF 响应形状，不跨应用导入 Python 或 BFF 源码。 */
export interface DocumentSummary {
  id: string;
  source_key: string;
  title: string;
  version: number;
  owner_name: string;
  format: 'markdown' | 'text';
  effective_from: string;
  effective_until: string | null;
  visibility: 'tenant' | 'owner';
  status: 'published';
}

export interface DocumentDetail extends DocumentSummary {
  original_text: string;
  content_hash: string;
  normalized_document: {
    schema_version: 'policy-text-v1';
    blocks: {
      id: string;
      kind: 'heading' | 'paragraph' | 'list' | 'table' | 'code';
      text: string;
      heading_path: string[];
      source_start_line: number;
      source_end_line: number;
    }[];
  };
}

export interface DocumentList {
  items: DocumentSummary[];
  total: number;
  limit: number;
  offset: number;
}

/** 结果是检索到的完整条款，不包含生成式答案；引用和命中片段都固定到来源版本。 */
export interface SearchDocuments {
  query: string;
  as_of: string;
  limit: number;
  items: {
    document: DocumentSummary;
    citation_id: string;
    matched_chunk_id: string;
    rule_version: 'policy-chunks-v1';
    text: string;
    heading_path: string[];
    source_start_line: number;
    source_end_line: number;
  }[];
}
