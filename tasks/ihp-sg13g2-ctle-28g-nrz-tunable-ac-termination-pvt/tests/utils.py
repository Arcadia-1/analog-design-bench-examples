"""Result files of the verifier."""

import json
from pathlib import Path

import ihp_signoff


def write_results(checks, output=Path("/logs/verifier")):
    """Write reward.json and new-ctrf.json; the reward is the passed fraction, and 0 after a broken signoff rule."""
    if ihp_signoff.VIOLATIONS:
        checks = list(checks) + [ihp_signoff.rule_check()]
    passed = sum(ok for _, ok, _ in checks)
    reward = 0 if ihp_signoff.VIOLATIONS else round(passed / len(checks), 12)
    tests = [{"name": name, "status": "passed" if ok else "failed", "message": message} for name, ok, message in checks]
    output.mkdir(parents=True, exist_ok=True)
    (output / "reward.json").write_text(json.dumps({
        "reward": reward, "tests_total": len(checks), "tests_passed": passed, "partial": reward,
    }) + "\n")
    (output / "new-ctrf.json").write_text(json.dumps({
        "results": {
            "summary": {"tests": len(checks), "passed": passed, "failed": len(checks) - passed},
            "tests": tests,
        }
    }, indent=2) + "\n")
    for test in tests:
        print(f"{test['status'].upper()} {test['name']}: {test['message']}")
