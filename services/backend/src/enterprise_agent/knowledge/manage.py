"""受控本机管理命令：登记 / 发布 / 撤回制度，不提供任意 HTTP 上传或 Agent 写工具。"""

import argparse
import json
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

from enterprise_agent.db.models import User
from enterprise_agent.db.session import get_engine
from enterprise_agent.knowledge.normalization import MAX_DOCUMENT_BYTES
from enterprise_agent.knowledge.retrieval import build_index
from enterprise_agent.knowledge.schemas import RegisterDocument
from enterprise_agent.knowledge.service import change_publication, register_document


def main() -> None:
    """操作者从数据库读取，不从文档 JSON 接受租户、角色、责任人或状态。

    该命令属于具备数据库访问权的运维边界，--actor 指定受控执行身份，不是用户登录。
    对外服务仅开放读取；正式管理后台及企业身份接入另行安排。
    """
    parser = argparse.ArgumentParser(description="受控制度管理（UTF-8 Markdown / 文本）")
    parser.add_argument("action", choices=["register", "publish", "withdraw", "index"])
    parser.add_argument("--actor", type=UUID, required=True)
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--file", type=Path)
    parser.add_argument("--document", type=UUID)
    args = parser.parse_args()
    try:
        with Session(get_engine()) as db:
            actor = db.get(User, args.actor)
            if actor is None:
                parser.error("操作者不存在")
            if args.action == "register":
                if args.metadata is None or args.file is None:
                    parser.error("登记需要 --metadata 和 --file")
                command = RegisterDocument.model_validate_json(
                    args.metadata.read_text(encoding="utf-8-sig")
                )
                # 限量读取，不因误选大文件将全部内容载入内存。
                with args.file.open("rb") as source:
                    raw = source.read(MAX_DOCUMENT_BYTES + 1)
                row = register_document(db, actor, command, raw, str(uuid4()))
            else:
                if args.document is None:
                    parser.error("发布 / 撤回 / 索引需要 --document")
                if args.action == "index":
                    index = build_index(db, actor, args.document, str(uuid4()))
                    print(
                        json.dumps(
                            {
                                "document_id": str(index.document_id),
                                "unit_count": index.unit_count,
                                "chunk_count": index.chunk_count,
                                "rule_version": index.rule_version,
                            }
                        )
                    )
                    return
                row = change_publication(
                    db,
                    actor,
                    args.document,
                    publish=args.action == "publish",
                    request_id=str(uuid4()),
                )
            print(json.dumps({"id": str(row.id), "status": row.status, "version": row.version}))
    except (ValueError, ValidationError, OSError) as exc:
        parser.exit(2, f"制度输入校验失败：{exc}\n")
    except HTTPException as exc:
        parser.exit(2, f"操作被拒绝（{exc.status_code}）：{exc.detail}\n")


if __name__ == "__main__":
    main()
