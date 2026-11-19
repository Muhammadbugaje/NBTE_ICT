"""v2.9: the ES is no longer blind at stage 16 — before he decides he sees every
earlier decision and the full brief (reports, the Directorate's
recommendation, every CQAC member's vote and comment by name, the tally, the
Chairman's decision and remark, every remark). The Chairman keeps his blind
rule but reads the panel's named votes in the same brief."""
from qa_core.tests.phase4_fixtures import advance_to, holder, staff_client


def test_es_sees_every_earlier_decision_before_deciding(p4):
    from accreditation import decisions as dec
    from accreditation.models import Decision

    case = advance_to(p4, 15)
    chair = holder(case, "cqac_chair_approval")
    # The Chairman: still blind on the panel's recommendation, but sees the named votes.
    assert not dec.reveal_earlier(case, Decision.CHAIR, chair)
    chair_brief = dec.decision_brief(case, chair, Decision.CHAIR)
    assert chair_brief is not None and not chair_brief["for_es"] and chair_brief["earlier_hidden"]
    first = chair_brief["programmes"][0]
    assert first["cqac_votes"] and first["cqac_votes"][0]["member_name"] and first["tally"]
    assert first["cqac_decision"] is None and first["chair"] is None and first["es"] is None
    assert dec.decision_brief(case, p4.people.director, Decision.CHAIR) is None

    case = advance_to(p4, 16, case)
    assert case.status == "es_final_approval"
    es = holder(case, "es_final_approval")
    assert not Decision.unscoped.filter(case=case, stage=Decision.ES).exists()
    # Not blind: earlier decisions are visible before he has saved anything.
    assert dec.reveal_earlier(case, Decision.ES, es)
    rows = dec.board(case, "es_final_approval", es)
    assert rows and all(not r["earlier_hidden"] for r in rows)
    assert {h.stage for h in rows[0]["history"]} == {Decision.CQAC, Decision.CHAIR}
    assert rows[0]["start"]["outcome"] == ""  # his own form still starts empty

    brief = dec.decision_brief(case, es, Decision.ES)
    assert brief["for_es"] and not brief["earlier_hidden"]
    assert len(brief["programmes"]) == len(dec.rows(case))
    for item in brief["programmes"]:
        assert item["cqac_votes"], "every CQAC vote is listed"
        vote = item["cqac_votes"][0]
        assert vote["member"] is not None and vote["comment"] == "Demo vote" and vote["outcome_label"]
        assert item["tally"]["total"] == len(item["cqac_votes"])
        assert item["cqac_decision"]["stage"] == Decision.CQAC
        assert item["chair"]["stage"] == Decision.CHAIR and item["chair"]["remark"] == "Demo decision"
        assert item["chair"]["by"] is not None and item["es"] is None
    assert isinstance(brief["timeline_remarks"], list)
    assert dec.decision_brief(case, p4.people.cqac_member, Decision.ES) is None

    r = staff_client(es).get(f"/qa/cases/{case.pk}/?tab=decisions")
    assert r.status_code == 200
    assert r.context["es_brief"] is not None and not r.context.get("earlier_hidden")
    stages = {d.stage for d in r.context["all_decisions"]}
    assert {Decision.CQAC, Decision.CHAIR} <= stages
