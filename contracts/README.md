# 协议资源

执行根目录 `pnpm contracts:generate`，从 FastAPI/Pydantic 导出 backend.openapi.json，并生成 NestJS 的 TypeScript Schema 类型。

生成过程不需要启动数据库，但需要先安装 Python 和 Node 依赖。当前没有手工伪造 OpenAPI 导出文件。

BFF 浏览器接口当前记录在 docs/api-boundaries.md。后续增加 Swagger 或独立 OpenAPI 导出时，需要避免把 Session Cookie 或内部 Token 示例写入协议。
