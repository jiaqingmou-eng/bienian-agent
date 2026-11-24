"""有界评测入口：默认验证参考提取，--live 才调用模型；不调用任何财务写工具。"""

import argparse
import json
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from enterprise_agent.agent.model import CompatibleExtractor
from enterprise_agent.agent.prompts import PROMPT_VERSION
from enterprise_agent.agent.schemas import Extraction
from enterprise_agent.agent.workflow import interpret


class ReferenceExtractor:
    def __init__(self, reference):
        self.reference = reference

    def extract(self, _context):
        return Extraction.model_validate(self.reference)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("evals/expense_cases.json"))
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.limit <= 30:
        parser.error("--limit must be between 1 and 30")
    cases = json.loads(args.dataset.read_text(encoding="utf-8"))[: args.limit]
    results = []
    for case in cases:
        context = {
            "anchor_date": case["anchor_date"],
            "timezone": "Asia/Hong_Kong",
            "current_candidate": {},
            "history": [{"id": str(uuid4()), "role": "user", "content": case["message"]}],
        }
        try:
            state = interpret(
                CompatibleExtractor() if args.live else ReferenceExtractor(case["reference"]),
                context,
            )
            intent = state["extraction"].intent
            status = (
                "cancelled"
                if intent == "cancel"
                else (
                    "awaiting_confirmation"
                    if intent == "draft" and state["payload"]
                    else "collecting"
                )
            )
            passed = status == case["expected_status"]
            if "expected_total_fen" in case:
                total = sum(
                    item["amount_fen"] for item in (state["payload"] or {}).get("items", [])
                )
                passed = passed and total == case["expected_total_fen"]
            if "expected_date" in case:
                items = (state["payload"] or {}).get("items", [])
                passed = passed and bool(items) and items[0]["incurred_on"] == case["expected_date"]
            results.append({"id": case["id"], "passed": passed, "status": status})
        except HTTPException as exc:
            results.append({"id": case["id"], "passed": False, "error_status": exc.status_code})
    report = {
        "mode": "live-model" if args.live else "reference-validation-only",
        "prompt_version": PROMPT_VERSION,
        "cases": len(results),
        "passed": sum(row["passed"] for row in results),
        "results": results,
    }
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    print(output)
    raise SystemExit(0 if all(row["passed"] for row in results) else 1)


if __name__ == "__main__":
    main()
