"""读取并检查国际科普合作项目账的共享资料。

只依赖标准库。结构层面的字段约定见 contracts/domain.schema.json；
本模块额外执行业务不变量的检查：

- 提案以资源承诺为起点，且引用的会员必须存在
- 活动按去重键只确认一次，公开数字必须注明覆盖范围
- 敏感参与者数据留在来源国，不得跨境
- 有利益冲突的评审人不得参与资助与验收
- 拆分、退出、换人、跨年度续作的责任链保持可追溯
"""

import json
from collections import Counter
from pathlib import Path

REQUIRED_TOP_FIELDS = {
    "domain", "version", "sample_id", "organization", "actors", "facts",
    "constraints", "members", "reviewers", "proposals", "activities",
    "sensitive_datasets", "reviews",
}

STATUSES = ("intent", "active", "completed", "impact")
EVIDENCE_LOCAL = {
    "local_feedback_summary",
    "participant_work",
    "observation_record",
    "local_org_report",
}
PHOTO_PRESS = {"group_photo", "press_release"}


def load_domain(path: Path) -> dict:
    """读取字段完整且通过业务不变量检查的项目账资料。"""
    value = json.loads(path.read_text(encoding="utf-8"))
    errors = validate_domain(value)
    if errors:
        raise ValueError("共享资料未通过校验：" + "；".join(errors))
    return value


def validate_domain(value: dict) -> list[str]:
    """返回全部校验错误；为空列表表示资料可用。"""
    errors: list[str] = []

    missing = REQUIRED_TOP_FIELDS - value.keys()
    if missing:
        return [f"共享资料缺少必要字段：{sorted(missing)}"]

    if value["domain"] != "global-science-literacy":
        errors.append("domain 标识必须为 global-science-literacy")
    if not isinstance(value["version"], int) or value["version"] < 2:
        errors.append("version 必须为不小于 2 的整数")
    if len(value["actors"]) < 2 or len(value["facts"]) < 2:
        errors.append("actors 与 facts 至少各含 2 条")
    if len(value["constraints"]) < 6:
        errors.append("constraints 至少包含 6 条业务约束")

    org = value["organization"]
    if org["member_countries"] != 21:
        errors.append("组织会员来源国数应为 21")
    if org["founding_institutions"] < org["member_countries"]:
        errors.append("创始单位数不应少于会员来源国数")

    member_ids = {m["id"] for m in value["members"]}
    if len(member_ids) != len(value["members"]):
        errors.append("会员 id 不得重复")
    active_ids = {m["id"] for m in value["members"] if m["status"] == "active"}
    withdrawn = {m["id"] for m in value["members"] if m["status"] == "withdrawn"}
    for m in value["members"]:
        if m["status"] == "withdrawn" and not m.get("withdrawn_on"):
            errors.append(f"退出会员 {m['id']} 必须记录退出日期")

    reviewer_ids = {r["id"] for r in value["reviewers"]}
    conflicts = {
        r["id"]: set(r.get("conflicts_with_projects", []))
        for r in value["reviewers"]
    }

    proposals = value["proposals"]
    project_ids = {p["id"] for p in proposals}
    if len(project_ids) != len(proposals):
        errors.append("提案 id 不得重复")

    for p in proposals:
        pid = p["id"]
        errors.extend(_validate_proposal(p, member_ids, active_ids, withdrawn, project_ids))

    errors.extend(_validate_activities(value["activities"], project_ids, member_ids))
    errors.extend(_validate_datasets(value["sensitive_datasets"], proposals))
    errors.extend(_validate_reviews(value["reviews"], project_ids, reviewer_ids, conflicts))

    return errors


def _validate_proposal(p, member_ids, active_ids, withdrawn, project_ids) -> list[str]:
    errors: list[str] = []
    pid = p["id"]

    if p["status"] not in STATUSES:
        errors.append(f"{pid} 状态必须是 {STATUSES} 之一")

    lead = p["lead"]["member_id"]
    if lead not in member_ids:
        errors.append(f"{pid} 负责人引用了不存在的会员 {lead}")
    if lead in withdrawn:
        errors.append(f"{pid} 现任负责人不能是已退出会员 {lead}")

    for key in ("members",):
        for mid in p[key]:
            if mid not in member_ids:
                errors.append(f"{pid} 参与方引用了不存在的会员 {mid}")

    # 提案从“谁承诺提供什么资源”开始：至少一条承诺，承诺方须为真实会员。
    if not p["commitments"]:
        errors.append(f"{pid} 必须以至少一条资源承诺开始")
    for c in p["commitments"]:
        if c["member_id"] not in member_ids:
            errors.append(f"{pid} 承诺 {c.get('id')} 引用了不存在的会员")
        if not c.get("resource"):
            errors.append(f"{pid} 承诺 {c.get('id')} 缺少资源内容")

    budget = p.get("budget")
    if budget is not None:
        shares = [s["share"] for s in budget["funded_by"]]
        if any(s["member_id"] not in member_ids for s in budget["funded_by"]):
            errors.append(f"{pid} 预算出资方引用了不存在的会员")
        if shares and abs(sum(shares) - 1.0) > 1e-6:
            errors.append(f"{pid} 预算出资比例之和必须为 1")

    for d in p.get("dependencies", []):
        if d["member_id"] not in member_ids:
            errors.append(f"{pid} 依赖 {d['id']} 引用了不存在的会员")
    for o in p.get("collaboration_offers", []):
        if o["from_member"] not in member_ids:
            errors.append(f"{pid} 协作 {o['id']} 引用了不存在的会员")

    lineage = p["lineage"]
    if lineage["split_from"] and lineage["split_from"] not in project_ids:
        errors.append(f"{pid} 拆分来源提案不存在")
    if lineage["continuation_of"] and lineage["continuation_of"] not in project_ids:
        # 续作可能指向上一年度、不在本期账本内的编号；此时以 X- 前缀标识外部来源。
        if not lineage["continuation_of"].endswith("-2024"):
            errors.append(f"{pid} 跨年度续作来源不存在或未按历史编号登记")

    for rc in lineage["responsibility_chain"]:
        if rc["origin_member"] not in member_ids:
            errors.append(f"{pid} 责任链 {rc['id']} 的原始责任方不存在")
        if rc["current_holder"] not in member_ids:
            errors.append(f"{pid} 责任链 {rc['id']} 的当前承担方不存在")
        if not rc.get("traceable", False):
            errors.append(f"{pid} 责任链 {rc['id']} 必须保持可追溯")

    event_member_fields = {
        "member_withdrawal": ("member_id",),
        "lead_change": ("from_member", "to_member"),
        "split": (),
        "continuation": (),
    }
    for ev in lineage["events"]:
        for f in event_member_fields.get(ev["type"], ()):
            if ev.get(f) not in member_ids:
                errors.append(f"{pid} 谱系事件 {ev['id']} 引用了不存在的会员")
        if ev["type"] == "member_withdrawal" and ev.get("member_id") not in withdrawn:
            errors.append(f"{pid} 退出事件 {ev['id']} 的会员未登记为退出")

    rules = p["data_rules"]
    if rules["sensitive_participant_data"] and rules["cross_border_transfer"]:
        errors.append(f"{pid} 含敏感参与者数据时不得跨境转移")
    if rules["source_country"] != rules["residency_country"]:
        errors.append(f"{pid} 敏感数据来源国与留存国必须一致")

    impact = p.get("follow_on_impact")
    if p["status"] == "impact":
        if not impact or not impact.get("evidence"):
            errors.append(f"{pid} 标记为产生后续影响时必须附本地证据")
    if impact:
        for e in impact["evidence"]:
            if e["type"] in PHOTO_PRESS:
                errors.append(f"{pid} 合影或新闻稿不能作为后续影响的证据")

    return errors


def _validate_activities(activities, project_ids, member_ids) -> list[str]:
    errors: list[str] = []
    confirmed_keys: set[str] = set()
    key_counter = Counter(a["dedup_key"] for a in activities)

    for a in activities:
        aid = a["id"]
        if a["project_id"] not in project_ids:
            errors.append(f"{aid} 引用了不存在的提案")
        for r in a["reports"]:
            if r["member_id"] not in member_ids:
                errors.append(f"{aid} 报送方引用了不存在的会员")

        if not a["reports"]:
            errors.append(f"{aid} 至少保留一条会员报送记录")

        conf = a["confirmation"]
        if conf["confirmed"]:
            if a["dedup_key"] in confirmed_keys:
                errors.append(
                    f"dedup_key {a['dedup_key']} 对应同一场活动，只能确认一次（{aid} 重复确认）"
                )
            confirmed_keys.add(a["dedup_key"])

        for fig in a["public_figures"]:
            if not fig.get("coverage"):
                errors.append(f"{aid} 公开数字 {fig['label']} 必须注明覆盖范围")

        # 未确认（如重复报送）的活动不得再携带公开数字。
        if not conf["confirmed"] and a["public_figures"]:
            errors.append(f"{aid} 未确认活动不得发布统计数字")

        if conf["confirmed"]:
            if not any(e["type"] in EVIDENCE_LOCAL for e in a["evidence"]):
                errors.append(f"{aid} 已确认活动必须附当地证据，不能只有合影或新闻稿")

    for key, count in key_counter.items():
        confirmed = [a["id"] for a in activities
                     if a["dedup_key"] == key and a["confirmation"]["confirmed"]]
        if len(confirmed) > 1:
            errors.append(f"同一场活动 {key} 被确认 {len(confirmed)} 次：{confirmed}")

    return errors


def _validate_datasets(datasets, proposals) -> list[str]:
    errors: list[str] = []
    by_project = {p["id"]: p for p in proposals}
    for ds in datasets:
        p = by_project.get(ds["project_id"])
        if p is None:
            errors.append(f"{ds['id']} 引用了不存在的提案")
            continue
        if ds["cross_border"]:
            errors.append(f"{ds['id']} 敏感参与者数据不得跨境")
        if ds["stored_in_country"] != ds["source_country"]:
            errors.append(f"{ds['id']} 必须存储在来源国 {ds['source_country']}")
        if ds["source_country"] != p["data_rules"]["source_country"]:
            errors.append(f"{ds['id']} 来源国与提案数据规则不一致")
    return errors


def _validate_reviews(reviews, project_ids, reviewer_ids, conflicts) -> list[str]:
    errors: list[str] = []
    for rv in reviews:
        if rv["project_id"] not in project_ids:
            errors.append("评审记录引用了不存在的提案")
        if rv["reviewer_id"] not in reviewer_ids:
            errors.append("评审记录引用了不存在的评审人")
        if rv["project_id"] in conflicts.get(rv["reviewer_id"], set()):
            errors.append(
                f"评审人 {rv['reviewer_id']} 与 {rv['project_id']} 存在利益冲突，"
                f"不得参与{rv['stage']}"
            )
    return errors


# ---- 汇总视图 -----------------------------------------------------------

def member_homepage(value: dict, member_id: str) -> dict:
    """会员首页：自己尚未完成的依赖，以及可获得的协作。"""
    if not any(m["id"] == member_id for m in value["members"]):
        raise KeyError(f"未知会员 {member_id}")

    open_dependencies = []
    available_collaboration = []
    for p in value["proposals"]:
        for d in p.get("dependencies", []):
            if d["member_id"] == member_id and not d["fulfilled"]:
                open_dependencies.append({"project_id": p["id"], **d})
        if member_id in p["members"]:
            for o in p.get("collaboration_offers", []):
                if o["available"] and o["from_member"] != member_id:
                    available_collaboration.append({"project_id": p["id"], **o})
    return {
        "member_id": member_id,
        "open_dependencies": open_dependencies,
        "available_collaboration": available_collaboration,
    }


def secretariat_summary(value: dict) -> dict:
    """按意向、在办、完成、产生后续影响四种事实汇总，并统计已确认活动。"""
    buckets: dict[str, list[str]] = {s: [] for s in STATUSES}
    for p in value["proposals"]:
        buckets[p["status"]].append(p["id"])
    confirmed = {
        a["dedup_key"]
        for a in value["activities"]
        if a["confirmation"]["confirmed"]
    }
    return {
        "by_status": buckets,
        "counts": {s: len(buckets[s]) for s in STATUSES},
        "confirmed_activities": len(confirmed),
        "raw_activity_reports": sum(len(a["reports"]) for a in value["activities"]),
    }
