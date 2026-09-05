"""Mukta Publicity - Client Outreach & Feedback Assistant.

Streamlit entry point. Run with:  streamlit run app.py
"""

from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from mukta import config, data, services
from mukta.gemini import SDK_AVAILABLE, GeminiClient, GeminiError

LOGO_PATH = Path(__file__).parent / "assets" / "mukta-logo.jpg"

st.set_page_config(
    page_title="Mukta Publicity - Client Assistant",
    page_icon=str(LOGO_PATH) if LOGO_PATH.exists() else "📣",
    layout="wide",
)


# --- Branding --------------------------------------------------------------
# Palette sampled from muktapublicity.com; the logo is served from assets/ so
# the app keeps its branding without reaching out to their site at runtime.

BRAND_RED = "#C40C14"
BRAND_INK = "#2C2C2C"
BRAND_GREY = "#64686D"


@st.cache_data(show_spinner=False)
def _logo_data_uri() -> str | None:
    if not LOGO_PATH.exists():
        return None
    encoded = base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


st.markdown(
    f"""
    <style>
      .brand-header {{
          display: flex; align-items: center; gap: 1.15rem;
          padding: 0.2rem 0 0.9rem;
          border-bottom: 3px solid {BRAND_RED};
          margin-bottom: 1.4rem;
      }}
      .brand-header img {{ height: 62px; width: auto; }}
      .brand-title {{
          font-size: 1.85rem; font-weight: 700; line-height: 1.15;
          color: {BRAND_INK};
      }}
      .brand-sub {{ font-size: 0.88rem; color: {BRAND_GREY}; margin-top: 0.2rem; }}
      .stTabs [aria-selected="true"] {{ color: {BRAND_RED} !important; }}
      [data-testid="stMetricValue"] {{ color: {BRAND_RED}; }}
      h2, h3 {{ color: {BRAND_INK}; }}
    </style>
    """,
    unsafe_allow_html=True,
)

if LOGO_PATH.exists():
    st.logo(str(LOGO_PATH), size="large")


# --- Data (cached) ---------------------------------------------------------

@st.cache_data(show_spinner=False)
def _load():
    return data.load_clients(), data.load_feedback()


try:
    CLIENTS, FEEDBACK = _load()
except FileNotFoundError as exc:
    st.error(f"Could not load sample data: {exc}")
    st.stop()


PHONE_BY_ID = {c.client_id: c.phone for c in CLIENTS}
CLIENT_NAME_BY_ID = {c.client_id: c.name for c in CLIENTS}


# --- Sidebar ---------------------------------------------------------------

with st.sidebar:
    st.header("Configuration")

    if not SDK_AVAILABLE:
        st.warning("`google-generativeai` is not installed. Run `pip install -r requirements.txt`.")

    api_key = st.text_input(
        "Gemini API key",
        value=os.environ.get("GOOGLE_API_KEY", ""),
        type="password",
        help="Create one at aistudio.google.com/app/apikey",
    )
    model_name = st.text_input("Model", value=config.DEFAULT_MODEL)

    st.divider()
    st.caption(
        "**Prototype notice.** Client and feedback records shown here are sample data "
        "created for this course project, not live Mukta Publicity records. No real "
        "client contact details are stored or transmitted."
    )
    st.caption(
        f"**Escalation rule.** Any unresolved rating of {config.ESCALATION_RATING_THRESHOLD} "
        f"or below is flagged for a callback within {config.ESCALATION_WINDOW_HOURS} hours."
    )

GEMINI = GeminiClient(api_key=api_key or None, model_name=model_name or config.DEFAULT_MODEL)

if not GEMINI.enabled:
    st.info("Add a Gemini API key in the sidebar to enable message drafting and feedback analysis. "
            "The escalation queue below works without it.", icon="🔑")


# --- Header ----------------------------------------------------------------

_logo = _logo_data_uri()
_logo_img = f'<img src="{_logo}" alt="Mukta Publicity">' if _logo else ""
st.markdown(
    f"""
    <div class="brand-header">
      {_logo_img}
      <div>
        <div class="brand-title">Client Outreach &amp; Feedback Assistant</div>
        <div class="brand-sub">Customized GenAI prototype · MBA AI &amp; Applications, IIM Sirmaur</div>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

tab_outreach, tab_feedback, tab_library = st.tabs(
    [
        "✍️  Generate Outreach Message",
        "📊  Review Today's Feedback",
        "📚  Message Library",
    ]
)


# =============================================================================
# TAB 1 — Outreach
# =============================================================================

with tab_outreach:
    left, right = st.columns([1, 1.4], gap="large")

    with left:
        st.subheader("Client")

        selected_label = st.selectbox(
            "Select a client",
            options=[c.label for c in CLIENTS],
        )
        client = next(c for c in CLIENTS if c.label == selected_label)

        # Keyed per client so switching clients re-seeds the box from the record
        # instead of holding the previous client's owner.
        owner_name = st.text_input(
            "Owner / contact person",
            value=client.contact_person,
            key=f"owner_{client.client_id}",
            help="Who the message is addressed to. The firm name is often not the "
                 "person's name - edit this if the record is out of date.",
        )

        if client.phone:
            st.caption(f":grey[Contact: {client.phone} - one shared test number for this prototype.]")

        service = st.selectbox(
            "Service delivered",
            options=config.SERVICES,
            index=config.SERVICES.index(client.primary_service)
            if client.primary_service in config.SERVICES else 0,
        )

        if client.segment == "Regular":
            st.caption(
                ":grey[Regular clients are deliberately **not** messaged about one "
                "specific job - per-job asks to regulars suppress response rates - so "
                "the draft will not name this service. It is used for context only.]"
            )

        lang_col, var_col = st.columns(2)
        language = lang_col.selectbox(
            "Language",
            options=list(config.LANGUAGES),
            key="language",
            help="Client servicing in Ahmedabad does not happen only in English.",
        )
        variant_count = var_col.selectbox(
            "Drafts to produce",
            options=list(range(1, config.MAX_VARIANTS + 1)),
            index=0,
            key="variant_count",
            help="Several options come back in a single call - cheaper than "
                 "regenerating until one lands.",
        )

        c1, c2, c3 = st.columns(3)
        c1.metric("Segment", client.segment)
        c2.metric("Jobs done", client.jobs_completed)
        c3.metric("Days since job", client.days_since_last_job)

        segment_info = config.SEGMENTS[client.segment]
        with st.expander("Why this segment is messaged differently"):
            st.markdown(f"**Goal.** {segment_info['goal']}")
            st.markdown(f"**Tone.** {segment_info['tone']}")
            st.markdown(f"**Cadence.** {segment_info['cadence']}")

        if client.notes:
            st.caption(f"Account note: {client.notes}")

        st.divider()

        # Satisfaction is read from the feedback log rather than self-declared, so
        # the guardrail cannot be bypassed by ticking a box from memory.
        verdict = services.review_eligibility(client, FEEDBACK)
        if verdict.eligible:
            st.success(f"Review ask allowed — {verdict.reason}", icon="✅")
        else:
            st.warning(f"Review ask blocked — {verdict.reason}", icon="🚫")
        if verdict.evidence:
            st.caption(f':grey["{verdict.evidence}"]')

        want_review = st.checkbox(
            "Also draft a Google review ask",
            help="Permitted only when the feedback log evidences satisfaction. The "
                 "check runs in code, not in the prompt.",
        )

        # Cadence, enforced rather than merely documented in the segment rules.
        cooldown = services.cooldown_status(
            client, data.last_contacted(data.load_outreach_log(), client.client_id)
        )
        if cooldown.within_cooldown:
            st.warning(cooldown.message, icon="⏳")
        else:
            st.caption(f":grey[{cooldown.message}]")
        extra = st.text_area(
            "Anything specific to mention? (optional)",
            placeholder="e.g. campaign ran two weeks longer than planned",
            height=80,
        )

        generate = st.button("Generate message", type="primary", width="stretch")

    with right:
        st.subheader("Draft")

        if generate:
            if not GEMINI.enabled:
                st.error("Add a Gemini API key in the sidebar first.")
            else:
                with st.spinner("Drafting…"):
                    try:
                        result = services.generate_outreach(
                            GEMINI,
                            client,
                            service,
                            request_review_ask=want_review,
                            feedback=FEEDBACK,
                            owner_name=owner_name,
                            language=language,
                            variant_count=variant_count,
                            extra_context=extra,
                        )
                        st.session_state["outreach"] = result
                        st.session_state["outreach_for"] = (
                            client.client_id, service, owner_name, language,
                        )
                        # Widgets keyed by a stable string keep their first value
                        # forever; bumping this nonce gives each draft a fresh
                        # text_area so a regenerated message actually appears.
                        st.session_state["draft_nonce"] = (
                            st.session_state.get("draft_nonce", 0) + 1
                        )
                    except GeminiError as exc:
                        st.error(str(exc))

        result = st.session_state.get("outreach")
        if result and st.session_state.get("outreach_for") != (
            client.client_id,
            service,
            owner_name,
            language,
        ):
            # Selection moved on — do not leave another client's draft on screen.
            result = None

        if result:
            if result.blocked_reason:
                st.warning(result.blocked_reason, icon="⚠️")

            nonce = st.session_state.get("draft_nonce", 0)

            chosen = result.message
            if len(result.variants) > 1:
                chosen = st.radio(
                    f"{len(result.variants)} drafts — pick one",
                    options=result.variants,
                    key=f"variant_pick_{nonce}",
                    format_func=lambda m: (m[:90] + "…") if len(m) > 90 else m,
                )

            edited = st.text_area(
                "Feedback request — review and edit before sending",
                value=chosen,
                height=140,
                key=f"msg_box_{nonce}_{result.variants.index(chosen) if chosen in result.variants else 0}",
            )

            review_edited = None
            if result.review_ask:
                review_edited = st.text_area(
                    "Google review ask — send as a separate message",
                    value=result.review_ask,
                    height=110,
                    key=f"review_box_{nonce}",
                )

            wa_col1, wa_col2 = st.columns(2)
            wa_link = services.whatsapp_link(client.phone, edited)
            wa_col1.link_button(
                "Open in WhatsApp", wa_link, width="stretch", disabled=not wa_link,
                help=f"Opens {client.contact_person}'s chat with this message pre-filled. "
                     "You still press Send inside WhatsApp — a human always reviews the "
                     "wording before a client sees it." if wa_link else "No phone number on file.",
            )
            if review_edited:
                wa_review_link = services.whatsapp_link(client.phone, review_edited)
                wa_col2.link_button(
                    "Open review ask in WhatsApp", wa_review_link, width="stretch",
                    disabled=not wa_review_link,
                    help="Opens the same chat with the review ask instead — send it as a "
                         "second message, after the feedback request.",
                )

            st.caption(
                "Nothing is sent automatically. “Open in WhatsApp” loads the message into the "
                "client's chat; a human still presses Send there, so the wording always gets "
                "one last look before it goes."
            )

            if st.button("Mark as sent", key=f"log_sent_{nonce}", width="stretch"):
                data.log_outreach(
                    client, owner_name or client.contact_person,
                    service, result.language, edited,
                )
                st.success(
                    f"Logged against {client.name}. The cadence check will now count "
                    "from today.",
                    icon="📕",
                )

            recent = data.load_outreach_log()
            if not recent.empty:
                with st.expander(f"Outreach log ({len(recent)} sent)"):
                    st.dataframe(
                        recent.sort_values("sent_at", ascending=False)[
                            ["sent_at", "client_name", "segment", "service", "language"]
                        ],
                        width="stretch",
                        hide_index=True,
                    )
        else:
            st.info("Select a client and generate a message to see the draft here.")


# =============================================================================
# TAB 2 — Feedback
# =============================================================================

with tab_feedback:
    win_col, _ = st.columns([1, 3])
    window_label = win_col.selectbox(
        "Reviewing", options=list(config.REVIEW_WINDOWS), key="review_window"
    )
    window_days = config.REVIEW_WINDOWS[window_label]

    if window_days is None:
        scoped = FEEDBACK
    else:
        cutoff = datetime.now() - timedelta(days=window_days)
        scoped = [f for f in FEEDBACK if f.received_at >= cutoff]

    st.caption(
        f":grey[{len(scoped)} of {len(FEEDBACK)} responses in this window. "
        "The callback queue below always shows every unresolved escalation, "
        "however old, and trends always use the full history.]"
    )

    triage_items = sorted(scoped, key=lambda f: f.received_at, reverse=True)[
        : config.MAX_TRIAGE_ITEMS
    ]

    stats = services.compute_stats(scoped)

    nps = services.nps_summary(scoped)

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Feedback received", stats.total)
    m2.metric("Needs callback", stats.escalations)
    m3.metric("Past 24 hrs", stats.overdue, delta_color="inverse")
    m4.metric("Average rating", f"{stats.average_rating} / 5")
    m5.metric(
        "NPS", f"{nps.score:+d}",
        help=f"{nps.promoters} promoters, {nps.passives} passives, "
             f"{nps.detractors} detractors of {nps.responses} who scored 0-10. "
             "Percent promoters minus percent detractors.",
    )

    breakdown = services.service_breakdown(scoped)
    if breakdown:
        with st.expander(
            f"By service line — {breakdown[0].service} is lowest at "
            f"{breakdown[0].average_rating}/5",
            expanded=True,
        ):
            st.caption(
                "A single average hides which line is dragging. Worst first."
            )
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Service": s.service,
                            "Avg rating": s.average_rating,
                            "Feedback": s.count,
                            "Open escalations": s.escalations,
                        }
                        for s in breakdown
                    ]
                ),
                width="stretch",
                hide_index=True,
            )
            st.bar_chart(
                pd.DataFrame(
                    {"Avg rating": [s.average_rating for s in breakdown]},
                    index=[s.service for s in breakdown],
                ),
                horizontal=True,
            )

    trend = services.monthly_trend(FEEDBACK)
    if len(trend) > 1:
        with st.expander("Trend over time", expanded=True):
            st.markdown(f"**{services.trend_headline(FEEDBACK)}**")

            months = [m.label for m in trend]
            st.line_chart(
                pd.DataFrame({"Average rating": [m.average_rating for m in trend]}, index=months),
                y_label="Average rating",
            )

            nps_series = services.nps_trend(FEEDBACK)
            if len(nps_series) > 1:
                st.caption(
                    "Net Promoter Score by month — would they put their name behind "
                    "you, which the star rating does not ask."
                )
                st.line_chart(
                    pd.DataFrame(
                        {"NPS": [score for _, score, _ in nps_series]},
                        index=[label for label, _, _ in nps_series],
                    ),
                    y_label="NPS",
                )

            per_service = services.service_trend(FEEDBACK)
            order = {m.month: i for i, m in enumerate(trend)}
            st.caption("By service line — the headline average hides which line moved.")
            st.line_chart(
                pd.DataFrame(
                    {
                        service: [values.get(m.month) for m in trend]
                        for service, values in per_service.items()
                    },
                    index=months,
                ),
                y_label="Average rating",
            )

            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Month": m.label,
                            "Responses": m.count,
                            "Avg rating": m.average_rating,
                            "Rated 3 or below": m.escalations,
                        }
                        for m in trend
                    ]
                ),
                width="stretch",
                hide_index=True,
            )

    st.divider()

    flagged = data.escalations(FEEDBACK)
    if flagged:
        st.error(
            f"{len(flagged)} client(s) rated {config.ESCALATION_RATING_THRESHOLD} or below "
            f"and are awaiting a callback.",
            icon="🚨",
        )
    else:
        st.success("No open escalations.", icon="✅")

    if flagged:
        st.subheader("Callback queue")
        for f in flagged:
            name = CLIENT_NAME_BY_ID.get(f.client_id, f.client_id)
            phone = PHONE_BY_ID.get(f.client_id, "")
            overdue = f.breaches_sla
            row = st.columns([5, 2, 2])
            row[0].markdown(
                f"{'🔴' if overdue else '🟠'} **{name}** — {f.rating}/5 — {f.service}  \n"
                f":grey[{f.comment}]"
            )
            row[1].markdown(
                f"**{'OVERDUE' if overdue else 'Call'}**  \n"
                f"{phone or '—'}  \n"
                f":grey[{f.hours_open:.0f} hrs open]"
            )
            if row[2].button("Mark resolved", key=f"resolve_{f.feedback_id}"):
                if data.mark_resolved(f.feedback_id):
                    _load.clear()
                    st.session_state.pop("triaged", None)
                    st.toast(f"{name} marked resolved.", icon="✅")
                    st.rerun()
                else:
                    st.error(f"Could not find {f.feedback_id} to update.")
        st.divider()

    run_triage = st.button(
        f"Analyse {len(triage_items)} recent with AI", type="primary",
        help="Reads each comment for sentiment, urgency, and issue type, and suggests an "
             f"opening line for the callback. Capped at {config.MAX_TRIAGE_ITEMS} rows per "
             "call so a long history is not re-analysed every time.",
    )

    # Signature of what would be analysed, so re-clicking with nothing changed
    # reuses the previous answer instead of paying for it again.
    triage_signature = tuple(
        (f.feedback_id, f.rating, f.resolved, f.comment) for f in triage_items
    )

    if run_triage:
        if not GEMINI.enabled:
            st.error("Add a Gemini API key in the sidebar first.")
        elif st.session_state.get("triage_signature") == triage_signature and \
                st.session_state.get("triaged"):
            st.info("Feedback is unchanged since the last analysis — reusing it.", icon="♻️")
        else:
            with st.spinner("Reading feedback…"):
                triaged, warning = services.triage_feedback(GEMINI, triage_items, CLIENTS)
                st.session_state["triaged"] = triaged
                st.session_state["triage_warning"] = warning
                st.session_state["triage_signature"] = triage_signature

    triaged = st.session_state.get("triaged")
    warning = st.session_state.get("triage_warning")

    if warning:
        st.warning(f"AI analysis unavailable — showing rule-based escalations only. ({warning})")

    if triaged:
        st.subheader("Triaged feedback")
        for item in sorted(triaged, key=lambda t: (not t.needs_escalation, t.feedback.rating)):
            f = item.feedback
            badge = "🚨" if item.needs_escalation else ("⚠️" if item.model_flagged else "·")
            header = f"{badge}  {item.client_name} — {f.rating}/5 — {f.service}"

            with st.expander(header, expanded=item.needs_escalation):
                st.write(f.comment)

                phone = PHONE_BY_ID.get(f.client_id, "")
                if phone:
                    st.caption(f":grey[Contact: {phone}]")

                cols = st.columns(3)
                cols[0].caption(f"**Sentiment:** {item.sentiment or '—'}")
                cols[1].caption(f"**Urgency:** {item.urgency or '—'}")
                cols[2].caption(f"**Issue:** {item.issue_type or '—'}")

                if item.needs_escalation:
                    phone = PHONE_BY_ID.get(f.client_id, "")
                    st.markdown(
                        f"**Action:** callback within {config.ESCALATION_WINDOW_HOURS} hours "
                        f"({f.hours_open:.0f} hrs open)."
                        + (f"  \n**Call {item.client_name} on {phone}**" if phone else "")
                    )
                if item.model_flagged:
                    st.markdown(
                        "**Note:** rating did not trip the escalation rule, but the comment "
                        "reads as urgent — worth a look."
                    )
                if item.suggested_first_line:
                    st.info(f"Suggested opening line: {item.suggested_first_line}")
    else:
        st.subheader(f"Feedback log — {window_label.lower()}")
        st.dataframe(
            data.feedback_to_frame(
                sorted(scoped, key=lambda f: f.received_at, reverse=True), CLIENTS
            ),
            width="stretch",
            hide_index=True,
        )

    with st.expander("Add feedback"):
        st.caption(
            "Records a new response against a client. A rating of "
            f"{config.ESCALATION_RATING_THRESHOLD} or below joins the callback queue "
            "immediately."
        )
        with st.form("add_feedback", clear_on_submit=True):
            fa, fb_ = st.columns(2)
            new_client = fa.selectbox(
                "Client", options=[c.label for c in CLIENTS], key="nf_client"
            )
            new_service = fb_.selectbox(
                "Service", options=config.SERVICES, key="nf_service"
            )
            new_rating = st.slider("Rating", 1, 5, 3, key="nf_rating")
            new_comment = st.text_area(
                "Comment", placeholder="What did the client say?", key="nf_comment"
            )
            submitted = st.form_submit_button("Record feedback", type="primary")

        if submitted:
            if not new_comment.strip():
                st.error("Add the client's comment before recording.")
            else:
                target = next(c for c in CLIENTS if c.label == new_client)
                new_id = data.append_feedback(
                    target.client_id, new_rating, new_comment, new_service
                )
                _load.clear()
                st.session_state.pop("triaged", None)
                st.session_state.pop("triage_signature", None)
                st.toast(f"Recorded {new_id} for {target.name}.", icon="📝")
                st.rerun()

    st.divider()
    st.subheader("Recurring themes")
    st.caption("Groups complaints into patterns worth fixing structurally, rather than "
               "client by client.")

    if st.button("Summarise themes"):
        if not GEMINI.enabled:
            st.error("Add a Gemini API key in the sidebar first.")
        else:
            with st.spinner("Looking for patterns…"):
                try:
                    st.session_state["themes"] = services.summarize_themes(
                        GEMINI, FEEDBACK, CLIENTS
                    )
                except GeminiError as exc:
                    st.error(str(exc))

    if st.session_state.get("themes"):
        st.info(st.session_state["themes"])


# =============================================================================
# TAB 3 - Message library
# =============================================================================

with tab_library:
    st.caption(
        "Twelve approved texts per service line, tagged by segment. These need no "
        "API key - copy one and send it as is. The texts matching the selected "
        "service and segment are also fed to the model on the first tab, which is "
        "what keeps a generated draft in the right register for that service."
    )

    lib_left, lib_right = st.columns([1, 2], gap="large")

    with lib_left:
        lib_service = st.selectbox(
            "Service line", options=config.SERVICES, key="lib_service"
        )
        lib_segment = st.radio(
            "Segment",
            options=["All segments", *config.SEGMENTS],
            key="lib_segment",
        )

        entries = config.SERVICE_MESSAGES.get(lib_service, [])
        if lib_segment != "All segments":
            entries = [e for e in entries if e["segment"] == lib_segment]

        st.metric("Texts shown", len(entries))

        rows = [
            {"service": s, "segment": e["segment"], "text": e["text"]}
            for s, items in config.SERVICE_MESSAGES.items()
            for e in items
        ]
        st.download_button(
            "Download all 48 as CSV",
            data=pd.DataFrame(rows).to_csv(index=False).encode("utf-8"),
            file_name="mukta_message_library.csv",
            mime="text/csv",
            width="stretch",
        )

    with lib_right:
        if not entries:
            st.info("No texts for that combination.")
        for i, entry in enumerate(entries, start=1):
            st.markdown(f"**{i}.** &nbsp; `{entry['segment']}`", unsafe_allow_html=True)
            st.code(entry["text"], language=None, wrap_lines=True)

        if lib_segment == "Regular":
            st.caption(
                ":grey[Regular texts deliberately refer to the ongoing service line "
                "rather than one specific job - per-job asks to regulars suppress "
                "response rates over time.]"
            )
