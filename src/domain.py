"""读取并检查项目共享资料。"""

import json
from pathlib import Path

STATES = ("intent", "active", "completed", "impacting")
LOCAL_EVIDENCE_KINDS = ("local_record", "open_dataset")
EVENT_TYPES = ("split", "withdrawal", "lead_change", "continuation")
DECISIONS = ("funding", "acceptance")
ACTOR_KINDS = ("secretariat", "member", "reviewer")
ACTOR_STATUS = ("active", "withdrawn")
COMMITMENT_STATUS = ("pledged", "delivered")
ACTION_STATUS = ("open", "done")

REQUIRED_FIELDS = {
    "domain", "version", "sample_id", "actors", "proposals", "activities",
    "participant_data", "public_figures", "reviews", "facts", "constraints",
}


def load_domain(path: Path) -> dict:
    """读取字段完整且带版本的业务资料。"""
    value = json.loads(path.read_text(encoding="utf-8"))
    validate_domain(value)
    return value


def validate_domain(value: dict) -> None:
    """校验共享资料的必要字段与跨字段约束。"""
    missing = REQUIRED_FIELDS - value.keys()
    if missing:
        raise ValueError(f"共享资料缺少必要字段: {sorted(missing)}")
    if value["version"] < 2:
        raise ValueError("共享资料版本过低，需要 version >= 2")
    actors = _check_actors(value["actors"])
    proposals = _check_proposals(value["proposals"], actors)
    _check_activities(value["activities"], proposals, actors)
    _check_participant_data(value["participant_data"], value["activities"])
    _check_public_figures(value["public_figures"])
    _check_reviews(value["reviews"], proposals, actors)


def member_homepage(domain: dict, member_id: str) -> dict:
    """会员首页：本人尚未完成的依赖与可获得的协作。"""
    members = {a["id"]: a for a in domain["actors"] if a["kind"] == "member"}
    if member_id not in members:
        raise ValueError(f"未知会员: {member_id}")
    outstanding = []
    for proposal in domain["proposals"]:
        for commitment in proposal["commitments"]:
            if commitment["member"] == member_id and commitment["status"] == "pledged":
                outstanding.append({
                    "type": "commitment",
                    "proposal": proposal["id"],
                    "id": commitment["id"],
                    "detail": commitment["resource"]["detail"],
                })
        for action in proposal["sustained_actions"]:
            if action["owner"] == member_id and action["status"] == "open":
                outstanding.append({
                    "type": "sustained_action",
                    "proposal": proposal["id"],
                    "id": action["id"],
                    "detail": action["action"],
                    "due": action["due"],
                })
    available = []
    if members[member_id]["status"] == "active":
        for proposal in domain["proposals"]:
            committed = {c["member"] for c in proposal["commitments"]}
            if proposal["state"] in ("intent", "active") and member_id not in committed:
                available.append({
                    "proposal": proposal["id"],
                    "title": proposal["title"],
                    "state": proposal["state"],
                    "lead": proposal["lead"],
                })
    return {
        "member": member_id,
        "outstanding_dependencies": outstanding,
        "available_collaborations": available,
    }


def secretariat_summary(domain: dict) -> dict:
    """秘书处汇总：按意向、在办、完成、产生后续影响四种事实统计。"""
    summary = {state: 0 for state in STATES}
    for proposal in domain["proposals"]:
        summary[proposal["state"]] += 1
    confirmed = [a for a in domain["activities"] if a["confirmed"]]
    summary["confirmed_activities"] = len({a["dedup_key"] for a in confirmed})
    summary["activity_reports"] = sum(len(a["reports"]) for a in domain["activities"])
    return summary


def _require(record: dict, keys, where: str) -> None:
    missing = [key for key in keys if key not in record]
    if missing:
        raise ValueError(f"{where}缺少字段: {sorted(missing)}")


def _check_actors(actors: list) -> dict:
    by_id = {}
    for actor in actors:
        _require(actor, ["id", "kind", "name", "country", "status"], "参与方")
        if actor["id"] in by_id:
            raise ValueError(f"参与方标识重复: {actor['id']}")
        if actor["kind"] not in ACTOR_KINDS or actor["status"] not in ACTOR_STATUS:
            raise ValueError(f"参与方类型或状态无效: {actor['id']}")
        by_id[actor["id"]] = actor
    members = [a for a in actors if a["kind"] == "member"]
    if len(members) < 2 or not any(a["kind"] == "secretariat" for a in actors):
        raise ValueError("参与方构成不完整")
    return by_id


def _check_proposals(proposals: list, actors: dict) -> dict:
    by_id = {}
    for proposal in proposals:
        where = f"提案{proposal.get('id', '?')}"
        _require(proposal, ["id", "title", "state", "lead", "commitments", "budget",
                            "translations", "scope", "open_materials", "local_feedback",
                            "sustained_actions", "events"], where)
        if proposal["id"] in by_id:
            raise ValueError(f"提案标识重复: {proposal['id']}")
        if proposal["state"] not in STATES:
            raise ValueError(f"{where}状态无效: {proposal['state']}")
        lead = actors.get(proposal["lead"])
        if lead is None or lead["kind"] != "member":
            raise ValueError(f"{where}负责人必须是会员: {proposal['lead']}")
        if not proposal["commitments"]:
            raise ValueError(f"{where}未以承诺为起点")
        commitment_ids = set()
        for commitment in proposal["commitments"]:
            _require(commitment, ["id", "member", "resource", "status"], f"{where}承诺")
            _require(commitment["resource"], ["kind", "detail"], f"{where}承诺资源")
            member = actors.get(commitment["member"])
            if member is None or member["kind"] != "member":
                raise ValueError(f"{where}承诺方必须是会员: {commitment['member']}")
            if commitment["status"] not in COMMITMENT_STATUS:
                raise ValueError(f"{where}承诺状态无效: {commitment['status']}")
            commitment_ids.add(commitment["id"])
        for item in proposal["budget"]:
            _require(item, ["item", "amount", "currency", "funded_by"], f"{where}预算")
            if item["funded_by"] not in commitment_ids:
                raise ValueError(f"{where}预算来源不是本提案承诺: {item['funded_by']}")
        for action in proposal["sustained_actions"]:
            _require(action, ["id", "owner", "action", "due", "status"], f"{where}持续行动")
            owner = actors.get(action["owner"])
            if owner is None or owner["kind"] != "member":
                raise ValueError(f"{where}持续行动负责人必须是会员: {action['owner']}")
            if action["status"] not in ACTION_STATUS:
                raise ValueError(f"{where}持续行动状态无效: {action['status']}")
        for event in proposal["events"]:
            _require(event, ["type", "date", "note"], f"{where}事件")
            if event["type"] not in EVENT_TYPES:
                raise ValueError(f"{where}事件类型无效: {event['type']}")
        if "parent" in proposal and proposal.get("relation") not in ("split", "continuation"):
            raise ValueError(f"{where}拆分或续作须注明关系类型")
        by_id[proposal["id"]] = proposal
    for proposal in proposals:
        if "parent" in proposal and proposal["parent"] not in by_id:
            raise ValueError(f"提案{proposal['id']}的来源提案不存在: {proposal['parent']}")
    return by_id


def _check_activities(activities: list, proposals: dict, actors: dict) -> None:
    confirmed_keys = set()
    for activity in activities:
        where = f"活动{activity.get('id', '?')}"
        _require(activity, ["id", "proposal", "dedup_key", "date", "country",
                            "reports", "confirmed", "evidence"], where)
        if activity["proposal"] not in proposals:
            raise ValueError(f"{where}所属提案不存在: {activity['proposal']}")
        for report in activity["reports"]:
            _require(report, ["member", "reported_at"], f"{where}报送")
            member = actors.get(report["member"])
            if member is None or member["kind"] != "member":
                raise ValueError(f"{where}报送方必须是会员: {report['member']}")
        has_local = any(e.get("kind") in LOCAL_EVIDENCE_KINDS for e in activity["evidence"])
        if activity["confirmed"]:
            if not has_local:
                raise ValueError(f"{where}缺少当地证据，合影或新闻稿不能替代")
            if activity["dedup_key"] in confirmed_keys:
                raise ValueError(f"{where}与已确认活动重复，同一活动只确认一次")
            confirmed_keys.add(activity["dedup_key"])


def _check_participant_data(records: list, activities: list) -> None:
    activity_ids = {a["id"] for a in activities}
    for record in records:
        where = f"参与者数据{record.get('id', '?')}"
        _require(record, ["id", "activity", "sensitivity", "source_country", "stored_in"], where)
        if record["activity"] not in activity_ids:
            raise ValueError(f"{where}所属活动不存在: {record['activity']}")
        if record["sensitivity"] == "sensitive" and record["stored_in"] != record["source_country"]:
            raise ValueError(f"{where}须留在来源国{record['source_country']}")


def _check_public_figures(figures: list) -> None:
    for figure in figures:
        where = f"公开数字{figure.get('id', '?')}"
        _require(figure, ["id", "metric", "value", "coverage", "period"], where)
        if not str(figure["coverage"]).strip():
            raise ValueError(f"{where}须注明覆盖范围")


def _check_reviews(reviews: list, proposals: dict, actors: dict) -> None:
    for review in reviews:
        where = f"评审{review.get('id', '?')}"
        _require(review, ["id", "proposal", "reviewer", "decision", "outcome"], where)
        if review["proposal"] not in proposals:
            raise ValueError(f"{where}评审对象不存在: {review['proposal']}")
        reviewer = actors.get(review["reviewer"])
        if reviewer is None or reviewer["kind"] != "reviewer":
            raise ValueError(f"{where}评审人无效: {review['reviewer']}")
        if review["decision"] not in DECISIONS:
            raise ValueError(f"{where}评审环节无效: {review['decision']}")
        if review["proposal"] in reviewer.get("conflicts_of_interest", []):
            raise ValueError(f"{where}存在利益冲突，不得参与资助和验收")
