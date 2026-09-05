"""Prompt construction.

Prompts live here rather than inline in the UI so they can be reviewed,
version-controlled, and cited in the project report as design artifacts.
"""

from __future__ import annotations

import json

from . import config
from .data import Client, Feedback

# --- System framing --------------------------------------------------------

_AGENCY_CONTEXT = (
    f"{config.AGENCY_NAME} is an out-of-home (OOH) advertising agency in "
    f"{config.AGENCY_CITY}, operating for over 20 years. Its service lines are "
    "auto-rickshaw hood branding, hoardings and billboards, transit (bus) "
    "advertising, and digital OOH displays. Client servicing happens over "
    "WhatsApp, so messages are short, personal, and never read like marketing copy."
)


# --- Tab 1: outreach message ----------------------------------------------

def outreach_message_prompt(
    client: Client,
    service: str,
    *,
    owner_name: str | None = None,
    include_review_ask: bool = False,
    language: str = config.DEFAULT_LANGUAGE,
    variant_count: int = 1,
    extra_context: str = "",
) -> str:
    segment = config.SEGMENTS[client.segment]

    # The person being messaged, which is often not the firm name. The account
    # manager can override the record, so an empty box falls back to the CSV.
    owner = (owner_name or "").strip() or client.contact_person
    first_name = owner.split()[0] if owner.split() else owner

    # Approved texts for exactly this service + segment. These do the heavy
    # lifting on register: a hoarding draft drifting into rickshaw language is
    # much less likely when every example in front of the model is a hoarding one.
    library = config.messages_for(service, client.segment)
    examples_block = ""
    if library:
        shown = library[:4]
        rendered = "\n".join(f'  - "{m}"' for m in shown)
        examples_block = f"""
APPROVED MESSAGES FOR THIS EXACT SERVICE AND SEGMENT
(these are how the agency writes about {service} for {client.segment} clients -
match how they refer to the work; do not copy any of them word for word):
{rendered}
"""

    language_clause = config.LANGUAGES.get(language, config.LANGUAGES[config.DEFAULT_LANGUAGE])

    variant_count = max(1, min(int(variant_count), config.MAX_VARIANTS))
    variant_clause = ""
    if variant_count > 1:
        variant_clause = f"""
Produce exactly {variant_count} alternative versions of the message. They must
differ in opening and phrasing, not just swap a word - the account manager is
choosing between genuinely different options. Separate consecutive versions with
a line containing only {config.VARIANT_SEPARATOR}
"""

    review_clause = ""
    if include_review_ask:
        review_clause = f"""
After the final version, add a separator line "---" and then ONE short, separate
message asking for a public Google review. Match this house style:
  "{config.REVIEW_ASK_EXAMPLE}"
Use {config.REVIEW_LINK_PLACEHOLDER} as the link placeholder.
"""

    context_clause = f"\nAdditional context from the account manager: {extra_context}\n" if extra_context.strip() else ""

    return f"""{_AGENCY_CONTEXT}

Draft a WhatsApp message to a client.

CLIENT
  Business name: {client.name}
    (this is the firm, NOT a person - never greet anyone by this name)
  Owner / contact person: {owner}
    (this is the human being messaged - greet them as "{first_name}")
  Segment: {client.segment}
  Jobs completed to date: {client.jobs_completed}
  Days since last job: {client.days_since_last_job}
  Account notes (historical background only - these may describe earlier jobs on a
    DIFFERENT service line, and must not be used to describe the current campaign):
    {client.notes or "none"}

THE CAMPAIGN THIS MESSAGE IS ABOUT
  Service delivered: {service}
  This is the authoritative fact. If the account notes above mention any other
  service line, ignore that detail for this message.
{context_clause}
SEGMENT INTENT
  Goal: {segment['goal']}
  Tone: {segment['tone']}

HOUSE STYLE for this segment (match the register and length; personalize it,
do not copy it word for word):
  "{segment['house_example']}"
{examples_block}
RULES
  - Open by addressing the owner as "{first_name}" - their first name only.
    Do not open with the business name, and do not use both.
  - Maximum {config.MAX_MESSAGE_SENTENCES} sentences.
  - Use {config.FORM_LINK_PLACEHOLDER} where the feedback form link goes.
  - No emoji, no exclamation stacking, no marketing adjectives ("premium",
    "unparalleled", "world-class").
  - Do not invent facts about the campaign that are not given above.
  - Refer only to "{service}". Never name or imply a different service line
    (rickshaw hoods, hoardings/billboards, bus panels, digital screens) unless
    that is the service delivered above.
  - Output the message text only - no preamble, no quotation marks, no labels,
    and no numbering of the versions.

LANGUAGE
  {language_clause}
{variant_clause}{review_clause}"""


# --- Tab 2: triage and themes ---------------------------------------------

_TRIAGE_SCHEMA = {
    "feedback_id": "string",
    "sentiment": "positive | neutral | negative",
    "urgency": "low | medium | high",
    "issue_type": "short label, 2-4 words, or null if no issue",
    "suggested_first_line": "one sentence the account manager could open the callback with, or null",
}


def triage_prompt(items: list[Feedback]) -> str:
    payload = [
        {
            "feedback_id": f.feedback_id,
            "rating": f.rating,
            "comment": f.comment,
            "service": f.service,
        }
        for f in items
    ]

    return f"""{_AGENCY_CONTEXT}

Triage the client feedback below.

FEEDBACK
{json.dumps(payload, indent=2)}

For each entry, judge sentiment and urgency from the comment text, not the star
rating alone - a 4-star comment describing a repeat failure is more urgent than a
3-star comment with no specific complaint.

Return ONLY a JSON array. No markdown fences, no commentary. Each object:
{json.dumps(_TRIAGE_SCHEMA, indent=2)}

Keep "suggested_first_line" factual and non-defensive; it should acknowledge the
specific problem the client named. Use null where a field does not apply."""


def themes_prompt(
    items: list[Feedback],
    client_names: dict[str, str],
    *,
    trend_table: str = "",
) -> str:
    lines = "\n".join(
        f"- {f.received_at:%d %b} | {client_names.get(f.client_id, f.client_id)} | "
        f"{f.rating}/5 | {f.service} | {f.comment}"
        for f in sorted(items, key=lambda f: f.received_at)
    )

    trend_block = f"""
HOW RATINGS HAVE MOVED
{trend_table}
""" if trend_table.strip() else ""

    return f"""{_AGENCY_CONTEXT}

Below is recent client feedback, most recent last.

{lines}
{trend_block}
Identify recurring themes across these comments - operational patterns the agency
could fix structurally, not one-off client complaints. Then say what the trend
shows: which service lines are improving, which are getting worse, and whether a
theme looks like it is already being fixed or is still live.

RULES
  - A theme needs at least two clients mentioning the same underlying issue.
  - Name each theme in 3-6 words, then list the clients and the service lines it spans.
  - Add one line on what a structural fix would target.
  - Separately, list any single-client comments as "isolated" - do not inflate them
    into patterns.
  - If there are no genuine multi-client patterns, say so plainly.
  - Ground every trend claim in the month-by-month figures above; do not assert a
    direction the numbers do not show, and say when a line has too little data.
  - Under 220 words total. Plain text with short headers, no markdown tables."""
