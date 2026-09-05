"""Business rules and constants for the Mukta Publicity assistant.

Every value here traces back to a decision in Mukta Publicity's internal
recommendations memo or the website audit, so the rules stay auditable
rather than being buried in prompt text.
"""

from __future__ import annotations

from pathlib import Path

# --- Paths -----------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
CLIENTS_CSV = DATA_DIR / "clients.csv"
FEEDBACK_CSV = DATA_DIR / "feedback.csv"
OUTREACH_LOG_CSV = DATA_DIR / "outreach_log.csv"

# --- Model -----------------------------------------------------------------

DEFAULT_MODEL = "gemini-3.6-flash"
GENERATION_TEMPERATURE = 0.7   # outreach copy: some warmth and variation
ANALYSIS_TEMPERATURE = 0.2     # triage and theming: reproducible, low drift
# Reasoning tokens on the current Gemini models are billed against this same
# budget, so a limit sized for the visible answer alone starves the response and
# comes back empty. Triage returns one object per feedback row, so it needs real
# headroom; the short outreach message never approaches this.
MAX_OUTPUT_TOKENS = 8192

# --- Escalation rule -------------------------------------------------------
# From the memo: "any rating of 3 or below triggers a follow-up call from the
# client-servicing person within 24 hours."

ESCALATION_RATING_THRESHOLD = 3
ESCALATION_WINDOW_HOURS = 24

# --- Review window ---------------------------------------------------------
# The feedback log now holds months of history. The operational views work on a
# recent slice - triaging half a year of already-closed rows would be slow, cost
# a great deal, and bury today's work. Trends deliberately use everything.

REVIEW_WINDOWS: dict[str, int | None] = {
    "Last 7 days": 7,
    "Last 30 days": 30,
    "Last 90 days": 90,
    "All time": None,
}
DEFAULT_REVIEW_WINDOW = "Last 7 days"

# Cap on how many rows are sent to the model in one triage call.
MAX_TRIAGE_ITEMS = 25
# Recent comments quoted to the themes prompt, on top of the trend table.
MAX_THEME_ITEMS = 40


# --- Net Promoter Score ----------------------------------------------------
# The star rating is a CSAT measure: how did this job go. NPS answers a different
# question - would you put your name behind us - which is the one that predicts
# referral, and OOH in Ahmedabad runs largely on referral. Standard 0-10 bands.

NPS_PROMOTER_MIN = 9      # 9-10
NPS_PASSIVE_MIN = 7       # 7-8; below 7 is a detractor
NPS_SCALE_MAX = 10


def nps_band(score: int | None) -> str | None:
    """Promoter / Passive / Detractor for one response."""
    if score is None:
        return None
    if score >= NPS_PROMOTER_MIN:
        return "Promoter"
    if score >= NPS_PASSIVE_MIN:
        return "Passive"
    return "Detractor"


# --- Churn early warning ---------------------------------------------------
# Satisfaction falls before ordering stops, so a client can be visibly at risk
# while still looking active on the books. These thresholds turn that into a
# check rather than a hunch.

CHURN_RATING_DROP = 0.4        # points of decline, first half vs second half
CHURN_MIN_RESPONSES = 4        # below this a "trend" is noise, not signal
# Days since last job that count as drifting, per segment. A Regular client
# silent for 60 days is a much louder signal than an Irregular one.
CHURN_SILENCE_DAYS: dict[str, int] = {
    "New": 45,
    "Regular": 60,
    "Irregular": 120,
    "Obsolete": 999,           # already lapsed; nothing left to warn about
}

RISK_BANDS = [(5, "High"), (3, "Medium"), (1, "Low")]   # score -> label, descending


# --- Issue vocabulary ------------------------------------------------------
# Keyword families for tracking whether a theme is shrinking month on month.
# Deliberately plain matching, computed in Python: the point is a stable series
# the model cannot drift on, not a second opinion about what a comment meant.

ISSUE_KEYWORDS: dict[str, list[str]] = {
    "Late / missed installation": [
        "late", "delay", "slipped", "not ready", "behind", "turned up late",
        "started four days", "took another week",
    ],
    "Poor communication": [
        "nobody informed", "no one picked up", "heard nothing", "chase",
        "without a heads-up", "did not tell", "updates",
    ],
    "Print or finish quality": [
        "print", "duller", "faded", "sharper", "shade", "crooked", "tore",
        "peeling", "finish",
    ],
    "Screen or display downtime": [
        "not switched on", "dark", "skipped", "aspect ratio", "uptime",
    ],
    "Fitting or durability": [
        "came off", "came loose", "fitted loose", "replacement",
    ],
    "Coverage or reach shortfall": [
        "coverage", "routes", "missed in the first batch", "count",
    ],
}


# --- Client segments -------------------------------------------------------
# New / Regular / Irregular / Obsolete, with the goal the memo assigns to each.

SEGMENTS: dict[str, dict[str, str]] = {
    "New": {
        "goal": "Catch issues early, before the client decides whether to stick around.",
        "tone": "Warm and appreciative of their trying the agency out. Reference the "
                "specific service just delivered.",
        "house_example": (
            "Hi [Name], thanks for trying us out on [service]! We'd love to know how it "
            "went - your feedback helps us get it right from the start: [form link]"
        ),
        "cadence": "After each of the first 2-3 jobs (retention-critical window).",
    },
    "Regular": {
        "goal": "Light-touch periodic check-in that does not feel repetitive.",
        "tone": "Familiar and relaxed. Do NOT tie the message to one specific job - "
                "regulars get asked after every job and that suppresses response rates.",
        "house_example": (
            "Hi [Name], hope things are going well! Quick favor - could you share "
            "feedback on how we're doing lately? [form link]"
        ),
        "cadence": "Periodic, not per-job.",
    },
    "Irregular": {
        "goal": "Get feedback, plus a soft signal of availability without being pushy.",
        "tone": "Pleased to hear from them again. One light mention that the agency is "
                "there when they next need work - never a hard sell.",
        "house_example": (
            "Hi [Name], good to hear from you again! We'd love your feedback on the "
            "recent work, and always happy to help whenever you need us next: [form link]"
        ),
        "cadence": "After a job, when they resurface.",
    },
    "Obsolete": {
        "goal": "Win-back framed as feedback - a different job from the other three.",
        "tone": "Acknowledge the gap warmly and without guilt. Invite honesty about what "
                "could have been better, and leave an open door for new work.",
        "house_example": (
            "Hi [Name], it's been a while! We'd love to know if there's anything we "
            "could've done better, or if there's a project we could help with now: [form link]"
        ),
        "cadence": "One-time reactivation, in small batches of 30-50 per day.",
    },
}

REVIEW_ASK_EXAMPLE = (
    "Glad you're happy with the [service]! A quick Google review would mean a lot: [link]"
)

# Guardrail from the memo: only ask visibly satisfied clients for a public review,
# so an unresolved issue is never converted into a public one-star.
REVIEW_ASK_REQUIRES_POSITIVE_SIGNAL = True

# What counts as a client having "signalled satisfaction". Previously this was a
# checkbox the account manager ticked; deriving it from the feedback log instead
# means the guardrail rests on evidence rather than on someone remembering.
REVIEW_ASK_MIN_RATING = 4          # at least one rating this high, and
REVIEW_ASK_BLOCKS_ON_OPEN_ISSUE = True   # no unresolved escalation on the account

# --- Contact cadence -------------------------------------------------------
# Days that must pass before the same client is messaged again. Drawn from the
# cadence line in each segment above, so the rule is enforced rather than merely
# documented. New clients are exempt: the memo asks for a per-job ask during the
# first few jobs, which is the retention-critical window.

SEGMENT_COOLDOWN_DAYS: dict[str, int] = {
    "New": 0,
    "Regular": 90,      # "periodic, not per-job" - roughly the quarterly cycle
    "Irregular": 30,
    "Obsolete": 180,    # one-time reactivation, not a recurring campaign
}

# --- Drafting language -----------------------------------------------------
# Client servicing in Ahmedabad does not happen only in English. The placeholder
# tokens stay in Latin script in every language so the links remain pasteable.

LANGUAGES: dict[str, str] = {
    "English": "Write the message in English.",
    "Gujarati": (
        "Write the message in Gujarati, using Gujarati script. Keep it natural and "
        "conversational - the way a business owner in Ahmedabad would actually be "
        "addressed on WhatsApp, not a formal or translated register. Leave the "
        "placeholder tokens in square brackets exactly as given, in Latin script."
    ),
    "Hinglish": (
        "Write the message in Hinglish: conversational Hindi written in Latin "
        "script, mixed with the English business words that are normally typed on "
        "WhatsApp in Gujarat. Do not use Devanagari script. Leave the placeholder "
        "tokens in square brackets exactly as given."
    ),
}

DEFAULT_LANGUAGE = "English"

# --- Draft variants --------------------------------------------------------
# One call returns several options for the account manager to choose between,
# which is cheaper and faster than regenerating until something lands.

VARIANT_SEPARATOR = "==="
MAX_VARIANTS = 3

# --- Services --------------------------------------------------------------
# Service lines confirmed on muktapublicity.com during the website audit.

SERVICES: list[str] = [
    "Auto-Rickshaw Hood Branding",
    "Hoarding / Billboard Advertising",
    "Transit (Bus) Advertising",
    "Digital OOH Display",
]

# --- Message constraints ---------------------------------------------------

MAX_MESSAGE_SENTENCES = 3
FORM_LINK_PLACEHOLDER = "[feedback form link]"
REVIEW_LINK_PLACEHOLDER = "[Google review link]"

AGENCY_NAME = "Mukta Publicity"
AGENCY_CITY = "Ahmedabad"


# --- Service message library ----------------------------------------------
# Twelve ready-to-send texts per service line, tagged with the segment they suit.
# Two jobs: they are browsable in the UI as a copy-and-send bank that works with
# no API key, and the entries matching the selected service + segment are fed to
# the model as few-shot examples so a draft stays in that service's register.
#
# Regular-segment entries deliberately reference the ongoing service line rather
# than one specific job, per the cadence rule for that segment.

SERVICE_MESSAGES: dict[str, list[dict[str, str]]] = {
    "Auto-Rickshaw Hood Branding": [
        {"segment": "New", "text": "Hi [Name], thanks for trusting us with your first hood campaign. Now that the rickshaws are out on the road, how does the branding look to you? [feedback form link]"},
        {"segment": "New", "text": "Hi [Name], your hoods went up this week. We'd like to know how the print and the fitting came across before we plan the next batch: [feedback form link]"},
        {"segment": "New", "text": "Hi [Name], hope the rickshaw branding is doing its job. Since this was your first run with us, your honest read would help us set the standard: [feedback form link]"},
        {"segment": "New", "text": "Hi [Name], the hood campaign is live. Anything about the artwork or the rollout you would want handled differently next time? [feedback form link]"},
        {"segment": "Regular", "text": "Hi [Name], hope things are good at your end. Could you tell us how our rickshaw work has been holding up for you lately? [feedback form link]"},
        {"segment": "Regular", "text": "Hi [Name], we're taking stock of how the hood campaigns have run this quarter. Your view on what's working would be useful: [feedback form link]"},
        {"segment": "Regular", "text": "Hi [Name], quick one - anything about our rickshaw branding you'd want us to tighten up going forward? [feedback form link]"},
        {"segment": "Irregular", "text": "Hi [Name], good to be back on the road with you. How did the hood campaign come out this time? [feedback form link]"},
        {"segment": "Irregular", "text": "Hi [Name], glad we could take up the rickshaw work again. Your feedback helps, and we're around whenever the next batch comes up: [feedback form link]"},
        {"segment": "Irregular", "text": "Hi [Name], hope the hoods are running well. Do share how it went, and we're happy to pick up the next round whenever you need: [feedback form link]"},
        {"segment": "Obsolete", "text": "Hi [Name], it's been a while since we last did your hood branding. If something fell short back then, we'd genuinely like to hear it: [feedback form link]"},
        {"segment": "Obsolete", "text": "Hi [Name], we were going through older rickshaw campaigns and yours came up. Anything we could have done better? And if you have something planned, we're here: [feedback form link]"},
    ],
    "Hoarding / Billboard Advertising": [
        {"segment": "New", "text": "Hi [Name], your hoarding is up. Since this is our first project together, we'd like to know how the site and the print measured up: [feedback form link]"},
        {"segment": "New", "text": "Hi [Name], thanks for going with us on this billboard. How did the installation and the finish look when you saw it? [feedback form link]"},
        {"segment": "New", "text": "Hi [Name], the hoarding went live this week. Anything about the location or the artwork you'd flag before the next cycle? [feedback form link]"},
        {"segment": "New", "text": "Hi [Name], hope the board is drawing the attention you wanted. Your feedback on this first run would help us get the next one sharper: [feedback form link]"},
        {"segment": "Regular", "text": "Hi [Name], hope all's well. Could you share how our hoarding work has been serving you over the last few cycles? [feedback form link]"},
        {"segment": "Regular", "text": "Hi [Name], we're taking stock of how the billboard sites have performed for you. Anything you'd want changed? [feedback form link]"},
        {"segment": "Regular", "text": "Hi [Name], quick favour - a short read on how we're doing on your hoardings lately: [feedback form link]"},
        {"segment": "Irregular", "text": "Hi [Name], good to have your hoarding back up with us. How did this one go? [feedback form link]"},
        {"segment": "Irregular", "text": "Hi [Name], glad we could work on the billboard again. Do share your feedback, and we're around when the next site comes up: [feedback form link]"},
        {"segment": "Irregular", "text": "Hi [Name], hope the board is performing. Your view on the recent work would help, and we're happy to help whenever you plan the next: [feedback form link]"},
        {"segment": "Obsolete", "text": "Hi [Name], it's been some time since your last hoarding with us. If anything wasn't right then, we'd like to know honestly: [feedback form link]"},
        {"segment": "Obsolete", "text": "Hi [Name], we still have your earlier billboard campaign on file. Anything we could have handled better? And if a site is on your mind now, we're here: [feedback form link]"},
    ],
    "Transit (Bus) Advertising": [
        {"segment": "New", "text": "Hi [Name], your bus panels are running. Since this is your first transit campaign with us, how did the wrap turn out? [feedback form link]"},
        {"segment": "New", "text": "Hi [Name], thanks for trying us on the bus routes. We'd like your read on the print quality and the route coverage: [feedback form link]"},
        {"segment": "New", "text": "Hi [Name], the panels went out this week. Anything about the fitting or the routes you'd want adjusted next time? [feedback form link]"},
        {"segment": "New", "text": "Hi [Name], hope the buses are getting you the visibility you planned. Your feedback on this first run helps us set it up better next time: [feedback form link]"},
        {"segment": "Regular", "text": "Hi [Name], hope things are going well. Could you share how our transit work has been holding up for you lately? [feedback form link]"},
        {"segment": "Regular", "text": "Hi [Name], we're reviewing how the bus campaigns have run this season. Anything you'd want us to change? [feedback form link]"},
        {"segment": "Regular", "text": "Hi [Name], quick one - how are we doing on your bus panels these days? [feedback form link]"},
        {"segment": "Irregular", "text": "Hi [Name], good to have your panels back on the routes. How did this campaign come out? [feedback form link]"},
        {"segment": "Irregular", "text": "Hi [Name], glad we could take up the transit work again. Do share how it went, and we're around for the next one: [feedback form link]"},
        {"segment": "Irregular", "text": "Hi [Name], hope the buses are running well for you. Your feedback would help, and we're happy to pick up whenever you plan the next: [feedback form link]"},
        {"segment": "Obsolete", "text": "Hi [Name], it's been a while since your last bus campaign with us. If something didn't work then, we'd like to hear it straight: [feedback form link]"},
        {"segment": "Obsolete", "text": "Hi [Name], your transit campaign came up while we were going through past work. Anything we could have done better? And we're here if you have routes in mind: [feedback form link]"},
    ],
    "Digital OOH Display": [
        {"segment": "New", "text": "Hi [Name], your creative is live on the screen. Since this is your first digital run with us, how does it look on site? [feedback form link]"},
        {"segment": "New", "text": "Hi [Name], thanks for starting with us on digital. We'd like your read on the slot timing and how the creative renders: [feedback form link]"},
        {"segment": "New", "text": "Hi [Name], the display went live this week. Anything about the loop or the visual you'd want changed? [feedback form link]"},
        {"segment": "New", "text": "Hi [Name], hope the screen is pulling the attention you wanted. Your feedback on this first run helps us plan the next better: [feedback form link]"},
        {"segment": "Regular", "text": "Hi [Name], hope all's well. Could you tell us how our digital screens have been working out for you lately? [feedback form link]"},
        {"segment": "Regular", "text": "Hi [Name], we're reviewing how the display slots have performed for you. Anything you'd want adjusted? [feedback form link]"},
        {"segment": "Regular", "text": "Hi [Name], quick favour - a short read on how we're doing on your digital placements: [feedback form link]"},
        {"segment": "Irregular", "text": "Hi [Name], good to have your creative back on the screens. How did this run go? [feedback form link]"},
        {"segment": "Irregular", "text": "Hi [Name], glad we could take up the digital work again. Do share your feedback, and we're around when the next slot comes up: [feedback form link]"},
        {"segment": "Irregular", "text": "Hi [Name], hope the display is running well. Your view would help, and we're happy to help whenever you plan the next: [feedback form link]"},
        {"segment": "Obsolete", "text": "Hi [Name], it's been a while since your last digital campaign with us. If anything fell short, we'd genuinely like to know: [feedback form link]"},
        {"segment": "Obsolete", "text": "Hi [Name], we were looking back at earlier screen campaigns and yours came up. Anything we could have done better? And if you're planning something, we're here: [feedback form link]"},
    ],
}


def messages_for(service: str, segment: str | None = None) -> list[str]:
    """Library texts for a service, narrowed to one segment when given.

    Falls back to the full service list if a segment has no entries, so the
    caller always gets something usable.
    """
    entries = SERVICE_MESSAGES.get(service, [])
    if segment:
        matching = [e["text"] for e in entries if e["segment"] == segment]
        if matching:
            return matching
    return [e["text"] for e in entries]
