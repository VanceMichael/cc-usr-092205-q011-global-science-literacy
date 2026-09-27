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


def fixture_data() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class DomainTest(unittest.TestCase):
    def test_fixture_matches_domain(self):
        value = load_domain(FIXTURE)
        self.assertEqual(value["domain"], "global-science-literacy")
        self.assertGreaterEqual(len(value["constraints"]), 6)
        self.assertEqual(value["organization"]["member_countries"], 21)

    # ---- 提案从承诺开始 ------------------------------------------------

    def test_proposal_must_start_with_commitment(self):
        data = fixture_data()
        data["proposals"][0]["commitments"] = []
        errors = validate_domain(data)
        self.assertTrue(any("资源承诺" in e for e in errors), errors)

    def test_commitment_member_must_exist(self):
        data = fixture_data()
        data["proposals"][0]["commitments"][0]["member_id"] = "M-99"
        errors = validate_domain(data)
        self.assertTrue(any("不存在的会员" in e for e in errors), errors)

    def test_budget_shares_sum_to_one(self):
        data = fixture_data()
        data["proposals"][1]["budget"]["funded_by"][0]["share"] = 0.9
        errors = validate_domain(data)
        self.assertTrue(any("出资比例" in e for e in errors), errors)

    # ---- 活动去重与证据 ------------------------------------------------

    def test_same_activity_confirmed_only_once(self):
        data = fixture_data()
        # A-03 与 A-01 同 dedup_key；若把 A-03 也确认，应被拒绝。
        data["activities"][2]["confirmation"]["confirmed"] = True
        data["activities"][2]["evidence"].append(
            {"type": "local_org_report", "description": "补造证据"}
        )
        errors = validate_domain(data)
        self.assertTrue(any("只能确认一次" in e or "确认" in e for e in errors), errors)

    def test_group_photo_alone_is_not_local_evidence(self):
        data = fixture_data()
        a01 = next(a for a in data["activities"] if a["id"] == "A-01")
        a01["evidence"] = [
            {"type": "group_photo", "description": "只有合影"}
        ]
        errors = validate_domain(data)
        self.assertTrue(any("当地证据" in e for e in errors), errors)

    def test_public_figure_requires_coverage(self):
        data = fixture_data()
        data["activities"][0]["public_figures"][0]["coverage"] = ""
        errors = validate_domain(data)
        self.assertTrue(any("覆盖范围" in e for e in errors), errors)

    def test_unconfirmed_activity_has_no_public_figures(self):
        data = fixture_data()
        data["activities"][2]["public_figures"] = [
            {"label": "人数", "value": 10, "coverage": "x"}
        ]
        errors = validate_domain(data)
        self.assertTrue(any("未确认" in e for e in errors), errors)

    # ---- 数据留在来源国 ------------------------------------------------

    def test_sensitive_data_must_stay_in_source_country(self):
        data = fixture_data()
        data["sensitive_datasets"][0]["stored_in_country"] = "CN"
        errors = validate_domain(data)
        self.assertTrue(any("存储在来源国" in e for e in errors), errors)

    def test_cross_border_transfer_rejected(self):
        data = fixture_data()
        data["proposals"][0]["data_rules"]["cross_border_transfer"] = True
        errors = validate_domain(data)
        self.assertTrue(any("跨境" in e for e in errors), errors)

    # ---- 利益冲突回避 --------------------------------------------------

    def test_conflicted_reviewer_excluded_from_funding_and_acceptance(self):
        data = fixture_data()
        for stage in ("funding", "acceptance"):
            bad = fixture_data()
            bad["reviews"].append(
                {"project_id": "P-02", "stage": stage,
                 "reviewer_id": "R-01", "on": "2026-01-01"}
            )
            errors = validate_domain(bad)
            self.assertTrue(
                any("利益冲突" in e and stage in e for e in errors),
                (stage, errors),
            )

    # ---- 责任链：退出、换人、拆分、跨年 --------------------------------

    def test_withdrawn_member_cannot_lead_but_responsibility_remains(self):
        data = fixture_data()
        data["proposals"][1]["lead"]["member_id"] = "M-05"
        errors = validate_domain(data)
        self.assertTrue(any("已退出会员" in e for e in errors), errors)

    def test_withdrawal_event_member_must_be_registered(self):
        data = fixture_data()
        ev = next(e for e in data["proposals"][1]["lineage"]["events"]
                  if e["type"] == "member_withdrawal")
        ev["member_id"] = "M-01"
        errors = validate_domain(data)
        self.assertTrue(any("退出" in e for e in errors), errors)

    def test_responsibility_chain_must_stay_traceable(self):
        data = fixture_data()
        data["proposals"][0]["lineage"]["responsibility_chain"][0]["traceable"] = False
        errors = validate_domain(data)
        self.assertTrue(any("可追溯" in e for e in errors), errors)

    def test_split_source_must_exist(self):
        data = fixture_data()
        data["proposals"][2]["lineage"]["split_from"] = "P-99"
        errors = validate_domain(data)
        self.assertTrue(any("拆分来源" in e for e in errors), errors)

    # ---- 影响状态需要本地证据 ------------------------------------------

    def test_impact_status_requires_local_evidence(self):
        data = fixture_data()
        data["proposals"][1]["follow_on_impact"]["evidence"] = [
            {"type": "press_release", "description": "只有新闻稿"}
        ]
        errors = validate_domain(data)
        self.assertTrue(
            any("后续影响" in e or "合影或新闻稿" in e for e in errors),
            errors,
        )

    # ---- 会员首页与秘书处四态汇总 --------------------------------------

    def test_member_homepage_shows_open_dependencies_and_offers(self):
        value = load_domain(FIXTURE)
        page = member_homepage(value, "M-01")
        dep_ids = {d["id"] for d in page["open_dependencies"]}
        self.assertIn("D-01", dep_ids)
        # M-01 自身的承诺型依赖 D-02 属于 M-02，不应出现
        self.assertNotIn("D-02", dep_ids)

        page_m2 = member_homepage(value, "M-02")
        dep_ids_m2 = {d["id"] for d in page_m2["open_dependencies"]}
        self.assertIn("D-02", dep_ids_m2)
        # P-01 的协作来自 M-04，M-02 是参与方，可以看到
        offers = {o["id"] for o in page_m2["available_collaboration"]}
        self.assertIn("O-01", offers)

    def test_secretariat_summary_four_facts_and_dedup_count(self):
        value = load_domain(FIXTURE)
        summary = secretariat_summary(value)
        self.assertEqual(set(summary["by_status"]),
                         {"intent", "active", "completed", "impact"})
        self.assertEqual(summary["counts"]["intent"], 1)
        self.assertEqual(summary["counts"]["active"], 1)
        self.assertEqual(summary["counts"]["impact"], 1)
        # 3 条活动记录、4 次报送，但同一场活动只确认一次 -> 2 场
        self.assertEqual(summary["confirmed_activities"], 2)
        self.assertGreater(summary["raw_activity_reports"], summary["confirmed_activities"])

    def test_homepage_unknown_member_raises(self):
        value = load_domain(FIXTURE)
        with self.assertRaises(KeyError):
            member_homepage(value, "M-XX")


if __name__ == "__main__":
    unittest.main()
