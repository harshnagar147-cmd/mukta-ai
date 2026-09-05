"""Client health analysis, printed to the terminal.

This lived as a fourth tab and was pulled back out: it made the app harder to
demo, and none of it is something an account manager acts on mid-conversation.
It is analysis for the report, so it runs on demand instead:

    python tools_client_health.py

Everything here is computed in Python and covered by test_rules.py. No API key
and no model call is involved.
"""

from __future__ import annotations

import warnings

warnings.filterwarnings("ignore")

from mukta import config, data, services


def rule(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def main() -> None:
    clients = data.load_clients()
    feedback = data.load_feedback()

    print(f"Mukta Publicity - client health, {len(feedback)} responses on file")

    # --- Satisfaction and advocacy ----------------------------------------
    rule("Net Promoter Score")
    overall = services.nps_summary(feedback)
    print(
        f"  Overall NPS {overall.score:+d}  "
        f"({overall.promoters} promoters / {overall.passives} passives / "
        f"{overall.detractors} detractors)"
    )
    for label, score, responses in services.nps_trend(feedback):
        print(f"    {label}: {score:+d} across {responses} responses")

    # --- Churn -------------------------------------------------------------
    rule("Clients at risk")
    risks = [r for r in services.churn_risk(clients, feedback) if r.band != "None"]
    if not risks:
        print("  No client is showing churn signals.")
    for risk in risks:
        delta = risk.trend_delta
        movement = f", rating {delta:+.2f}" if delta is not None else ""
        print(
            f"  [{risk.band:6}] {risk.client.name} ({risk.client.segment})"
            f"{movement}, last job {risk.days_since_job}d ago"
        )
        for reason in risk.reasons:
            print(f"           - {reason}")

    # --- Did resolution work? ---------------------------------------------
    rule("Closed-loop recovery")
    outcomes = services.recovery_outcomes(clients, feedback)
    if not outcomes:
        print("  No resolved complaint yet has later feedback to judge it against.")
    else:
        recovered = sum(1 for o in outcomes if o.recovered)
        print(
            f"  {recovered} of {len(outcomes)} resolved complaints were followed by "
            f"ratings averaging 4/5 or better ({services.recovery_rate(outcomes)}%)."
        )
        print("  Closing a ticket is not the same as recovering a client.")
        for outcome in outcomes[-8:]:
            verdict = "recovered" if outcome.recovered else "still poor"
            print(
                f"    {outcome.complaint_at}  {outcome.client_name:22} "
                f"{outcome.complaint_rating}/5 -> next {outcome.following_average}  {verdict}"
            )

    # --- Are the themes shrinking? ----------------------------------------
    rule("Issue frequency (mentions per 100 responses)")
    trends = services.issue_trends(feedback)
    if not trends:
        print("  No tracked issue appears in the feedback.")
    for series in trends:
        months = " ".join(f"{m.split()[0]}={r}" for m, r in zip(series.months, series.per_100))
        print(f"  {series.issue:30} {series.direction:10} {months}")

    rule("Service lines, worst first")
    for stats in services.service_breakdown(feedback):
        print(
            f"  {stats.average_rating:.2f}/5  n={stats.count:3}  "
            f"open escalations={stats.escalations}  {stats.service}"
        )

    print()
    print(services.trend_headline(feedback))


if __name__ == "__main__":
    main()
