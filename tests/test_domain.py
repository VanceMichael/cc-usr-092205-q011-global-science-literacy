import copy
import json
import unittest
from pathlib import Path

from src.domain import (
    load_domain,
    member_homepage,
    secretariat_summary,
    validate_domain,
)

FIXTURE = Path("fixtures/domain.json")


def fresh() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class DomainTest(unittest.TestCase):
    def test_fixture_matches_domain(self):
        value = load_domain(FIXTURE)
        self.assertEqual(value["domain"], "global-science-literacy")
        self.assertGreaterEqual(len(value["constraints"]), 2)

    def test_summary_uses_four_states_and_counts_once(self):
        summary = secretariat_summary(load_domain(FIXTURE))
        self.assertEqual(summary["intent"], 1)
        self.assertEqual(summary["active"], 2)
        self.assertEqual(summary["completed"], 0)
        self.assertEqual(summary["impacting"], 1)
        self.assertEqual(summary["confirmed_activities"], 2)
        self.assertEqual(summary["activity_reports"], 4)

    def test_member_homepage_lists_dependencies_and_collaborations(self):
        page = member_homepage(load_domain(FIXTURE), "m01")
        self.assertEqual({d["id"] for d in page["outstanding_dependencies"]}, {"sa01"})
        self.assertEqual(
            {c["proposal"] for c in page["available_collaborations"]},
            {"p02", "p03", "p04"},
        )

    def test_withdrawn_member_responsibilities_remain_visible(self):
        domain = load_domain(FIXTURE)
        page = member_homepage(domain, "m06")
        self.assertEqual(page["available_collaborations"], [])
        self.assertEqual({d["id"] for d in page["outstanding_dependencies"]}, {"c06"})
        actor = next(a for a in domain["actors"] if a["id"] == "m06")
        self.assertEqual(actor["status"], "withdrawn")
        commitments = {c["id"] for p in domain["proposals"] for c in p["commitments"]}
        self.assertTrue({"c03", "c06"} <= commitments)

    def test_lineage_events_keep_original_responsibility_traceable(self):
        domain = load_domain(FIXTURE)
        proposals = {p["id"]: p for p in domain["proposals"]}
        self.assertEqual((proposals["p03"]["parent"], proposals["p03"]["relation"]),
                         ("p01", "split"))
        self.assertEqual((proposals["p04"]["parent"], proposals["p04"]["relation"]),
                         ("p01", "continuation"))
        events = {(e["type"], e.get("actor")) for p in domain["proposals"] for e in p["events"]}
        self.assertIn(("withdrawal", "m06"), events)
        lead_changes = [e for p in domain["proposals"] for e in p["events"]
                        if e["type"] == "lead_change"]
        self.assertTrue(all("from" in e and "to" in e for e in lead_changes))

    def test_proposal_must_start_from_commitment(self):
        value = fresh()
        value["proposals"][2]["commitments"] = []
        with self.assertRaises(ValueError):
            validate_domain(value)

    def test_confirmed_activity_needs_local_evidence(self):
        value = fresh()
        value["activities"][1]["confirmed"] = True
        with self.assertRaises(ValueError):
            validate_domain(value)

    def test_same_activity_confirmed_only_once(self):
        value = fresh()
        duplicate = copy.deepcopy(value["activities"][0])
        duplicate["id"] = "a09"
        value["activities"].append(duplicate)
        with self.assertRaises(ValueError):
            validate_domain(value)

    def test_sensitive_data_stays_in_source_country(self):
        value = fresh()
        value["participant_data"][0]["stored_in"] = "DE"
        with self.assertRaises(ValueError):
            validate_domain(value)

    def test_public_figure_must_state_coverage(self):
        value = fresh()
        value["public_figures"][0]["coverage"] = " "
        with self.assertRaises(ValueError):
            validate_domain(value)

    def test_conflict_of_interest_blocks_funding_and_acceptance(self):
        value = fresh()
        value["reviews"].append({
            "id": "rv09",
            "proposal": "p03",
            "reviewer": "r01",
            "decision": "funding",
            "outcome": "pending",
        })
        with self.assertRaises(ValueError):
            validate_domain(value)


if __name__ == "__main__":
    unittest.main()
