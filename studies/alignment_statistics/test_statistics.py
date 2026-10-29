"""Focused tests of study bookkeeping and uncertainty presentation, not coverage."""
import json
import math
from pathlib import Path
import tempfile
import unittest

from studies.alignment_statistics import scan_statistics as scan


class StatisticsBookkeepingTests(unittest.TestCase):
    def test_declared_scan_has_28_cohorts_and_three_optional_reuses(self):
        values = list(scan.cases())
        self.assertEqual(len(values), 28)
        self.assertEqual(len({name for _, _, name in values}), 28)
        self.assertEqual([(n, repeat) for n, repeat, _ in values
                          if scan.baseline_for(n, repeat)],
                         [(100, 0), (500, 0), (1000, 0)])
        self.assertTrue(all(scan.baseline_for(n, repeat, True) is None
                            for n, repeat, _ in values))

    def test_within_n_intervals_are_disjoint_but_cross_n_overlap(self):
        for n, repeats in scan.COUNTS:
            intervals = [set(range(n * repeat, n * (repeat + 1)))
                         for repeat in range(repeats)]
            for i, first in enumerate(intervals):
                for second in intervals[i + 1:]:
                    self.assertFalse(first & second)
        self.assertTrue(set(range(100)) < set(range(200)))

    def test_sample_sd_is_distinct_from_rms_and_mean_uncertainty(self):
        value = scan.replicate_summary([1., 3., 5.])
        self.assertEqual(value["mean_error"], 3.)
        self.assertEqual(value["sample_sd"], 2.)
        self.assertAlmostEqual(value["rms_error"], math.sqrt(35. / 3.))
        self.assertAlmostEqual(value["standard_error_of_mean"], 2. / math.sqrt(3.))
        self.assertEqual(value["repetitions"], 3)

    def test_single_cohort_has_no_measured_variance(self):
        value = scan.replicate_summary([-3.])
        self.assertEqual(value["mean_error"], -3.)
        self.assertEqual(value["rms_error"], 3.)
        self.assertIsNone(value["sample_sd"])
        self.assertIsNone(value["standard_error_of_mean"])

    def test_invalid_summary_values_are_refused(self):
        for values in ([], [float("nan")], [0., float("inf")]):
            with self.subTest(values=values), self.assertRaises(ValueError):
                scan.replicate_summary(values)

    def test_read_only_relocated_study_uses_its_actual_directory(self):
        study = Path("/reference/study")
        entry = {"name": "n0200_r01", "n": 200, "repeat": 1,
                 "baseline_run": None, "new_run": "/old/producer/analysis/scan_2000/runs/n0200_r01"}
        self.assertEqual(scan.case_directory(study, entry),
                         study / "analysis/scan_2000/runs/n0200_r01")
        entry = dict(entry, name="n0500_r00", n=500, repeat=0,
                     baseline_run="accuracy_N500")
        self.assertEqual(scan.case_directory(study, entry), study / "accuracy_N500")

    def test_unknown_or_wrong_cohort_baseline_is_refused(self):
        entry = {"name": "n0200_r01", "n": 200, "repeat": 1,
                 "baseline_run": "accuracy_N100"}
        with self.assertRaises(ValueError):
            scan.case_directory(Path("/reference"), entry)
        with self.assertRaises(ValueError):
            scan.case_directory(Path("/reference"), dict(entry, name="../escape"))

    def test_verified_final_rejects_tampering_and_never_claims_coverage(self):
        from calibration.native_controller import seal
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            final = root / "final"
            final.mkdir()
            report = {"engineering_success": True, "applied_corrections": 1,
                      "status": "converged", "physics_validation": "not_validated"}
            (final / "report.json").write_text(json.dumps(report))
            (final / "candidate.xml").write_text("<candidate/>")
            seal(final)
            self.assertEqual(scan.verified_final(root), report)
            (final / "candidate.xml").write_text("<modified/>")
            with self.assertRaisesRegex(RuntimeError, "Modified sealed output"):
                scan.verified_final(root)

    def test_explicit_ledger_keeps_order_without_interpreting_the_reason(self):
        ledger = {"schema": "explicit-event-selection-v1", "source_raw_root_sha256": "source",
                  "source_channel_payload_sha256": "payload",
                  "exclude": [{"run_number": 2, "event_number": 11,
                               "reason": "Researcher-recorded tracking failure; no automatic inference"}]}
        kept, excluded = scan.selection_indices([(2, 10), (2, 11), (2, 13)], ledger, "source", "payload")
        self.assertEqual(kept, [0, 2])
        self.assertEqual(excluded, [{"source_event_index": 1, "run_number": 2,
                                    "event_number": 11, "reason": ledger["exclude"][0]["reason"]}])

    def test_ledger_requires_exact_source_and_payload_identities(self):
        ledger = {"schema": "explicit-event-selection-v1", "source_raw_root_sha256": "source",
                  "source_channel_payload_sha256": "payload", "exclude": []}
        for source, payload in (("other", "payload"), ("source", "other")):
            with self.subTest(source=source, payload=payload), self.assertRaises(ValueError):
                scan.selection_indices([(0, 1)], ledger, source, payload)

    def test_unknown_duplicate_or_unexplained_exclusions_are_refused(self):
        base = {"schema": "explicit-event-selection-v1", "source_raw_root_sha256": "source",
                "source_channel_payload_sha256": "payload"}
        good = {"run_number": 0, "event_number": 1, "reason": "Explicit research decision"}
        for excluded in ([dict(good, event_number=99)], [good, good],
                         [dict(good, reason="")], [dict(good, event_number=True)]):
            with self.subTest(excluded=excluded), self.assertRaises(ValueError):
                scan.selection_indices([(0, 1), (0, 2)], dict(base, exclude=excluded), "source", "payload")
        with self.assertRaises(ValueError):
            scan.selection_indices([(0, 1), (0, 1)], dict(base, exclude=[]), "source", "payload")

    def test_raw_clone_bridge_uses_every_integer_field(self):
        from studies.alignment_statistics.compare_hit_residuals import bridge_raw
        self.assertEqual(bridge_raw([(99, 8, -2)], [(88, 1, 4), (99, 8, -2)])[99], (99, 8, -2))
        for changed in ((98, 8, -2), (99, 9, -2), (99, 8, -1)):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                bridge_raw([(99, 8, -2)], [changed])

    def test_raw_bridge_refuses_ambiguous_cells(self):
        from studies.alignment_statistics.compare_hit_residuals import bridge_raw
        with self.assertRaises(ValueError):
            bridge_raw([(99, 8, -2)], [(99, 8, -2), (99, 8, -2)])
        with self.assertRaises(ValueError):
            bridge_raw([(99, 8, -2), (99, 8, -2)], [(99, 8, -2)])


if __name__ == "__main__":
    unittest.main()
