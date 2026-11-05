"""Replay the research adjudication with frozen reports and debate-order controls.

Runs no data tools, settlement or trading-memory writes. The saved original rating
is a reference, not a correctness label. Live model calls require --execute.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path

from tradingagents.agents.managers.research_manager import create_research_manager
from tradingagents.agents.proposal_validation import labelled_fields
from tradingagents.agents.rating_policy import REPORTS, rating_policy
from tradingagents.dataflows.config import run_config
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.llm_clients.factory import create_agent_client, resolve_agent_spec


def reorder_debate(history: str, swapped: bool) -> str:
    turns = re.findall(r"(?ms)^\s*((?:Bull|Bear) Analyst:.*?)(?=^\s*(?:Bull|Bear) Analyst:|\Z)", history)
    if not turns or len(turns) % 2:
        raise ValueError("Audit requires complete Bull/Bear pairs in the frozen debate")
    for i in range(0, len(turns), 2):
        if turns[i].startswith("Bull") == turns[i + 1].startswith("Bull"):
            raise ValueError("Frozen debate must alternate Bull/Bear turns")
        if swapped:
            turns[i], turns[i + 1] = turns[i + 1], turns[i]
    return "\n\n".join(turn.strip() for turn in turns)


def audit_case(source: Path, model_factory=None, execute=False, on_result=None) -> dict:
    state = json.loads(source.read_text(encoding="utf-8"))
    ticker = state["company_of_interest"]
    evidence = {key: state.get(key, "") for key in REPORTS}
    evidence_hash = hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()
    cfg = deepcopy(DEFAULT_CONFIG)
    cfg.update(state.get("run_settings") or {})
    model_spec = resolve_agent_spec(cfg, "research_manager")
    # Old reports did not save the evaluation policy. Adopt the current policy
    # explicitly rather than implying those old decisions used these thresholds.
    result = {
        "source": str(source.resolve()), "ticker": ticker, "trade_date": state["trade_date"],
        "evidence_sha256": evidence_hash,
        "original_rating": state.get("final_rating"), "policy": rating_policy(ticker, cfg),
        "model": model_spec["model"], "provider": model_spec["provider"],
        "executed": execute, "variants": [],
    }
    factory = model_factory or (lambda config: create_agent_client(config, "research_manager").get_llm())
    history = state["investment_debate_state"]["history"]
    for independent in (False, True):
        for swapped in (False, True):
            trial = deepcopy(state)
            trial["investment_debate_state"]["history"] = reorder_debate(history, swapped)
            trial["investment_debate_state"].setdefault("count", len(re.findall(r"(?m)^\s*(?:Bull|Bear) Analyst:", history)))
            config = {**cfg, "rating_independent_review": independent}
            row = {"independent_review": independent, "debate_order": "swapped" if swapped else "original"}
            if execute:
                try:
                    with run_config(config):
                        out = create_research_manager(factory(config))(trial)
                    fields = labelled_fields(out["investment_plan"])
                    row.update({
                        "recommendation": fields.get("recommendation", "REVIEW"),
                        "validation": out["research_validation"],
                        "decision": out["investment_plan"],
                        "independent_assessment": out["independent_research_plan"],
                    })
                except Exception as exc:
                    # Preserve completed comparisons and expose errors, not guessed ratings.
                    row["error"] = f"{type(exc).__name__}: {exc}"
            result["variants"].append(row)
            if on_result:
                on_result(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs="+", type=Path, help="Saved full_states_log_*.json files")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--execute", action="store_true", help="Invoke the configured research-manager model")
    args = parser.parse_args()
    if args.output.resolve() in {source.resolve() for source in args.sources}:
        parser.error("--output must not overwrite a source state file")
    results = []
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def save(case):
        document = {"scope": "Frozen research-manager sensitivity replay; not a new market analysis or return backtest", "cases": [*results, case]}
        args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        last = case["variants"][-1]
        print(f"{case['ticker']} independent={last['independent_review']} order={last['debate_order']}: "
              f"{last.get('recommendation', last.get('error', 'planned'))}", flush=True)

    for source in args.sources:
        results.append(audit_case(source, execute=args.execute, on_result=save))


if __name__ == "__main__":
    main()
