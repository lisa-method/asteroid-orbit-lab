from __future__ import annotations

import unittest

from prepare_selector_holdout24 import (
    ENCOUNTER_STRATA,
    STRATA,
    metadata_availability_counts,
    select_objects,
    validate_availability_counts,
    _regular_grid,
)


def _document(fields, data):
    return {"signature": {"version": "1.5"}, "fields": list(fields), "data": data}


def _catalogues():
    documents = {}
    encounter_fields = ("des", "jd", "cd", "dist", "body")
    shared_encounter_rows = []
    for index, stratum in enumerate(ENCOUNTER_STRATA):
        rows = []
        for item in range(5):
            object_id = str(10000 + index * 100 + item)
            rows.append([object_id, str(2462000.5 + item), f"2029-Jan-{10 + item:02d} 00:00", str(0.01 + item / 1000), stratum])
        # This duplicate is farther away and must not displace the closest row.
        rows.append([rows[0][0], "2462000.0", "2029-Jan-09 00:00", "0.2", stratum])
        if stratum == "Jupiter":
            documents["cad_jupiter_2026_2029_10au"] = _document(encounter_fields, rows)
        else:
            shared_encounter_rows.extend(rows)
    documents["cad_2026_2029_02au"] = _document(encounter_fields, shared_encounter_rows)
    control_fields = ("pdes", "full_name", "a", "e", "q", "i", "epoch", "orbit_id")
    for index, stratum in enumerate(("inner_controls", "outer_controls")):
        rows = []
        for item in range(6):
            object_id = str(20000 + index * 100 + item)
            rows.append([object_id, f" {object_id} Synthetic", "2.2", ".1", "2.0", "1.0", "2462000.5", "1"])
        documents[stratum] = _document(control_fields, rows)
    return documents


class SelectorHoldoutTests(unittest.TestCase):
    def test_metadata_counts_have_no_object_ids_and_enforce_four_per_stratum(self):
        documents = _catalogues()
        counts = metadata_availability_counts(documents, {"10000"})
        self.assertEqual(set(counts), set(STRATA))
        self.assertTrue(all(item["eligible_count"] >= 4 and item["sufficient"] for item in counts.values()))
        self.assertTrue(all("id" not in item and "ids" not in item for item in counts.values()))
        validate_availability_counts(counts)

    def test_selection_is_deterministic_disjoint_and_uses_event_start_minus_30_days(self):
        documents = _catalogues()
        first = select_objects(documents, {"10000"})
        second = select_objects(documents, {"10000"})
        self.assertEqual(first, second)
        self.assertEqual(len(first), 24)
        self.assertEqual(len({item["id"] for item in first}), 24)
        self.assertEqual({item["stratum"] for item in first}, set(STRATA))
        self.assertTrue(all(sum(item["stratum"] == stratum for item in first) == 4 for stratum in STRATA))
        self.assertNotIn("10000", {item["id"] for item in first})
        event_starts = {item["start_date"] for item in first if item["event"] is not None}
        self.assertTrue(event_starts <= {"2028-12-11", "2028-12-12", "2028-12-13", "2028-12-14", "2028-12-15"})
        self.assertEqual({item["start_date"] for item in first if item["stratum"] == "Earth"}, {
            "2028-12-12", "2028-12-13", "2028-12-14", "2028-12-15"
        })
        self.assertTrue(all(item["start_date"] == "2029-01-01" for item in first if item["event"] is None))

    def test_cross_stratum_id_collision_is_resolved_by_greedy_order(self):
        documents = _catalogues()
        # Earth is first in STRATA, so its closest ID owns the shared row.
        venus_rows = documents["cad_2026_2029_02au"]["data"]
        venus_first = next(row for row in venus_rows if row[-1] == "Venus")
        venus_first[0] = "10000"
        # Keep five unique Venus candidates, including the shared one.
        documents["cad_2026_2029_02au"]["data"] = [
            row for row in venus_rows if not (row[-1] == "Venus" and row[0] == "10100" and row[3] == "0.2")
        ]
        counts = metadata_availability_counts(documents, set())
        self.assertEqual(counts["Earth"]["eligible_count"], 5)
        self.assertEqual(counts["Venus"]["eligible_count"], 4)
        selected = select_objects(documents, set())
        self.assertEqual(len(selected), 24)
        self.assertEqual(len({item["id"] for item in selected}), 24)
        self.assertEqual(sum(item["id"] == "10000" for item in selected), 1)

    def test_insufficient_stratum_fails_without_relaxing_policy(self):
        documents = _catalogues()
        documents["inner_controls"]["data"] = documents["inner_controls"]["data"][:3]
        counts = metadata_availability_counts(documents, set())
        with self.assertRaisesRegex(ValueError, "Fewer than 4"):
            validate_availability_counts(counts)
        with self.assertRaisesRegex(ValueError, "Fewer than 4"):
            select_objects(documents, set())

    def test_non_numbered_control_designations_are_not_candidates(self):
        documents = _catalogues()
        documents["outer_controls"]["data"][0][0] = "2015 AB"
        counts = metadata_availability_counts(documents, set())
        self.assertEqual(counts["outer_controls"]["eligible_count"], 5)

    def test_regular_grid_uses_jd_ulp_tolerance_and_checks_every_interval(self):
        step = 5.0 / 1440.0
        epochs = [2460000.5 + index * step for index in range(4)]
        parsed = [{"epoch_jd_tdb": value} for value in epochs]
        self.assertTrue(_regular_grid(parsed, step))
        parsed[2]["epoch_jd_tdb"] += 1e-6
        self.assertFalse(_regular_grid(parsed, step))
