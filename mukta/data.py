"""Loading and shaping the client and feedback data.

The CSVs under ``data/`` are sample records built to mirror the shape of
Mukta Publicity's real client book, not the real book itself. Swapping in a
production source means changing only ``load_clients`` / ``load_feedback``.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

from . import config


# --- Domain models ---------------------------------------------------------

@dataclass(frozen=True)
class Client:
    client_id: str
    name: str
    contact_person: str
    phone: str
    segment: str
    primary_service: str
    jobs_completed: int
    last_job_date: date
    city: str
    notes: str

    @property
    def days_since_last_job(self) -> int:
        return (date.today() - self.last_job_date).days

    @property
    def label(self) -> str:
        return f"{self.name} ({self.segment})"


@dataclass(frozen=True)
class Feedback:
    feedback_id: str
    client_id: str
    received_at: datetime
    rating: int
    nps_score: int | None
    comment: str
    service: str
    resolved: bool

    @property
    def nps_band(self) -> str | None:
        """Promoter / Passive / Detractor, or None if the client did not answer."""
        return config.nps_band(self.nps_score)

    @property
    def needs_escalation(self) -> bool:
        return (
            self.rating <= config.ESCALATION_RATING_THRESHOLD
            and not self.resolved
        )

    @property
    def hours_open(self) -> float:
        """Hours since the feedback arrived, floored at zero.

        Sample rows are timestamped for 'today', so depending on the clock a row
        can sit slightly in the future. Flooring keeps the countdown sensible
        instead of showing more than the full SLA window as remaining.
        """
        elapsed = (datetime.now() - self.received_at).total_seconds() / 3600
        return max(elapsed, 0.0)

    @property
    def breaches_sla(self) -> bool:
        """Past the 24-hour callback window and still unresolved."""
        return self.needs_escalation and self.hours_open > config.ESCALATION_WINDOW_HOURS


# --- Loaders ---------------------------------------------------------------

def load_clients(path=None) -> list[Client]:
    df = pd.read_csv(path or config.CLIENTS_CSV, parse_dates=["last_job_date"])
    return [
        Client(
            client_id=row.client_id,
            name=row.name,
            contact_person=row.contact_person,
            phone=str(row.phone) if pd.notna(row.phone) else "",
            segment=row.segment,
            primary_service=row.primary_service,
            jobs_completed=int(row.jobs_completed),
            last_job_date=row.last_job_date.date(),
            city=row.city,
            notes=str(row.notes) if pd.notna(row.notes) else "",
        )
        for row in df.itertuples(index=False)
    ]


def load_feedback(path=None) -> list[Feedback]:
    df = pd.read_csv(path or config.FEEDBACK_CSV, parse_dates=["received_at"])
    return [
        Feedback(
            feedback_id=row.feedback_id,
            client_id=row.client_id,
            received_at=row.received_at.to_pydatetime(),
            rating=int(row.rating),
            nps_score=int(row.nps_score) if pd.notna(row.nps_score) else None,
            comment=str(row.comment),
            service=row.service,
            resolved=bool(row.resolved),
        )
        for row in df.itertuples(index=False)
    ]


# --- Writeback -------------------------------------------------------------
# The prototype's store is a CSV, so "saving" means rewriting the file. Kept in
# this module so a production swap to a database touches only this section.

def mark_resolved(feedback_id: str, path=None) -> bool:
    """Flag one feedback row resolved. Returns False if the id was not found."""
    target = path or config.FEEDBACK_CSV
    df = pd.read_csv(target)
    match = df["feedback_id"] == feedback_id
    if not match.any():
        return False
    df.loc[match, "resolved"] = True
    df.to_csv(target, index=False)
    return True


def next_feedback_id(path=None) -> str:
    """Next F-number, so a hand-added row cannot collide with an existing one."""
    df = pd.read_csv(path or config.FEEDBACK_CSV)
    numbers = [
        int(str(v)[1:]) for v in df["feedback_id"] if str(v).startswith("F") and str(v)[1:].isdigit()
    ]
    return f"F{max(numbers, default=0) + 1:03d}"


def append_feedback(
    client_id: str,
    rating: int,
    comment: str,
    service: str,
    *,
    nps_score: int | None = None,
    received_at: datetime | None = None,
    path=None,
) -> str:
    """Add a feedback row and return its new id."""
    target = path or config.FEEDBACK_CSV
    feedback_id = next_feedback_id(target)
    row = {
        "feedback_id": feedback_id,
        "client_id": client_id,
        "received_at": (received_at or datetime.now()).strftime("%Y-%m-%dT%H:%M:%S"),
        "rating": int(rating),
        "nps_score": "" if nps_score is None else int(nps_score),
        "comment": comment.replace("\n", " ").strip(),
        "service": service,
        "resolved": False,
    }
    df = pd.read_csv(target)
    df.loc[len(df)] = row
    df.to_csv(target, index=False)
    return feedback_id


# --- Outreach log ----------------------------------------------------------
# Without a record of what went out, the cadence rules in config are unenforced
# and response rate is not computable. Appended to on every accepted draft.

OUTREACH_FIELDS = [
    "sent_at", "client_id", "client_name", "owner_name",
    "segment", "service", "language", "message",
]


def log_outreach(
    client: "Client",
    owner_name: str,
    service: str,
    language: str,
    message: str,
    *,
    sent_at: datetime | None = None,
    path=None,
) -> None:
    target = path or config.OUTREACH_LOG_CSV
    target.parent.mkdir(parents=True, exist_ok=True)
    is_new = not target.exists()
    with open(target, "a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTREACH_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow({
            "sent_at": (sent_at or datetime.now()).strftime("%Y-%m-%dT%H:%M:%S"),
            "client_id": client.client_id,
            "client_name": client.name,
            "owner_name": owner_name,
            "segment": client.segment,
            "service": service,
            "language": language,
            "message": message.replace("\n", " ").strip(),
        })


def load_outreach_log(path=None) -> pd.DataFrame:
    """The outreach log, or an empty frame with the right columns if unwritten."""
    target = path or config.OUTREACH_LOG_CSV
    if not target.exists():
        return pd.DataFrame(columns=OUTREACH_FIELDS)
    return pd.read_csv(target, parse_dates=["sent_at"])


def last_contacted(log: pd.DataFrame, client_id: str) -> datetime | None:
    if log.empty or "client_id" not in log:
        return None
    mine = log[log["client_id"] == client_id]
    if mine.empty:
        return None
    return pd.to_datetime(mine["sent_at"]).max().to_pydatetime()


# --- Queries ---------------------------------------------------------------

def client_by_id(clients: list[Client], client_id: str) -> Client | None:
    return next((c for c in clients if c.client_id == client_id), None)


def client_by_name(clients: list[Client], name: str) -> Client | None:
    return next((c for c in clients if c.name == name), None)


def feedback_for_client(feedback: list[Feedback], client_id: str) -> list[Feedback]:
    return [f for f in feedback if f.client_id == client_id]


def escalations(feedback: list[Feedback]) -> list[Feedback]:
    """Unresolved feedback at or below the escalation threshold, most urgent first."""
    flagged = [f for f in feedback if f.needs_escalation]
    return sorted(flagged, key=lambda f: (f.rating, -f.hours_open))


def feedback_to_frame(feedback: list[Feedback], clients: list[Client]) -> pd.DataFrame:
    """Display frame joining feedback to client names, with the escalation verdict."""
    name_by_id = {c.client_id: c.name for c in clients}
    phone_by_id = {c.client_id: c.phone for c in clients}
    return pd.DataFrame(
        [
            {
                "Client": name_by_id.get(f.client_id, f.client_id),
                "Phone": phone_by_id.get(f.client_id, ""),
                "Rating": f.rating,
                "NPS": f.nps_score if f.nps_score is not None else "",
                "Service": f.service,
                "Comment": f.comment,
                "Received": f.received_at.strftime("%d %b, %H:%M"),
                "Action": _action_label(f),
            }
            for f in feedback
        ]
    )


def _action_label(f: Feedback) -> str:
    if f.resolved:
        return "Resolved"
    if f.breaches_sla:
        return f"OVERDUE - {f.hours_open:.0f} hrs open"
    if f.needs_escalation:
        remaining = min(max(config.ESCALATION_WINDOW_HOURS - f.hours_open, 0),
                        config.ESCALATION_WINDOW_HOURS)
        return f"Call within {remaining:.0f} hrs"
    return "No action"
