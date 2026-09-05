# Mukta Publicity — Client Outreach & Feedback Assistant

A customized GenAI prototype built for **Mukta Publicity**, an out-of-home (OOH)
advertising agency in Ahmedabad.

MBA *Artificial Intelligence & Applications* group project, IIM Sirmaur.

---

## What it does

**Tab 1 — Generate Outreach Message.** Drafts a short, WhatsApp-ready feedback
request tailored to the client's segment (New / Regular / Irregular / Obsolete)
and the service just delivered. The message is addressed to the owner, not the
firm - the two often differ, so the owner name is an editable box that seeds from
the client record and can be overridden per message. Optionally drafts a separate Google review ask.
An **"Open in WhatsApp"** button turns the draft into a
[click-to-chat link](https://faq.whatsapp.com/425247423114725) - it opens the
client's chat with the message already typed in, so the account manager's part
of the job is press Send, not copy, switch app, find contact, paste.

**Tab 3 — Message Library.** Twelve approved texts per service line, tagged by
segment, browsable and copyable with no API key. The entries matching the
selected service and segment are also injected into the drafting prompt as
few-shot examples, which is what holds a generated draft in the right register
for that service.

**Tab 2 — Review Today's Feedback.** Shows the day's incoming feedback, flags any
unresolved rating of 3 or below for a callback within 24 hours, reads each comment
for sentiment and urgency, and groups complaints into recurring themes. The
callback queue names the number to ring and can be worked down — marking a
callback done writes back to the feedback log. A per-service breakdown shows
which line is dragging the average, a trend view charts six months of history by
month and by service line, and new feedback can be recorded in place.

The operational views (metrics, log, AI triage) work on a selectable recent
window, defaulting to the last 7 days; the callback queue always shows every
unresolved escalation however old, and trends always use the full history. Triage
is capped per call so months of already-closed feedback are never re-analysed.

---

## Setup

```bash
pip install -r requirements.txt
streamlit run app.py
```

Then paste your key into the sidebar, or set it in the environment first:

```bash
export GOOGLE_API_KEY="your-key"          # macOS / Linux
$env:GOOGLE_API_KEY = "your-key"          # Windows PowerShell
```

Get a key from [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey).

The app runs without a key — the escalation queue and feedback log are fully
functional offline. Only the drafting and analysis features need the model.

---

## Layout

```
app.py                  Streamlit UI (presentation only)
mukta/
  config.py             Business rules, segments, thresholds, service lines,
                        cadence cooldowns, drafting languages, and the 48-text
                        message library (12 per service)
  data.py               Domain models and CSV loaders
  prompts.py            Prompt construction, kept out of the UI
  gemini.py             API wrapper (google-genai) — retries, JSON parsing, graceful failure
  services.py           Orchestration; enforces policy in code, not in prompts
test_rules.py           Tests for every rule the model is not trusted with
tools_client_health.py  Churn risk, recovery rate and issue frequency, printed
                        for the report - kept out of the app to keep it simple
tools_generate_history.py  Regenerates the sample feedback history
data/
  clients.csv           Sample client master
  feedback.csv          Sample feedback log - 97 rows across Mar-Aug 2026 with
                        both a 1-5 rating and a 0-10 NPS score, written back to
                        when resolving
  outreach_log.csv      What was sent to whom, created on first send
assets/
  mukta-logo.jpg        Agency logo, served locally rather than hotlinked
.streamlit/
  config.toml           Brand theme (red #C40C14 sampled from muktapublicity.com)
```

---

## Design decisions worth defending in the report

**The model writes; the rules decide.** Escalation is a Python comparison against
a threshold, not something the model is asked to judge. If the API is down, the
callback queue still works. The model only handles language: drafting copy,
reading sentiment, naming themes.

**The review ask is gated on evidence, in code.** A public Google review is only
solicited from a client the feedback log shows to be satisfied: at least one
rating of 4 or better, and nothing unresolved open on the account. The check is
`services.review_eligibility`, called from `generate_outreach` — not a prompt
instruction, which can be talked around, and not a checkbox, which depends on
someone remembering. On the sample data this blocks Urban Threads, whose screen
was dark for two evenings, from ever being asked for a public review.

**Contact cadence is enforced, not just documented.** Each segment carries a
cooldown in `config.SEGMENT_COOLDOWN_DAYS`, checked against the outreach log
before a draft goes out. Regular clients sit on a 90-day cadence because per-job
asks to regulars suppress response rates; New clients are exempt, since the memo
asks for a per-job ask during the retention-critical first few jobs.

**Two measures, because they answer different questions.** The 1-5 star rating is
a CSAT measure - how did this job go. NPS asks whether the client would put their
name behind the agency, which is what predicts referral, and OOH in Ahmedabad runs
largely on referral. They are collected separately rather than derived from one
another, because a 4/5 job does not reliably produce a promoter.

These next three run in `tools_client_health.py` rather than the app. They are
analysis for the report, not something an account manager acts on mid-conversation,
and a fourth tab made the tool harder to demo than it was worth.

**Churn is predicted from behaviour, not read off a label.** The four segments are
a churn funnel, but in the CSV they are a label someone typed. `services.churn_risk`
recomputes the same funnel from what clients actually do - rating trend, silence
measured against that segment's normal return interval, open complaints, recent
detractors. Satisfaction falls before ordering stops, so this flags a client while
there is still something to save. On the sample book it puts Shreeji Foods at high
risk: ratings down 4.67 to 3.25 and no work for months, while the CSV still calls
them merely Irregular.

**Resolving is not the same as recovering.** Marking a complaint resolved closes a
row; it does not prove the client came back. `services.recovery_outcomes` reads what
each client said *after* a resolved complaint. On the sample data only 30% of
resolved complaints were followed by ratings of 4/5 or better - which is the sort of
finding a dashboard of open-ticket counts would never surface.

**Issue counts are normalised.** Mentions are reported per 100 responses, not raw,
because August carries far more responses than March and raw counts would make a
busy month look like a worsening one.

**Trends are aggregated in code, then handed to the model.** The themes summary
receives a month-by-month table computed in Python plus the most recent comments,
not six months of raw rows. The model names patterns and reads direction; it is
never asked to do the arithmetic, and it is told to ground every trend claim in
the figures given and to say when a line has too little data.

**Language is a first-class option.** Client servicing in Ahmedabad does not
happen only in English, so drafts can be produced in Gujarati or Hinglish. The
link placeholders stay in Latin script in every language so they remain
pasteable.

**Segment-aware, not template-driven.** Each of the four segments carries a
distinct goal, tone, and cadence drawn from the agency's internal recommendations.
A regular client is deliberately *not* messaged about a specific job, because
per-job asks to regulars suppress response rates over time.

**Nothing sends automatically.** Every draft lands in an editable box for the
account manager to review before it reaches a client. The prototype simulates the
WhatsApp step rather than integrating a Business API — that integration is
documented as the production upgrade path.

**Model output is not trusted blindly.** The triage layer separates rule-based
escalation from model-flagged urgency, so the two are visible as distinct signals.
A malformed or missing model response degrades the tool to its deterministic
core instead of breaking it.

---

## Data and privacy

All client and feedback records in `data/` are sample records created for this
project. The six months of history were generated with a seeded script and carry
a deliberate shape - hoardings degrade from April and recover in August, digital
OOH weakens late - so the trend view has something real to summarise. They are
illustrative, not observed. They mirror the structure of a real client book but contain no
real business names or identifying information. All ten records currently share a
single real phone number, supplied by the developer so the WhatsApp click-to-chat
integration (below) can be tested end-to-end - swap `data/clients.csv` for
distinct numbers before this leaves prototype use. Comment text is sent to the
Gemini API for analysis; a production deployment would need explicit client
consent for this and a documented retention policy.

---

## Production upgrade path

| Prototype | Production |
|---|---|
| CSV files | Client database or CRM |
| "Open in WhatsApp" link, human presses Send | WhatsApp Business API (WATI / AiSensy / Interakt) - no click at all, but needs Meta business verification, per-conversation cost, and approved templates for a first message |
| "Mark as sent" logged by hand | Delivery and read receipts from the API |
| Sidebar API key | Server-side secret management |
| Escalation shown on screen | Assigned to a named owner with a notification |
| Churn risk read on demand | Weekly digest to the account team |
| Issue keywords hand-listed | Learned from the corpus, reviewed quarterly |
| Single-user CSV writeback | Concurrent-safe store with an audit trail |

## Tests

```bash
python test_rules.py
```

63 tests covering the escalation threshold and SLA window, the review-ask gate,
segment cooldowns, service isolation in both the library and the prompt, owner
addressing, model-output handling, CSV writeback, trend aggregation, NPS banding
and arithmetic, churn scoring, recovery measurement, issue-rate normalisation, and
the caps on what is sent to the model. They run against copies,
never the sample data, and need neither an API key nor a test framework.
