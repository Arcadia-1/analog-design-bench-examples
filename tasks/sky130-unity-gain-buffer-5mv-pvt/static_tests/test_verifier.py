"""Offline verifier regressions. These tests never start ngspice or a container."""

import contextlib
import io
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest import mock


TASK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TASK / "tests"))
import verify as verifier

GOOD = {
    "tracking_error_v": 3e-3,
    "buffer_gain_vv": 0.99,
    "buffer_bandwidth_hz": 150e6,
    "input_capacitance_f": 0.28e-12,
    "quiescent_dut_power_w": 0.29e-3,
    "settling_rise_time_s": 2.6e-9,
    "settling_fall_time_s": 4.7e-9,
    "settling_rise_final_tracking_error_v": 3.3e-3,
    "settling_fall_final_tracking_error_v": 1.2e-3,
    "settling_overshoot_v": 0.0,
    "settling_undershoot_v": 5.7e-3,
}
LIMITS = {
    "tracking_error_v": (5e-3, "tracking_5mv", "upper"),
    "buffer_gain_vv": (0.95, "gain_bandwidth_100mhz", "lower"),
    "buffer_bandwidth_hz": (100e6, "gain_bandwidth_100mhz", "lower"),
    "input_capacitance_f": (0.5e-12, "input_loading_0p5pf", "upper"),
    "quiescent_dut_power_w": (0.5e-3, "quiescent_power_0p5mw", "upper"),
    "settling_rise_time_s": (10e-9, "step_settling", "upper"),
    "settling_fall_time_s": (10e-9, "step_settling", "upper"),
    "settling_rise_final_tracking_error_v": (5e-3, "step_settling", "upper"),
    "settling_fall_final_tracking_error_v": (5e-3, "step_settling", "upper"),
    "settling_overshoot_v": (10e-3, "step_settling", "upper"),
    "settling_undershoot_v": (10e-3, "step_settling", "upper"),
}


def good_row(bench, point, **overrides):
    row = {"bench": bench, "point": point, "name": verifier.point_name(point), "ok": True}
    row.update({metric: GOOD[metric] for metric in verifier.BENCH_METRICS[bench]})
    row.update(overrides)
    return row


def all_rows(**overrides):
    rows = []
    for bench, metrics in verifier.BENCH_METRICS.items():
        for point in verifier.POINTS:
            local = {key: value for key, value in overrides.items() if key in metrics}
            rows.append(good_row(bench, point, **local))
    return rows


def passed(checks):
    return {name: ok for name, ok, _ in checks}


def result_block(values):
    body = "".join(f"{key} = {value}\n" for key, value in values.items())
    return f"noise = 1\n{verifier.RESULT_BEGIN}\n{body}{verifier.RESULT_END}\n"


class EvaluationTests(unittest.TestCase):
    def test_complete_pvt_matrix_with_nominal_first(self):
        expected = {(c, s, t) for c in ("tt", "ss", "ff", "sf", "fs") for s in (1.62, 1.80, 1.98) for t in (-40, 27, 125)}
        self.assertEqual(len(verifier.POINTS), 45)
        self.assertEqual(set(verifier.POINTS), expected)
        self.assertEqual(verifier.POINTS[0], verifier.NOMINAL)
        self.assertEqual(verifier.NOMINAL, ("tt", 1.80, 27))
        self.assertEqual([name for name, _, _ in verifier.evaluate(all_rows())], list(verifier.CHECK_NAMES))

    def test_cold_low_supply_corner_is_scored(self):
        # The previous three-point set never paired 1.62 V with -40 C.
        rows = all_rows()
        target = next(row for row in rows if row["bench"] == "tracking" and row["point"] == ("sf", 1.62, -40))
        target["tracking_error_v"] = 6.18e-3
        self.assertFalse(passed(verifier.evaluate(rows))["tracking_5mv"])

    def test_all_good_passes_every_check(self):
        self.assertTrue(all(passed(verifier.evaluate(all_rows())).values()))

    def test_inclusive_boundaries_and_isolated_failures(self):
        at_limit = {metric: limit for metric, (limit, _, _) in LIMITS.items()}
        self.assertTrue(all(passed(verifier.evaluate(all_rows(**at_limit))).values()))
        for metric, (limit, check, side) in LIMITS.items():
            beyond = limit * (1 + 1e-6) if side == "upper" else limit * (1 - 1e-6)
            if limit == 0:
                beyond = 1e-9
            with self.subTest(metric=metric):
                rows = all_rows()
                bench = next(b for b, metrics in verifier.BENCH_METRICS.items() if metric in metrics)
                target = next(row for row in rows if row["bench"] == bench and row["point"] == verifier.POINTS[2])
                target[metric] = beyond
                result = passed(verifier.evaluate(rows))
                self.assertFalse(result[check])
                self.assertEqual([name for name, ok in result.items() if not ok], [check])

    def test_negative_power_is_rejected(self):
        self.assertFalse(passed(verifier.evaluate(all_rows(quiescent_dut_power_w=-1e-6)))["quiescent_power_0p5mw"])

    def test_passive_feedthrough_fails_only_input_loading(self):
        # Measured signature of a transmission-gate feedthrough: ideal tracking, ~1 pF input.
        rows = all_rows(tracking_error_v=1e-10, buffer_gain_vv=1.0, buffer_bandwidth_hz=268e6,
                        input_capacitance_f=1.02e-12, quiescent_dut_power_w=0.0,
                        settling_rise_time_s=1.7e-9, settling_fall_time_s=1e-9,
                        settling_rise_final_tracking_error_v=0.0, settling_fall_final_tracking_error_v=0.0,
                        settling_undershoot_v=0.0)
        result = passed(verifier.evaluate(rows))
        self.assertEqual([name for name, ok in result.items() if not ok], ["input_loading_0p5pf"])

    def test_missing_failed_duplicate_and_nonfinite_rows_fail_closed(self):
        variants = []
        rows = all_rows()
        variants.append(("missing run", rows[:-1], "step_settling"))
        variants.append(("duplicate run", rows[:-1] + [rows[-2]], "step_settling"))
        failed = all_rows()
        index = next(i for i, row in enumerate(failed) if row["bench"] == "ac" and row["point"] == ("fs", 1.98, 125))
        failed[index] = {**failed[index], "ok": False, "error": "ngspice exited 1"}
        variants.append(("failed run", failed, "gain_bandwidth_100mhz"))
        for value in (math.nan, math.inf, "bad", None):
            broken = all_rows()
            broken[0] = {**broken[0], "tracking_error_v": value}
            variants.append((f"value {value}", broken, "tracking_5mv"))
        missing_metric = all_rows()
        del missing_metric[-1]["settling_undershoot_v"]
        variants.append(("missing metric", missing_metric, "step_settling"))
        for label, variant, check in variants:
            with self.subTest(label=label):
                result = passed(verifier.evaluate(variant))
                self.assertFalse(result["complete_signoff"])
                self.assertFalse(result[check])
        self.assertFalse(any(passed(verifier.evaluate([])).values()))


class ParserTests(unittest.TestCase):
    metrics = verifier.BENCH_METRICS["ac"]
    values = {"buffer_gain_vv": "9.903900e-01", "buffer_bandwidth_hz": "2.015671e+08",
              "input_capacitance_f": "2.194831e-13"}

    def test_marked_block_parses_exactly(self):
        text = "buffer_gain_vv      =  9.90390e-01\n" + result_block(self.values)
        self.assertEqual(verifier.parse_results(text, self.metrics),
                         {"buffer_gain_vv": 0.99039, "buffer_bandwidth_hz": 201567100.0, "input_capacitance_f": 2.194831e-13})

    def test_malformed_blocks_rejected(self):
        good = result_block(self.values)
        variants = {
            "no markers": "\n".join(f"{k} = {v}" for k, v in self.values.items()),
            "duplicate begin": verifier.RESULT_BEGIN + "\n" + good,
            "missing end": good.replace(verifier.RESULT_END, ""),
            "reversed": good.replace(verifier.RESULT_BEGIN, "TMP").replace(verifier.RESULT_END, verifier.RESULT_BEGIN).replace("TMP", verifier.RESULT_END),
            "duplicate metric": good.replace(verifier.RESULT_END, "buffer_gain_vv = 0.99\n" + verifier.RESULT_END),
            "missing metric": good.replace("input_capacitance_f = 2.194831e-13\n", ""),
            "unexpected metric": good.replace(verifier.RESULT_END, "tracking_error_v = 0\n" + verifier.RESULT_END),
            "non-finite": good.replace("2.015671e+08", "1e999"),
            "nan text": good.replace("2.015671e+08", "nan"),
            "trailing garbage": good.replace("2.015671e+08", "2.015671e+08 at= 1"),
            "error line": good.replace(verifier.RESULT_END, "Error: no such vector\n" + verifier.RESULT_END),
        }
        for label, text in variants.items():
            with self.subTest(label=label), self.assertRaises(ValueError):
                verifier.parse_results(text, self.metrics)


class RuntimeTests(unittest.TestCase):
    def run_fake(self, stdout, returncode=0, bench="ac"):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        seen = {}

        def fake_run(command, **kwargs):
            self.assertNotIn("timeout", kwargs)
            self.assertEqual(command[:2], ["ngspice", "-b"])
            self.assertEqual(kwargs["env"]["OMP_NUM_THREADS"], "1")
            work = Path(kwargs["cwd"])
            seen["spiceinit"] = (work / ".spiceinit").read_text()
            seen["deck"] = Path(command[2]).read_text()
            kwargs["stdout"].write(stdout)
            kwargs["stdout"].flush()
            return subprocess.CompletedProcess(command, returncode)

        with mock.patch.object(verifier, "OUTPUT", Path(temp.name)), mock.patch.object(
            verifier.subprocess, "run", side_effect=fake_run
        ), contextlib.redirect_stdout(io.StringIO()):
            row = verifier.run_bench(bench, ("ss", 1.62, 125))
        self.assertEqual(seen["spiceinit"], "set num_threads=1\n")
        self.assertEqual(Path(row["evidence_dir"], "ngspice.log").read_text(), stdout)
        return row, seen

    def test_success_single_thread_and_corner_substitution(self):
        values = {"buffer_gain_vv": 0.99, "buffer_bandwidth_hz": 1.5e8, "input_capacitance_f": 2.8e-13}
        row, seen = self.run_fake("Warning: Eta0 = -0.00264152 is negative.\nNote: Starting dynamic gmin stepping\n"
                                  + result_block(values))
        self.assertTrue(row["ok"], row)
        self.assertEqual({key: row[key] for key in values}, values)
        self.assertIn('.lib "/opt/sky130/continuous/sky130.lib.spice" ss\n', seen["deck"])
        self.assertIn(".param supply=1.62\n", seen["deck"])
        self.assertIn(".param temperature=125\n", seen["deck"])
        self.assertIn('.include "/app/circuit.spi"\n', seen["deck"])

    def test_nonzero_exit_rejects_good_results(self):
        values = {"buffer_gain_vv": 0.99, "buffer_bandwidth_hz": 1.5e8, "input_capacitance_f": 2.8e-13}
        row, _ = self.run_fake(result_block(values), returncode=1)
        self.assertFalse(row["ok"])
        self.assertIn("exited 1", row["error"])

    def test_simulator_error_text_rejects_good_results(self):
        values = {"buffer_gain_vv": 0.99, "buffer_bandwidth_hz": 1.5e8, "input_capacitance_f": 2.8e-13}
        for message in ("Error: measure  bandwidth_crossing_hz  when(WHEN) : out of interval\n",
                        "doAnalyses: TRAN:  Timestep too small\n", "run simulation(s) aborted\n",
                        "meas ac bandwidth_crossing_hz when transfer_db=x fall=1 failed!\n",
                        "Fatal error: singular matrix\n"):
            with self.subTest(message=message):
                row, _ = self.run_fake(message + result_block(values))
                self.assertFalse(row["ok"])

    def test_main_is_serial_binary_and_complete(self):
        def fake(bench, point):
            return good_row(bench, point)

        with tempfile.TemporaryDirectory() as work, mock.patch.object(verifier, "OUTPUT", Path(work)), \
                mock.patch.object(verifier, "run_bench", side_effect=fake) as run, \
                mock.patch.object(verifier, "ThreadPoolExecutor", create=True) as pool, \
                contextlib.redirect_stdout(io.StringIO()):
            verifier.main()
            result = json.loads((Path(work) / "reward.json").read_text())
        pool.assert_not_called()
        expected = [(bench, verifier.NOMINAL) for bench in verifier.BENCH_METRICS]
        expected += [(bench, point) for bench in verifier.BENCH_METRICS for point in verifier.POINTS[1:]]
        self.assertEqual([call.args for call in run.call_args_list], expected)
        self.assertEqual(len(expected), 135)
        self.assertEqual(result, {"reward": 1, "tests_total": 6, "tests_passed": 6, "partial": 1.0})

    def test_single_off_nominal_failure_gives_zero_reward(self):
        def fake(bench, point):
            extra = {"input_capacitance_f": 1.0e-12} if bench == "ac" and point == ("fs", 1.62, -40) else {}
            return good_row(bench, point, **extra)

        with tempfile.TemporaryDirectory() as work, mock.patch.object(verifier, "OUTPUT", Path(work)), \
                mock.patch.object(verifier, "run_bench", side_effect=fake), contextlib.redirect_stdout(io.StringIO()):
            verifier.main()
            result = json.loads((Path(work) / "reward.json").read_text())
        self.assertEqual(result["reward"], 0)
        self.assertEqual(result["tests_passed"], 5)

    def test_nominal_failure_skips_matrix_and_gives_no_credit(self):
        def fake(bench, point):
            return good_row(bench, point, **({"tracking_error_v": 7e-3} if bench == "tracking" else {}))

        with tempfile.TemporaryDirectory() as work, mock.patch.object(verifier, "OUTPUT", Path(work)), \
                mock.patch.object(verifier, "run_bench", side_effect=fake) as run, \
                contextlib.redirect_stdout(io.StringIO()):
            verifier.main()
            result = json.loads((Path(work) / "reward.json").read_text())
            ctrf = json.loads((Path(work) / "new-ctrf.json").read_text())
        self.assertEqual([call.args for call in run.call_args_list],
                         [(bench, verifier.NOMINAL) for bench in verifier.BENCH_METRICS])
        self.assertEqual(result, {"reward": 0, "tests_total": 6, "tests_passed": 0, "partial": 0.0})
        messages = {test["name"]: test["message"] for test in ctrf["results"]["tests"]}
        self.assertTrue(messages["tracking_5mv"].startswith("nominal FAIL"))
        self.assertTrue(messages["quiescent_power_0p5mw"].startswith("nominal PASS"))
        self.assertTrue(all("remaining PVT points not run" in message for message in messages.values()))

    def test_unexpected_exception_becomes_zero_with_diagnostics(self):
        with tempfile.TemporaryDirectory() as work, mock.patch.object(verifier, "OUTPUT", Path(work)), \
                mock.patch.object(verifier, "run_signoff", side_effect=RuntimeError("boom")), \
                contextlib.redirect_stdout(io.StringIO()):
            verifier.main()
            result = json.loads((Path(work) / "reward.json").read_text())
            ctrf = json.loads((Path(work) / "new-ctrf.json").read_text())
        self.assertEqual(result, {"reward": 0, "tests_total": 6, "tests_passed": 0, "partial": 0.0})
        self.assertIn("boom", ctrf["results"]["tests"][0]["message"])


class ContractTests(unittest.TestCase):
    def test_public_decks_match_hidden_signoff_definitions(self):
        for bench in verifier.BENCH_METRICS:
            hidden = (TASK / "tests/benches" / f"tb_{bench}.spi").read_text().splitlines()
            public = (TASK / "environment/starter/testbench" / f"tb_{bench}_tt.spi").read_text().splitlines()
            with self.subTest(bench=bench):
                self.assertEqual(hidden[2], '.include "/app/circuit.spi"')
                self.assertEqual(public[2], '.include "../circuit.spi"')
                self.assertEqual(hidden[1], public[1])
                self.assertEqual(hidden[3:], public[3:])

    def test_each_substitution_matches_exactly_once(self):
        for bench in verifier.BENCH_METRICS:
            for point in verifier.POINTS:
                deck = verifier.render_deck(bench, point)
                self.assertIn(f".param supply={point[1]:.12g}\n", deck)
                self.assertIn(f".lib \"{verifier.MODEL}\" {point[0]}\n", deck)

    def test_benches_print_every_scored_metric_between_markers(self):
        for bench, metrics in verifier.BENCH_METRICS.items():
            text = (TASK / "tests/benches" / f"tb_{bench}.spi").read_text()
            block = text[text.index("echo " + verifier.RESULT_BEGIN):text.index("echo " + verifier.RESULT_END)]
            printed = [word.lower() for line in block.splitlines() if line.startswith("print ") for word in line.split()[1:]]
            self.assertEqual(sorted(printed), sorted(metrics))
            self.assertEqual(text.count("echo " + verifier.RESULT_BEGIN), 1)

    def test_bench_conditions(self):
        ac = (TASK / "tests/benches/tb_ac.spi").read_text()
        settling = (TASK / "tests/benches/tb_settling.spi").read_text()
        tracking = (TASK / "tests/benches/tb_tracking.spi").read_text()
        for text in (ac, settling, tracking):
            self.assertIn("IBIAS vdd ibias 50u\n", text)
            self.assertIn("CL vout vss 1p\n", text)
            self.assertIn("XBUF vss ibias vdd vin vout unity_gain_buffer\n", text)
        self.assertIn("ac dec 100 1k 10Gig\n", ac)
        self.assertIn("FIND input_admittance_mag AT=100Meg", ac)
        self.assertIn("(2*3.141592653589793*100e6)", ac)
        self.assertIn("WHEN transfer_db=bandwidth_threshold_db FALL=1", ac)
        self.assertIn("tran 10p 220n\n", settling)
        self.assertIn("(rise_error gt 5m)*(time ge 20n)*(time le 79.9n)", settling)
        self.assertIn("(fall_error gt 5m)*(time ge 140n)*(time le 220n)", settling)
        self.assertIn("FIND v(vin) AT=60n", settling)
        self.assertIn("FIND v(vin) AT=180n", settling)
        self.assertIn("dc VSWEEP 0 1 0.008333333333\n", tracking)
        self.assertEqual(round(1 / 0.008333333333) + 1, 121)

    def test_spec_states_verifier_limits_and_conditions(self):
        instruction = (TASK / "environment/starter/spec/README.md").read_text()
        for text in ("tt, ss, ff, sf, and fs, supplies VDD = 1.62, 1.80, and 1.98 V, and temperatures -40, 27, and 125 C (45 points)",
                     "local mismatch, Monte Carlo variation, and layout parasitics are not modeled",
                     "The nominal tt/1.80 V/27 C point is evaluated first", "at all 45 PVT points", "no greater than 5 mV",
                     "121-point DC sweep", "at least 0.95 V/V at 1 kHz", "at least 100 MHz",
                     "1 kHz to 10 GHz at 100 points per decade", "Input loading no greater than 0.5 pF",
                     "/ (2*pi*100 MHz)", "must not exceed 0.5 mW", "10 ns to 19 ns",
                     "must not exceed 10 ns", "70-79 ns", "210-220 ns", "must not exceed 10 mV",
                     "zero source resistance", "The reward is 1 only when every requirement"):
            self.assertIn(text, instruction)
        self.assertEqual(verifier.TRACKING_MAX_V, 5e-3)
        self.assertEqual(verifier.GAIN_MIN_VV, 0.95)
        self.assertEqual(verifier.BANDWIDTH_MIN_HZ, 100e6)
        self.assertEqual(verifier.INPUT_CAPACITANCE_MAX_F, 0.5e-12)
        self.assertEqual(verifier.POWER_MAX_W, 0.5e-3)
        self.assertEqual(verifier.SETTLING_MAX_S, 10e-9)
        self.assertEqual(verifier.FINAL_ERROR_MAX_V, 5e-3)
        self.assertEqual(verifier.OVERSHOOT_MAX_V, 10e-3)



if __name__ == "__main__":
    unittest.main()
