"""Tests for the rules the model is deliberately not trusted with.

Everything asserted here is decided in Python, not by the language model: which
feedback escalates, who may be asked for a public review, how often a segment may
be contacted, and which service a draft is allowed to talk about. Those are the
claims the project report makes, so they are the ones worth pinning down.

Runs standalone with no test framework installed:

    python test_rules.py

It also works under pytest if that is available:

    python -m pytest test_rules.py
"""

from __future__ import annotations

import re
import shutil
import tempfile
import warnings
from datetime import date, datetime, timedelta
from pathlib import Path

warnings.filterwarnings("ignore")

from mukta import config, data, prompts, services
from mukta.gemini import parse_json_response

CLIENTS = data.load_clients()
FEEDBACK = data.load_feedback()
BY_ID = {c.client_id: c for c in CLIENTS}


def _client(**overrides) -> data.Client:
    base = dict(
        client_id="T001", name="Test Firm", contact_person="Test Owner",
        phone="+91 90000 00000", segment="New",
        primary_service=config.SERVICES[0], jobs_completed=1,
        last_job_date=date.today(), city="Ahmedabad", notes="",
    )
    base.update(overrides)
    return data.Client(**base)


def _feedback(**overrides) -> data.Feedback:
    base = dict(
        feedback_id="TF1", client_id="T001", received_at=datetime.now(),
        rating=5, nps_score=9, comment="Fine.", service=config.SERVICES[0],
        resolved=False,
    )
    base.update(overrides)
    return data.Feedback(**base)


# --- Escalation ------------------------------------------------------------

def test_escalation_triggers_at_and_below_threshold():
    for rating in (1, 2, 3):
        assert _feedback(rating=rating).needs_escalation, f"{rating}/5 should escalate"
    for rating in (4, 5):
        assert not _feedback(rating=rating).needs_escalation, f"{rating}/5 should not"


def test_resolved_feedback_leaves_the_queue():
    assert not _feedback(rating=1, resolved=True).needs_escalation


def test_sla_breach_only_after_the_window():
    inside = _feedback(rating=2, received_at=datetime.now() - timedelta(hours=23))
    outside = _feedback(rating=2, received_at=datetime.now() - timedelta(hours=25))
    assert not inside.breaches_sla
    assert outside.breaches_sla


def test_future_timestamp_does_not_produce_negative_hours():
    assert _feedback(received_at=datetime.now() + timedelta(hours=3)).hours_open == 0.0


def test_queue_is_ordered_worst_first():
    ratings = [f.rating for f in data.escalations(FEEDBACK)]
    assert ratings == sorted(ratings), "escalations must surface lowest rating first"


# --- Review-ask gate -------------------------------------------------------

def test_open_complaint_blocks_the_review_ask():
    """The whole point of the gate: never invite a public review mid-complaint."""
    verdict = services.review_eligibility(BY_ID["C010"], FEEDBACK)  # Urban Threads, 1/5
    assert not verdict.eligible
    assert "unresolved" in verdict.reason.lower()


def test_happy_client_may_be_asked():
    verdict = services.review_eligibility(BY_ID["C001"], FEEDBACK)  # 5/5, nothing open
    assert verdict.eligible


def test_absent_feedback_is_not_treated_as_satisfaction():
    verdict = services.review_eligibility(BY_ID["C007"], FEEDBACK)  # no feedback rows
    assert not verdict.eligible


def test_low_but_resolved_ratings_still_fail_the_bar():
    client = _client(client_id="T900")
    rows = [_feedback(client_id="T900", rating=3, resolved=True)]
    assert not services.review_eligibility(client, rows).eligible


def test_blocked_gate_keeps_the_review_clause_out_of_the_prompt():
    """Enforced in code, so the prompt never even offers the option."""
    seen = {}

    class Stub:
        enabled = True

        def generate(self, prompt, temperature=None):
            seen["prompt"] = prompt
            return "Hi Ritika, how did it go? [feedback form link]"

    result = services.generate_outreach(
        Stub(), BY_ID["C010"], config.SERVICES[3],
        request_review_ask=True, feedback=FEEDBACK,
    )
    assert result.review_ask is None
    assert result.blocked_reason
    assert "Google review" not in seen["prompt"]


# --- Contact cadence -------------------------------------------------------

def test_regular_clients_are_not_messaged_per_job():
    regular = _client(segment="Regular")
    recent = services.cooldown_status(regular, datetime.now() - timedelta(days=5))
    assert recent.within_cooldown


def test_cooldown_lapses_after_the_window():
    regular = _client(segment="Regular")
    old = services.cooldown_status(regular, datetime.now() - timedelta(days=200))
    assert not old.within_cooldown


def test_new_clients_are_exempt_from_cooldown():
    new = _client(segment="New")
    assert not services.cooldown_status(new, datetime.now() - timedelta(days=1)).within_cooldown


def test_never_contacted_client_is_not_blocked():
    assert not services.cooldown_status(_client(), None).within_cooldown


# --- Service isolation -----------------------------------------------------

SERVICE_MARKERS = {
    "Auto-Rickshaw Hood Branding": ["rickshaw", "hood"],
    "Hoarding / Billboard Advertising": ["hoarding", "billboard"],
    "Transit (Bus) Advertising": ["bus", "transit", "route"],
    "Digital OOH Display": ["screen", "digital", "display"],
}


def test_library_never_mixes_service_lines():
    for service, entries in config.SERVICE_MESSAGES.items():
        foreign = [w for s, ws in SERVICE_MARKERS.items() if s != service for w in ws]
        for entry in entries:
            for word in foreign:
                assert not re.search(rf"\b{word}", entry["text"], re.I), (
                    f"{service} text mentions '{word}': {entry['text']}"
                )


def test_library_covers_every_service_and_segment():
    for service in config.SERVICES:
        entries = config.SERVICE_MESSAGES[service]
        assert len(entries) == 12, f"{service} has {len(entries)} texts, expected 12"
        for segment in config.SEGMENTS:
            assert config.messages_for(service, segment), f"{service}/{segment} empty"


def test_prompt_examples_match_the_selected_service():
    """The fix for drafts drifting into another service's language."""
    for service in config.SERVICES:
        prompt = prompts.outreach_message_prompt(BY_ID["C001"], service)
        block = prompt[prompt.index("APPROVED"):prompt.index("RULES")]
        foreign = [w for s, ws in SERVICE_MARKERS.items() if s != service for w in ws]
        for word in foreign:
            assert not re.search(rf"\b{word}", block, re.I), (
                f"{service} prompt shows a '{word}' example"
            )


def test_account_note_is_marked_as_background_not_current_campaign():
    """C001's note mentions rickshaws; a hoarding draft must not follow it."""
    prompt = prompts.outreach_message_prompt(
        BY_ID["C001"], "Hoarding / Billboard Advertising"
    )
    assert "historical background only" in prompt
    assert "authoritative fact" in prompt


# --- Owner addressing ------------------------------------------------------

def test_owner_override_reaches_the_prompt():
    prompt = prompts.outreach_message_prompt(
        BY_ID["C009"], config.SERVICES[0], owner_name="Nilesh Trivedi"
    )
    assert 'greet them as "Nilesh"' in prompt


def test_blank_owner_falls_back_to_the_record():
    prompt = prompts.outreach_message_prompt(
        BY_ID["C009"], config.SERVICES[0], owner_name="   "
    )
    assert 'greet them as "Hetal"' in prompt


def test_firm_name_is_marked_as_not_a_person():
    prompt = prompts.outreach_message_prompt(BY_ID["C009"], config.SERVICES[0])
    assert "NOT a person" in prompt


# --- Language and variants -------------------------------------------------

def test_language_instruction_is_carried_into_the_prompt():
    prompt = prompts.outreach_message_prompt(
        BY_ID["C001"], config.SERVICES[0], language="Gujarati"
    )
    assert "Gujarati script" in prompt


def test_variant_count_is_clamped_to_the_configured_maximum():
    prompt = prompts.outreach_message_prompt(
        BY_ID["C001"], config.SERVICES[0], variant_count=99
    )
    assert f"exactly {config.MAX_VARIANTS} alternative versions" in prompt


def test_variants_are_split_apart():
    class Stub:
        enabled = True

        def generate(self, prompt, temperature=None):
            return f"First draft.\n{config.VARIANT_SEPARATOR}\nSecond draft."

    result = services.generate_outreach(
        Stub(), BY_ID["C001"], config.SERVICES[0],
        request_review_ask=False, variant_count=2,
    )
    assert result.variants == ["First draft.", "Second draft."]
    assert result.message == "First draft."


# --- Model output handling -------------------------------------------------

def test_json_survives_fences_and_wrapper_objects():
    assert parse_json_response('[{"a": 1}]') == [{"a": 1}]
    assert parse_json_response('```json\n[{"a": 1}]\n```') == [{"a": 1}]
    assert parse_json_response('{"results": [{"a": 1}]}') == [{"a": 1}]


def test_triage_reports_rather_than_silently_doing_nothing():
    class Stub:
        enabled = True

        def __init__(self, payload):
            self.payload = payload

        def generate_json(self, prompt, temperature=None):
            return self.payload

    _, warning = services.triage_feedback(Stub([{"feedback_id": "ZZZ"}]), FEEDBACK, CLIENTS)
    assert warning and "none carried a feedback_id" in warning

    _, warning = services.triage_feedback(Stub({"nope": 1}), FEEDBACK, CLIENTS)
    assert warning and "where a list" in warning


def test_rule_based_queue_survives_a_model_outage():
    class Down:
        enabled = True

        def generate_json(self, prompt, temperature=None):
            from mukta.gemini import GeminiError
            raise GeminiError("network down")

    triaged, warning = services.triage_feedback(Down(), FEEDBACK, CLIENTS)
    assert warning
    escalating = [t for t in triaged if t.needs_escalation]
    assert len(escalating) == len(data.escalations(FEEDBACK))


# --- Writeback (against copies, never the real CSVs) -----------------------

def test_resolving_removes_a_row_from_the_queue():
    tmp = Path(tempfile.mkdtemp())
    try:
        path = tmp / "feedback.csv"
        shutil.copy(config.FEEDBACK_CSV, path)
        before = len(data.escalations(data.load_feedback(path)))
        assert data.mark_resolved("F007", path)
        after = len(data.escalations(data.load_feedback(path)))
        assert after == before - 1
        assert not data.mark_resolved("MISSING", path)
    finally:
        shutil.rmtree(tmp)


def test_added_feedback_gets_a_fresh_id_and_escalates_when_low():
    tmp = Path(tempfile.mkdtemp())
    try:
        path = tmp / "feedback.csv"
        shutil.copy(config.FEEDBACK_CSV, path)
        new_id = data.append_feedback("C003", 2, "Panel came loose.", config.SERVICES[2], path=path)
        rows = data.load_feedback(path)
        added = next(f for f in rows if f.feedback_id == new_id)
        assert new_id not in {"F001", "F010"}
        assert added.needs_escalation
    finally:
        shutil.rmtree(tmp)


def test_outreach_log_round_trips():
    tmp = Path(tempfile.mkdtemp())
    try:
        path = tmp / "outreach_log.csv"
        assert data.load_outreach_log(path).empty
        assert data.last_contacted(data.load_outreach_log(path), "C001") is None
        data.log_outreach(BY_ID["C001"], "Rajesh", config.SERVICES[0], "English", "hi", path=path)
        log = data.load_outreach_log(path)
        assert len(log) == 1
        assert data.last_contacted(log, "C001") is not None
        assert data.last_contacted(log, "C999") is None
    finally:
        shutil.rmtree(tmp)


# --- Reporting -------------------------------------------------------------

def test_service_breakdown_is_worst_first():
    stats = services.service_breakdown(FEEDBACK)
    averages = [s.average_rating for s in stats]
    assert averages == sorted(averages)
    assert sum(s.count for s in stats) == len(FEEDBACK)


def test_stats_hold_together():
    stats = services.compute_stats(FEEDBACK)
    assert stats.total == len(FEEDBACK)
    assert stats.escalations == len(data.escalations(FEEDBACK))
    assert stats.overdue <= stats.escalations
    assert 1 <= stats.average_rating <= 5


def test_empty_feedback_does_not_divide_by_zero():
    stats = services.compute_stats([])
    assert stats.total == 0 and stats.average_rating == 0.0


# --- Trends ----------------------------------------------------------------

def test_history_spans_several_months():
    months = services.monthly_trend(FEEDBACK)
    assert len(months) >= 6, "need real history for a trend to mean anything"
    assert [m.month for m in months] == sorted(m.month for m in months)


def test_monthly_counts_account_for_every_row():
    assert sum(m.count for m in services.monthly_trend(FEEDBACK)) == len(FEEDBACK)


def test_every_service_line_has_a_trend_series():
    trend = services.service_trend(FEEDBACK)
    for service in config.SERVICES:
        assert service in trend, f"{service} has no history to trend"
        assert len(trend[service]) >= 2, f"{service} has too few months"


def test_headline_direction_matches_the_numbers():
    months = services.monthly_trend(FEEDBACK)
    delta = months[-1].average_rating - months[0].average_rating
    headline = services.trend_headline(FEEDBACK)
    assert ("down" if delta < -0.1 else "up" if delta > 0.1 else "flat") in headline
    assert "+-" not in headline and "down +" not in headline


def test_headline_is_honest_about_thin_history():
    single = [_feedback(received_at=datetime.now())]
    assert "Not enough history" in services.trend_headline(single)


def test_trend_table_names_every_month_and_service():
    table = services.trend_table(FEEDBACK)
    for month in services.monthly_trend(FEEDBACK):
        assert month.label in table
    for service in config.SERVICES:
        assert service in table


def test_themes_prompt_carries_the_trend_table():
    prompt = prompts.themes_prompt(
        FEEDBACK[:5], {c.client_id: c.name for c in CLIENTS},
        trend_table=services.trend_table(FEEDBACK),
    )
    assert "HOW RATINGS HAVE MOVED" in prompt
    assert "Ground every trend claim" in prompt


def test_themes_call_is_capped_and_sends_recent_rows():
    seen = {}

    class Stub:
        enabled = True

        def generate(self, prompt, temperature=None):
            seen["prompt"] = prompt
            return "ok"

    services.summarize_themes(Stub(), FEEDBACK, CLIENTS)
    quoted = seen["prompt"].count(chr(10) + "- ")
    assert quoted <= config.MAX_THEME_ITEMS, f"{quoted} rows quoted, cap is {config.MAX_THEME_ITEMS}"
    assert "HOW RATINGS HAVE MOVED" in seen["prompt"]


def test_every_client_has_a_callback_number():
    for client in CLIENTS:
        assert client.phone.strip(), f"{client.name} has no phone number"


def test_log_shows_a_number_for_every_row():
    frame = data.feedback_to_frame(FEEDBACK, CLIENTS)
    assert "Phone" in frame.columns
    assert frame["Phone"].astype(str).str.strip().ne("").all()


# --- Net Promoter Score ----------------------------------------------------

def test_nps_bands_use_the_standard_cutoffs():
    assert config.nps_band(10) == "Promoter"
    assert config.nps_band(9) == "Promoter"
    assert config.nps_band(8) == "Passive"
    assert config.nps_band(7) == "Passive"
    assert config.nps_band(6) == "Detractor"
    assert config.nps_band(0) == "Detractor"
    assert config.nps_band(None) is None


def test_nps_is_promoter_share_minus_detractor_share():
    rows = (
        [_feedback(nps_score=10) for _ in range(5)]     # promoters
        + [_feedback(nps_score=8) for _ in range(3)]    # passives
        + [_feedback(nps_score=2) for _ in range(2)]    # detractors
    )
    stats = services.nps_summary(rows)
    assert (stats.promoters, stats.passives, stats.detractors) == (5, 3, 2)
    assert stats.score == 30      # 50% promoters - 20% detractors


def test_nps_ignores_rows_with_no_score():
    rows = [_feedback(nps_score=10), _feedback(nps_score=None)]
    assert services.nps_summary(rows).responses == 1


def test_nps_of_empty_set_is_zero_not_an_error():
    assert services.nps_summary([]).score == 0


def test_every_sample_row_carries_an_nps_score():
    assert all(f.nps_score is not None for f in FEEDBACK)
    assert all(0 <= f.nps_score <= config.NPS_SCALE_MAX for f in FEEDBACK)


def test_nps_trend_covers_the_same_months_as_the_rating_trend():
    assert len(services.nps_trend(FEEDBACK)) == len(services.monthly_trend(FEEDBACK))


# --- Churn early warning ---------------------------------------------------

def test_falling_ratings_raise_the_risk_score():
    client = _client(client_id="T500", segment="Regular")
    good = [_feedback(client_id="T500", rating=5, nps_score=10,
                      received_at=datetime.now() - timedelta(days=d))
            for d in (200, 190, 180)]
    bad = [_feedback(client_id="T500", rating=2, nps_score=2,
                     received_at=datetime.now() - timedelta(days=d))
           for d in (30, 20, 10)]
    risk = services.churn_risk([client], good + bad)[0]
    assert risk.score > 0
    assert risk.trend_delta is not None and risk.trend_delta < 0
    assert any("Rating fell" in r for r in risk.reasons)


def test_steady_happy_client_is_not_flagged():
    client = _client(client_id="T501", segment="Regular")
    rows = [_feedback(client_id="T501", rating=5, nps_score=10,
                      received_at=datetime.now() - timedelta(days=d))
            for d in (120, 90, 60, 30, 10)]
    assert services.churn_risk([client], rows)[0].band == "None"


def test_too_few_responses_is_not_read_as_a_trend():
    client = _client(client_id="T502", segment="Regular")
    rows = [_feedback(client_id="T502", rating=5, nps_score=10),
            _feedback(client_id="T502", rating=1, nps_score=0)]
    risk = services.churn_risk([client], rows)[0]
    assert risk.trend_delta is None, "two responses is noise, not a trend"


def test_silence_counts_against_a_regular_but_not_an_obsolete_client():
    quiet = date.today() - timedelta(days=400)
    regular = _client(client_id="T503", segment="Regular", last_job_date=quiet)
    obsolete = _client(client_id="T504", segment="Obsolete", last_job_date=quiet)
    by_id = {r.client.client_id: r for r in services.churn_risk([regular, obsolete], [])}
    assert any("No work for" in r for r in by_id["T503"].reasons)
    assert not any("No work for" in r for r in by_id["T504"].reasons)


def test_risk_list_is_ordered_worst_first():
    scores = [r.score for r in services.churn_risk(CLIENTS, FEEDBACK)]
    assert scores == sorted(scores, reverse=True)


def test_sample_book_surfaces_a_high_risk_client():
    """Shreeji Foods: ratings falling and no work for months."""
    risks = {r.client.name: r for r in services.churn_risk(CLIENTS, FEEDBACK)}
    assert risks["Shreeji Foods"].band == "High"


# --- Closed-loop recovery --------------------------------------------------

def test_recovery_reads_what_the_client_said_next():
    client = _client(client_id="T600")
    rows = [
        _feedback(client_id="T600", rating=2, nps_score=2, resolved=True,
                  received_at=datetime.now() - timedelta(days=30)),
        _feedback(client_id="T600", rating=5, nps_score=10,
                  received_at=datetime.now() - timedelta(days=20)),
        _feedback(client_id="T600", rating=5, nps_score=9,
                  received_at=datetime.now() - timedelta(days=10)),
    ]
    outcomes = services.recovery_outcomes([client], rows)
    assert len(outcomes) == 1
    assert outcomes[0].recovered
    assert services.recovery_rate(outcomes) == 100.0


def test_a_complaint_that_did_not_recover_is_reported_as_such():
    client = _client(client_id="T601")
    rows = [
        _feedback(client_id="T601", rating=2, nps_score=1, resolved=True,
                  received_at=datetime.now() - timedelta(days=30)),
        _feedback(client_id="T601", rating=2, nps_score=1,
                  received_at=datetime.now() - timedelta(days=10)),
    ]
    assert not services.recovery_outcomes([client], rows)[0].recovered


def test_unresolved_complaints_are_not_counted_as_recovery():
    client = _client(client_id="T602")
    rows = [
        _feedback(client_id="T602", rating=2, nps_score=1, resolved=False,
                  received_at=datetime.now() - timedelta(days=30)),
        _feedback(client_id="T602", rating=5, nps_score=10,
                  received_at=datetime.now() - timedelta(days=10)),
    ]
    assert services.recovery_outcomes([client], rows) == []


def test_recovery_outcomes_are_chronological():
    outcomes = services.recovery_outcomes(CLIENTS, FEEDBACK)
    dates = [o.complaint_on for o in outcomes]
    assert dates == sorted(dates)


# --- Issue frequency -------------------------------------------------------

def test_issue_rates_are_normalised_by_monthly_volume():
    """A busier month must not read as a worse month."""
    quiet = [_feedback(comment="Installation was late.",
                       received_at=datetime(2026, 1, 5))]
    busy = [_feedback(comment="Installation was late.",
                      received_at=datetime(2026, 2, 5))]
    busy += [_feedback(comment="All good.", received_at=datetime(2026, 2, 6))
             for _ in range(9)]
    series = services.issue_trends(quiet + busy)[0]
    assert series.counts == [1, 1]
    assert series.per_100 == [100.0, 10.0], "same count, very different rate"


def test_issue_trends_are_ordered_by_volume():
    totals = [s.total for s in services.issue_trends(FEEDBACK)]
    assert totals == sorted(totals, reverse=True)


def test_issues_nobody_mentioned_are_left_out():
    series = services.issue_trends([_feedback(comment="All good, thanks.")])
    assert series == []


def test_direction_is_one_of_the_known_verdicts():
    for s in services.issue_trends(FEEDBACK):
        assert s.direction in {"improving", "worsening", "steady", "too little history"}


# --- WhatsApp click-to-chat -------------------------------------------------

def test_whatsapp_link_strips_formatting_from_the_phone_number():
    link = services.whatsapp_link("+91 94244 31894", "hi")
    assert link.startswith("https://wa.me/919424431894?text=")


def test_whatsapp_link_url_encodes_the_message():
    link = services.whatsapp_link("+91 94244 31894", "Hi Rajesh, how did it go? [link]")
    assert " " not in link and "," not in link and "?" not in link.split("?text=")[1]
    assert "%20" in link


def test_whatsapp_link_encodes_newlines_for_a_multi_line_draft():
    link = services.whatsapp_link("+91 94244 31894", "Line one\nLine two")
    assert "%0A" in link


def test_whatsapp_link_is_empty_without_a_phone_number():
    assert services.whatsapp_link("", "hi") == ""


def test_whatsapp_link_is_empty_without_a_message():
    assert services.whatsapp_link("+91 94244 31894", "   ") == ""


def test_every_sample_client_can_build_a_link():
    """The prototype shares one real test number across all clients."""
    for client in CLIENTS:
        assert services.whatsapp_link(client.phone, "test message")


# --- Runner ----------------------------------------------------------------

def _main() -> int:
    tests = sorted(
        ((name, fn) for name, fn in globals().items()
         if name.startswith("test_") and callable(fn)),
        key=lambda pair: pair[1].__code__.co_firstlineno,
    )
    passed, failures = 0, []
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failures.append((name, str(exc) or "assertion failed"))
            print(f"FAIL  {name}")
        except Exception as exc:  # noqa: BLE001 - report, do not mask
            failures.append((name, f"{type(exc).__name__}: {exc}"))
            print(f"ERROR {name}")
        else:
            passed += 1
            print(f"pass  {name}")

    print("-" * 60)
    print(f"{passed}/{len(tests)} passed")
    for name, detail in failures:
        print(f"  {name}: {detail}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
