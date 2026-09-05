"""Application services: the layer the UI calls.

Deterministic business rules (who gets escalated, who may be asked for a public
review) are enforced here in Python. The model is used only for language work -
drafting copy, reading sentiment, naming themes - never for deciding policy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import quote

from . import config, prompts
from .data import Client, Feedback, escalations, feedback_for_client
from .gemini import GeminiClient, GeminiError


# --- WhatsApp click-to-chat -------------------------------------------------

def whatsapp_link(phone: str, message: str) -> str:
    """A wa.me link that opens the client's chat with the message pre-filled.

    This is not sending: WhatsApp still requires a human to press Send inside
    the app once it opens. What it removes is the copy-paste - no text box to
    select, no switching apps to find the right contact. There is no API key,
    no business verification, and no cost involved; wa.me is a public link
    format WhatsApp itself provides.
    """
    digits = re.sub(r"\D", "", phone or "")
    if not digits or not message.strip():
        return ""
    return f"https://wa.me/{digits}?text={quote(message.strip())}"


# --- Review-ask eligibility ------------------------------------------------

@dataclass
class ReviewEligibility:
    """Whether a public review may be solicited, and why."""
    eligible: bool
    reason: str
    evidence: str = ""


def review_eligibility(client: Client, feedback: list[Feedback]) -> ReviewEligibility:
    """Decide from the feedback log, not from a checkbox.

    A public review is only worth soliciting from a client who has demonstrably
    been happy and has nothing unresolved outstanding. Reading that from the data
    means the guardrail does not depend on the account manager remembering.
    """
    mine = feedback_for_client(feedback, client.client_id)
    if not mine:
        return ReviewEligibility(
            False,
            "No feedback on record for this client, so satisfaction cannot be evidenced.",
        )

    open_issues = [f for f in mine if f.needs_escalation]
    if config.REVIEW_ASK_BLOCKS_ON_OPEN_ISSUE and open_issues:
        worst = min(f.rating for f in open_issues)
        return ReviewEligibility(
            False,
            f"An unresolved complaint is open on this account (rated {worst}/5). "
            "Asking for a public review now risks turning a private issue into a public one.",
            evidence=open_issues[0].comment,
        )

    best = max(f.rating for f in mine)
    if best < config.REVIEW_ASK_MIN_RATING:
        return ReviewEligibility(
            False,
            f"Best rating on record is {best}/5, below the {config.REVIEW_ASK_MIN_RATING}/5 "
            "needed to treat this client as satisfied.",
        )

    happiest = max(mine, key=lambda f: f.rating)
    return ReviewEligibility(
        True,
        f"Rated {best}/5 with nothing unresolved open.",
        evidence=happiest.comment,
    )


# --- Contact cadence -------------------------------------------------------

@dataclass
class CooldownStatus:
    """Whether this client was messaged too recently for their segment."""
    within_cooldown: bool
    days_since: int | None
    cooldown_days: int
    message: str


def cooldown_status(client: Client, last_sent: datetime | None) -> CooldownStatus:
    """Enforce the cadence each segment specifies, rather than only documenting it."""
    window = config.SEGMENT_COOLDOWN_DAYS.get(client.segment, 0)
    if last_sent is None:
        return CooldownStatus(False, None, window, "No outreach logged for this client yet.")

    days = (datetime.now() - last_sent).days
    if window and days < window:
        return CooldownStatus(
            True, days, window,
            f"Messaged {days} day(s) ago. {client.segment} clients are on a "
            f"{window}-day cadence - asking again this soon suppresses response rates.",
        )
    return CooldownStatus(False, days, window, f"Last messaged {days} day(s) ago.")


# --- Service breakdown -----------------------------------------------------

@dataclass
class ServiceStats:
    service: str
    count: int
    average_rating: float
    escalations: int


def service_breakdown(items: list[Feedback]) -> list[ServiceStats]:
    """Per-service ratings, worst first - a single headline average hides this."""
    grouped: dict[str, list[Feedback]] = {}
    for f in items:
        grouped.setdefault(f.service, []).append(f)
    stats = [
        ServiceStats(
            service=service,
            count=len(rows),
            average_rating=round(sum(f.rating for f in rows) / len(rows), 2),
            escalations=sum(1 for f in rows if f.needs_escalation),
        )
        for service, rows in grouped.items()
    ]
    return sorted(stats, key=lambda s: s.average_rating)


# --- Outreach --------------------------------------------------------------

@dataclass
class OutreachResult:
    message: str
    review_ask: str | None
    blocked_reason: str | None = None
    variants: list[str] = field(default_factory=list)
    language: str = config.DEFAULT_LANGUAGE


def generate_outreach(
    client_gemini: GeminiClient,
    client: Client,
    service: str,
    *,
    request_review_ask: bool,
    feedback: list[Feedback] | None = None,
    client_signalled_satisfaction: bool = False,
    owner_name: str | None = None,
    language: str = config.DEFAULT_LANGUAGE,
    variant_count: int = 1,
    extra_context: str = "",
) -> OutreachResult:
    """Draft a segment-appropriate outreach message.

    The review ask is gated in code, not in the prompt: a public review is only
    solicited from a client who has already signalled satisfaction, so an
    unresolved complaint is never converted into a public one-star.

    When ``feedback`` is supplied the gate reads that evidence directly; the
    ``client_signalled_satisfaction`` flag is only the fallback for callers that
    have no feedback log to consult.
    """
    blocked_reason = None
    include_review = request_review_ask

    if request_review_ask and config.REVIEW_ASK_REQUIRES_POSITIVE_SIGNAL:
        if feedback is not None:
            verdict = review_eligibility(client, feedback)
            if not verdict.eligible:
                include_review = False
                blocked_reason = f"Review ask withheld. {verdict.reason}"
        elif not client_signalled_satisfaction:
            include_review = False
            blocked_reason = (
                "Review ask withheld: this client has not signalled satisfaction yet. "
                "Asking now risks turning a private issue into a public review."
            )

    prompt = prompts.outreach_message_prompt(
        client,
        service,
        owner_name=owner_name,
        include_review_ask=include_review,
        language=language,
        variant_count=variant_count,
        extra_context=extra_context,
    )
    raw = client_gemini.generate(prompt, temperature=config.GENERATION_TEMPERATURE)

    body, review_ask = _split_review_ask(raw) if include_review else (raw, None)
    variants = _split_variants(body)
    return OutreachResult(
        message=variants[0],
        review_ask=review_ask.strip() if review_ask else None,
        blocked_reason=blocked_reason,
        variants=variants,
        language=language,
    )


def _split_variants(body: str) -> list[str]:
    """Split the model's alternatives, tolerating a missing or stray separator."""
    parts = [p.strip() for p in body.split(config.VARIANT_SEPARATOR)]
    parts = [p for p in parts if p]
    return parts or [body.strip()]


def _split_review_ask(raw: str) -> tuple[str, str | None]:
    parts = [p.strip() for p in raw.split("---") if p.strip()]
    if len(parts) >= 2:
        return parts[0], parts[1]
    return raw, None


# --- Triage ----------------------------------------------------------------

@dataclass
class TriagedFeedback:
    feedback: Feedback
    client_name: str
    sentiment: str | None = None
    urgency: str | None = None
    issue_type: str | None = None
    suggested_first_line: str | None = None

    @property
    def needs_escalation(self) -> bool:
        """Rule-based escalation, independent of the model's judgement."""
        return self.feedback.needs_escalation

    @property
    def model_flagged(self) -> bool:
        """Model thinks this is urgent even though the rating did not trip the rule."""
        return self.urgency == "high" and not self.feedback.needs_escalation


def triage_feedback(
    client_gemini: GeminiClient,
    items: list[Feedback],
    clients: list[Client],
) -> tuple[list[TriagedFeedback], str | None]:
    """Enrich feedback with model-read sentiment/urgency.

    Returns the triaged list plus a warning string if the model call failed -
    the rule-based escalations still stand in that case, so the tool degrades
    to its deterministic core rather than failing outright.
    """
    name_by_id = {c.client_id: c.name for c in clients}
    triaged = [
        TriagedFeedback(feedback=f, client_name=name_by_id.get(f.client_id, f.client_id))
        for f in items
    ]

    if not items:
        return triaged, None

    try:
        parsed = client_gemini.generate_json(prompts.triage_prompt(items))
    except GeminiError as exc:
        return triaged, str(exc)

    by_id = {t.feedback.feedback_id: t for t in triaged}
    if not isinstance(parsed, list):
        return triaged, (
            "The model returned "
            f"{type(parsed).__name__} where a list of entries was expected."
        )

    matched = 0
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        target = by_id.get(entry.get("feedback_id"))
        if target is None:
            continue
        matched += 1
        target.sentiment = _clean(entry.get("sentiment"))
        target.urgency = _clean(entry.get("urgency"))
        target.issue_type = _clean(entry.get("issue_type"))
        target.suggested_first_line = _clean(entry.get("suggested_first_line"))

    if matched == 0:
        # Parsed cleanly but nothing lined up - previously this left every field
        # blank with no explanation, which reads as "the button does nothing".
        return triaged, (
            f"The model returned {len(parsed)} entries but none carried a "
            "feedback_id matching the current rows, so nothing could be applied."
        )
    if matched < len(items):
        return triaged, (
            f"Only {matched} of {len(items)} rows came back from the model; "
            "the rest are shown with rule-based information only."
        )

    return triaged, None


def _clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text and text.lower() not in {"null", "none", "n/a"} else None


# --- Themes ----------------------------------------------------------------

def summarize_themes(
    client_gemini: GeminiClient,
    items: list[Feedback],
    clients: list[Client],
) -> str:
    """Themes plus direction: what recurs, and whether it is getting better.

    The model gets an aggregated trend table over the whole history plus the most
    recent comments verbatim, rather than every row - months of raw feedback would
    be expensive and would bury the recent signal.
    """
    name_by_id = {c.client_id: c.name for c in clients}
    recent = sorted(items, key=lambda f: f.received_at, reverse=True)[: config.MAX_THEME_ITEMS]
    return client_gemini.generate(
        prompts.themes_prompt(recent, name_by_id, trend_table=trend_table(items)),
        temperature=config.ANALYSIS_TEMPERATURE,
    )


# --- Trends ----------------------------------------------------------------

@dataclass
class MonthStats:
    month: str            # YYYY-MM, sortable
    label: str            # "Mar 2026"
    count: int
    average_rating: float
    escalations: int


def monthly_trend(items: list[Feedback]) -> list[MonthStats]:
    """Rating by calendar month, oldest first."""
    grouped: dict[str, list[Feedback]] = {}
    for f in items:
        grouped.setdefault(f.received_at.strftime("%Y-%m"), []).append(f)
    out = []
    for month in sorted(grouped):
        rows = grouped[month]
        out.append(MonthStats(
            month=month,
            label=rows[0].received_at.strftime("%b %Y"),
            count=len(rows),
            average_rating=round(sum(f.rating for f in rows) / len(rows), 2),
            escalations=sum(1 for f in rows if f.rating <= config.ESCALATION_RATING_THRESHOLD),
        ))
    return out


def service_trend(items: list[Feedback]) -> dict[str, dict[str, float]]:
    """{service: {month: average rating}} - the per-line view of the same data."""
    grouped: dict[str, dict[str, list[int]]] = {}
    for f in items:
        month = f.received_at.strftime("%Y-%m")
        grouped.setdefault(f.service, {}).setdefault(month, []).append(f.rating)
    return {
        service: {m: round(sum(r) / len(r), 2) for m, r in sorted(months.items())}
        for service, months in grouped.items()
    }


def trend_headline(items: list[Feedback]) -> str:
    """One sentence on direction, or a note that there is not enough history."""
    months = monthly_trend(items)
    if len(months) < 2:
        return "Not enough history yet to show a trend."
    first, last = months[0], months[-1]
    delta = round(last.average_rating - first.average_rating, 2)
    worst = min(months, key=lambda m: m.average_rating)
    direction = "up" if delta > 0.1 else ("down" if delta < -0.1 else "flat")
    trailing = (
        f" It bottomed out at {worst.average_rating}/5 in {worst.label}."
        if worst not in (first, last) else ""
    )
    return (
        f"Average rating is {direction} {abs(delta):.2f} points across {len(months)} months, "
        f"from {first.average_rating}/5 in {first.label} to "
        f"{last.average_rating}/5 in {last.label}.{trailing}"
    )


def trend_table(items: list[Feedback]) -> str:
    """Compact plain-text trend the model can reason over without the raw rows."""
    lines = ["Overall by month:"]
    for m in monthly_trend(items):
        lines.append(f"  {m.label}: {m.average_rating}/5 across {m.count} responses")
    lines.append("")
    lines.append("By service line and month:")
    for service, months in service_trend(items).items():
        series = ", ".join(
            f"{datetime.strptime(m, '%Y-%m'):%b}={v}" for m, v in months.items()
        )
        lines.append(f"  {service}: {series}")
    return "\n".join(lines)


# --- Net Promoter Score ----------------------------------------------------

@dataclass
class NPSStats:
    responses: int
    promoters: int
    passives: int
    detractors: int
    score: int          # -100..+100

    @property
    def promoter_share(self) -> float:
        return round(100 * self.promoters / self.responses, 1) if self.responses else 0.0


def nps_summary(items: list[Feedback]) -> NPSStats:
    """Standard NPS: percent promoters minus percent detractors, over responders."""
    scored = [f for f in items if f.nps_score is not None]
    if not scored:
        return NPSStats(0, 0, 0, 0, 0)
    bands = [f.nps_band for f in scored]
    promoters = bands.count("Promoter")
    passives = bands.count("Passive")
    detractors = bands.count("Detractor")
    score = round(100 * (promoters - detractors) / len(scored))
    return NPSStats(len(scored), promoters, passives, detractors, score)


def nps_trend(items: list[Feedback]) -> list[tuple[str, int, int]]:
    """[(month label, NPS, responses)] oldest first."""
    grouped: dict[str, list[Feedback]] = {}
    for f in items:
        if f.nps_score is not None:
            grouped.setdefault(f.received_at.strftime("%Y-%m"), []).append(f)
    out = []
    for month in sorted(grouped):
        rows = grouped[month]
        stats = nps_summary(rows)
        out.append((rows[0].received_at.strftime("%b %Y"), stats.score, stats.responses))
    return out


# --- Churn early warning ---------------------------------------------------

@dataclass
class ClientRisk:
    client: Client
    score: int
    band: str
    reasons: list[str]
    rating_before: float | None
    rating_after: float | None
    days_since_job: int
    responses: int

    @property
    def trend_delta(self) -> float | None:
        if self.rating_before is None or self.rating_after is None:
            return None
        return round(self.rating_after - self.rating_before, 2)


def _split_trend(rows: list[Feedback]) -> tuple[float | None, float | None]:
    """Average rating of the older half vs the newer half of a client's responses."""
    if len(rows) < config.CHURN_MIN_RESPONSES:
        return None, None
    ordered = sorted(rows, key=lambda f: f.received_at)
    middle = len(ordered) // 2
    older, newer = ordered[:middle], ordered[middle:]
    return (
        round(sum(f.rating for f in older) / len(older), 2),
        round(sum(f.rating for f in newer) / len(newer), 2),
    )


def churn_risk(clients: list[Client], feedback: list[Feedback]) -> list[ClientRisk]:
    """Score every client for churn risk, most at risk first.

    Satisfaction falls before ordering stops, so a client can still look active
    on the books while already leaving. The segment column is a label someone
    typed; this is the same funnel computed from behaviour.
    """
    out = []
    for client in clients:
        rows = feedback_for_client(feedback, client.client_id)
        before, after = _split_trend(rows)
        silence = config.CHURN_SILENCE_DAYS.get(client.segment, 90)
        days = client.days_since_last_job

        score, reasons = 0, []

        if before is not None and after is not None and (before - after) >= config.CHURN_RATING_DROP:
            score += 3
            reasons.append(f"Rating fell {before} to {after} across {len(rows)} responses.")

        if days > silence and client.segment != "Obsolete":
            score += 3
            reasons.append(
                f"No work for {days} days; {client.segment} clients normally return "
                f"within {silence}."
            )

        open_issues = [f for f in rows if f.needs_escalation]
        if open_issues:
            score += 2
            reasons.append(f"{len(open_issues)} unresolved complaint(s) still open.")

        recent = sorted(rows, key=lambda f: f.received_at, reverse=True)[:3]
        detractors = [f for f in recent if f.nps_band == "Detractor"]
        if detractors:
            score += 1
            reasons.append(f"{len(detractors)} of the last {len(recent)} responses were detractors.")

        if recent and recent[0].rating <= config.ESCALATION_RATING_THRESHOLD:
            score += 1
            reasons.append(f"Most recent response was {recent[0].rating}/5.")

        if not rows:
            reasons.append("No feedback on record - nothing to read either way.")

        band = "None"
        for threshold, label in config.RISK_BANDS:
            if score >= threshold:
                band = label
                break

        out.append(ClientRisk(
            client=client, score=score, band=band, reasons=reasons,
            rating_before=before, rating_after=after,
            days_since_job=days, responses=len(rows),
        ))

    return sorted(out, key=lambda r: (-r.score, -r.days_since_job))


# --- Closed-loop recovery --------------------------------------------------

@dataclass
class RecoveryOutcome:
    client_name: str
    complaint_rating: int
    complaint_on: datetime
    following_average: float
    recovered: bool

    @property
    def complaint_at(self) -> str:
        return self.complaint_on.strftime("%d %b %Y")


def recovery_outcomes(
    clients: list[Client], feedback: list[Feedback], *, lookahead: int = 2
) -> list[RecoveryOutcome]:
    """Did resolving a complaint actually work?

    For every complaint that was marked resolved, look at what that client said
    next. Closing a ticket is not the same as recovering a client, and the
    difference is the whole point of a closed feedback loop.
    """
    name_by_id = {c.client_id: c.name for c in clients}
    out = []
    for client_id in {f.client_id for f in feedback}:
        rows = sorted(feedback_for_client(feedback, client_id), key=lambda f: f.received_at)
        for index, row in enumerate(rows):
            if row.rating > config.ESCALATION_RATING_THRESHOLD or not row.resolved:
                continue
            following = rows[index + 1: index + 1 + lookahead]
            if not following:
                continue
            average = round(sum(f.rating for f in following) / len(following), 2)
            out.append(RecoveryOutcome(
                client_name=name_by_id.get(client_id, client_id),
                complaint_rating=row.rating,
                complaint_on=row.received_at,
                following_average=average,
                recovered=average >= 4,
            ))
    return sorted(out, key=lambda r: r.complaint_on)


def recovery_rate(outcomes: list[RecoveryOutcome]) -> float:
    """Share of resolved complaints where the client's next responses recovered."""
    if not outcomes:
        return 0.0
    return round(100 * sum(1 for o in outcomes if o.recovered) / len(outcomes), 1)


# --- Issue frequency -------------------------------------------------------

@dataclass
class IssueSeries:
    issue: str
    months: list[str]          # "Mar 2026", oldest first
    counts: list[int]          # raw mentions
    per_100: list[float]       # mentions per 100 responses that month
    direction: str
    total: int


def issue_trends(items: list[Feedback]) -> list[IssueSeries]:
    """How often each issue is raised, per month, normalised by volume.

    Deliberately computed in Python rather than asked of the model: a theme is
    only useful as a trend if the same comment is counted the same way every
    month, and a model re-reading the corpus will not guarantee that.

    Normalising matters. August carries far more responses than March, so raw
    counts would make a busy month look like a worsening month. The direction is
    read off the rate, not the count.
    """
    keys = sorted({f.received_at.strftime("%Y-%m") for f in items})
    if not keys:
        return []
    labels = {
        k: next(f.received_at.strftime("%b %Y") for f in items
                if f.received_at.strftime("%Y-%m") == k)
        for k in keys
    }
    totals = {k: sum(1 for f in items if f.received_at.strftime("%Y-%m") == k) for k in keys}

    out = []
    for issue, keywords in config.ISSUE_KEYWORDS.items():
        counts = {k: 0 for k in keys}
        for f in items:
            text = f.comment.lower()
            if any(word in text for word in keywords):
                counts[f.received_at.strftime("%Y-%m")] += 1
        if not sum(counts.values()):
            continue
        rates = [
            round(100 * counts[k] / totals[k], 1) if totals[k] else 0.0 for k in keys
        ]
        out.append(IssueSeries(
            issue=issue,
            months=[labels[k] for k in keys],
            counts=[counts[k] for k in keys],
            per_100=rates,
            direction=_direction(rates),
            total=sum(counts.values()),
        ))
    return sorted(out, key=lambda s: -s.total)


def _direction(rates: list[float]) -> str:
    """Better, worse, or holding - half of the series against the other half."""
    if len(rates) < 2:
        return "too little history"
    middle = len(rates) // 2
    older = sum(rates[:middle]) / max(middle, 1)
    newer = sum(rates[middle:]) / max(len(rates) - middle, 1)
    # Threshold in points of rate, so a one-off mention does not flip the verdict.
    if newer < older - 4:
        return "improving"
    if newer > older + 4:
        return "worsening"
    return "steady"


# --- Dashboard metrics -----------------------------------------------------

@dataclass
class DashboardStats:
    total: int
    escalations: int
    overdue: int
    average_rating: float
    promoters: int  # eligible for a review ask (rating 5, unresolved-free)


def compute_stats(items: list[Feedback]) -> DashboardStats:
    if not items:
        return DashboardStats(0, 0, 0, 0.0, 0)
    flagged = escalations(items)
    return DashboardStats(
        total=len(items),
        escalations=len(flagged),
        overdue=sum(1 for f in flagged if f.breaches_sla),
        average_rating=round(sum(f.rating for f in items) / len(items), 2),
        promoters=sum(1 for f in items if f.rating == 5),
    )
