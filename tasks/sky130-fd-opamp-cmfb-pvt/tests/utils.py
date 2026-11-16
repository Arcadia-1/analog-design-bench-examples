"""Harbor result files for the verifier."""

import json
from pathlib import Path


def write_results(checks: list[tuple[str, bool, str]], output: Path = Path("/logs/verifier")) -> None:
    """Write reward.json and new-ctrf.json, and print each check.

    The reward is 1 only when every scored check passes, else 0; tests_passed and
    partial (the fraction passed) are reported for diagnosis.
    """
    passed = sum(ok for _, ok, _ in checks)
    partial = round(passed / len(checks), 12)
    reward = 1.0 if passed == len(checks) else 0.0
    tests = [
        {"name": name, "status": "passed" if ok else "failed", "message": message}
        for name, ok, message in checks
    ]
    output.mkdir(parents=True, exist_ok=True)
    (output / "reward.json").write_text(json.dumps({
        "reward": reward, "tests_total": len(checks), "tests_passed": passed, "partial": partial,
    }) + "\n")
    (output / "new-ctrf.json").write_text(json.dumps({
        "results": {
            "summary": {"tests": len(checks), "passed": passed, "failed": len(checks) - passed},
            "tests": tests,
        }
    }, indent=2) + "\n")
    for test in tests:
        print(f"{test['status'].upper()} {test['name']}: {test['message']}")
