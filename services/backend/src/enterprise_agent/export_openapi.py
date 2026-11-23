"""不启动服务或连接数据库，导出由 API schema 生成的 OpenAPI 协议。"""

import json

from enterprise_agent.config import ROOT
from enterprise_agent.main import app


def main() -> None:
    """将当前 FastAPI 应用的 OpenAPI 描述写入 contracts 目录。"""
    destination = ROOT / "contracts" / "backend.openapi.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(f"Exported {destination}")


if __name__ == "__main__":
    main()
