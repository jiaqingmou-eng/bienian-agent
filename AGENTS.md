# Project conventions

- Follow the phased roadmap and acceptance gates in `plam.md`; update task status and validation evidence as work progresses. Planned capabilities are not implemented capabilities.
- Keep React, NestJS BFF and FastAPI independently buildable. Do not import source across applications.
- Python finance services own business authorization, monetary calculations and database writes.
- Agent tools must call these services, not bypass them with direct writes.
- Use integer CNY fen. Validate unknown input; reject caller-assigned tenant, applicant and status fields.
- Keep TypeScript strict. Explain non-obvious constraints and transaction boundaries in comments.
- Add a new Alembic revision for subsequent schema changes. Never rewrite a published revision.
- Regenerate backend OpenAPI and BFF transport types when contracts change.
- Run relevant checks from README when dependencies are available. Report checks that could not run.
- Never commit credentials, .env, real invoices or personal financial data.
