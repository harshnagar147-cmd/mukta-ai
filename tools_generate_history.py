"""Extend feedback.csv with six months of history so trends mean something.

The existing ten rows (F001-F010, 20-23 Aug) are left byte-identical: they are
what the callback queue demo and the tests rely on. History is appended with
later ids but earlier timestamps, which is fine - nothing assumes ids are
chronological.

The data carries a deliberate shape rather than being uniform noise, because a
flat random series has no trend to summarise:

  * hoardings degrade from April, bottom out in July, recover in August
  * digital OOH is strong all year until a new problem appears in August
  * rickshaw and transit hold steady and good throughout

so the summary should be able to say: overall dipped mid-year and is recovering,
one line drove the dip, and a different line is the new concern.
"""

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

random.seed(20260823)

FEEDBACK = Path("data/feedback.csv")
CLIENTS = Path("data/clients.csv")

HOARDING = "Hoarding / Billboard Advertising"
TRANSIT = "Transit (Bus) Advertising"
RICKSHAW = "Auto-Rickshaw Hood Branding"
DIGITAL = "Digital OOH Display"

# Only clients who were actually active Mar-Aug. The three New clients start in
# August, and the two Obsolete ones stopped ordering before March - giving them
# history would contradict their own records.
ACTIVE = {
    "C009": {"services": [(RICKSHAW, 6), (TRANSIT, 2), (DIGITAL, 1)], "per_month": 5},
    "C003": {"services": [(HOARDING, 5), (DIGITAL, 2)], "per_month": 4},
    "C004": {"services": [(TRANSIT, 4), (HOARDING, 2), (DIGITAL, 2)], "per_month": 4},
    "C006": {"services": [(HOARDING, 2), (DIGITAL, 1)], "per_month": 1},
    "C005": {"services": [(RICKSHAW, 1)], "per_month": 0},  # festive only, below
}
FESTIVE_MONTHS = {3, 5, 7}   # Kiran Motors orders around festive season only

MONTH_BASE = {3: 4.3, 4: 4.2, 5: 3.9, 6: 3.6, 7: 3.3, 8: 3.9}
SERVICE_DRIFT = {
    HOARDING: {3: 0.2, 4: 0.0, 5: -0.6, 6: -1.0, 7: -1.2, 8: -0.4},
    DIGITAL:  {3: 0.3, 4: 0.3, 5: 0.2, 6: 0.0, 7: -0.1, 8: -1.0},
    RICKSHAW: {3: 0.3, 4: 0.3, 5: 0.3, 6: 0.2, 7: 0.2, 8: 0.3},
    TRANSIT:  {3: 0.4, 4: 0.4, 5: 0.3, 6: 0.3, 7: 0.2, 8: 0.4},
}

COMMENTS = {
    (HOARDING, "low"): [
        "Installation slipped by three days and we heard nothing until we called.",
        "The site was not ready on the agreed date. Second time this quarter.",
        "Print came out duller than the proof we approved.",
        "Hoarding went up crooked and took another week to correct.",
        "We had to chase for the mounting photos every single day.",
        "Campaign started four days late so we lost the weekend footfall.",
        "Nobody informed us the site permit was still pending.",
        "The flex tore within a fortnight and replacement was slow.",
    ],
    (HOARDING, "mid"): [
        "Work was fine but the updates could have been more regular.",
        "Site is good, though the install ran a day behind.",
        "Print is acceptable but not quite the sample shade.",
        "Went up on time, finishing at the edges could be neater.",
        "Reasonable job overall, communication is the weak point.",
    ],
    (HOARDING, "high"): [
        "Board went up on schedule and looks sharp from the road.",
        "Good site selection, we are seeing walk-ins mention it.",
        "Clean install and the photos came through the same evening.",
        "Print quality matched the proof exactly this time.",
        "Smooth cycle from artwork to mounting, no chasing needed.",
    ],
    (TRANSIT, "low"): [
        "Two buses ran with the panel peeling at the corner.",
        "Route coverage was not what we agreed in the plan.",
        "Wrap looked faded within three weeks.",
    ],
    (TRANSIT, "mid"): [
        "Panels are fine, though a couple of buses went out late.",
        "Decent run, print could be a shade sharper.",
        "Coverage was alright but we expected the western routes too.",
    ],
    (TRANSIT, "high"): [
        "Panels look excellent and the routes were exactly as planned.",
        "Quick turnaround on the panel change, thank you.",
        "Wrap quality is holding up well after a month.",
        "Good visibility on the AMTS routes, happy with this one.",
        "Fitting was neat and the buses went out on schedule.",
    ],
    (RICKSHAW, "low"): [
        "Several hoods were fitted loose and came off within days.",
        "The fitting team turned up late two days running.",
    ],
    (RICKSHAW, "mid"): [
        "Hoods look fine, the rollout took longer than quoted.",
        "Design is good but a few autos were missed in the first batch.",
        "Acceptable work, proof approval took too many rounds.",
    ],
    (RICKSHAW, "high"): [
        "Hoods look clean and the coverage across the city is good.",
        "Consistent as always, no complaints from our side.",
        "Fitting was quick and the finish is holding up well.",
        "Good batch, the colours came out exactly right.",
        "Rollout was on time and the count matched the order.",
        "Very happy with the visibility we are getting from this.",
    ],
    (DIGITAL, "low"): [
        "Screen was dark for two evenings and nobody answered the phone.",
        "Our slot was skipped repeatedly during peak hours.",
        "The creative was running at the wrong aspect ratio.",
    ],
    (DIGITAL, "mid"): [
        "Runs fine but we would like a report on actual play counts.",
        "Placement is good, slot timing could be better.",
    ],
    (DIGITAL, "high"): [
        "Creative looks crisp on the screen and the slot timing is good.",
        "Excellent placement, customers have started mentioning it.",
        "Loop timing works well for our evening audience.",
        "Screen quality is great and uptime has been solid.",
    ],
}


def band(rating: int) -> str:
    return "low" if rating <= 2 else ("mid" if rating == 3 else "high")


def pick_service(options):
    pool = [s for s, weight in options for _ in range(weight)]
    return random.choice(pool)


def make_rating(service: str, month: int) -> int:
    score = MONTH_BASE[month] + SERVICE_DRIFT[service][month] + random.gauss(0, 0.45)
    return max(1, min(5, round(score)))


rows = []
next_id = 11

for month in range(3, 9):
    for client_id, spec in ACTIVE.items():
        count = spec["per_month"]
        if client_id == "C005":
            count = 1 if month in FESTIVE_MONTHS else 0
        for _ in range(count):
            day = random.randint(1, 27)
            # August history stops before the 20th - the original rows own that window.
            if month == 8:
                day = random.randint(1, 18)
            when = datetime(2026, month, day, random.randint(9, 18), random.choice([5, 15, 25, 40, 50]))
            service = pick_service(spec["services"])
            rating = make_rating(service, month)
            comment = random.choice(COMMENTS[(service, band(rating))])
            rows.append({
                "feedback_id": f"F{next_id:03d}",
                "client_id": client_id,
                "received_at": when.strftime("%Y-%m-%dT%H:%M:%S"),
                "rating": rating,
                "comment": comment,
                "service": service,
                # Historical complaints were worked and closed; leaving them open
                # would flood today's callback queue with months-old rows.
                "resolved": True if rating <= 3 else False,
            })
            next_id += 1

rows.sort(key=lambda r: r["received_at"])
# Renumber in date order now that ids no longer collide with the original ten.
for index, row in enumerate(rows, start=11):
    row["feedback_id"] = f"F{index:03d}"

existing = FEEDBACK.read_text(encoding="utf-8").rstrip().splitlines()
header = existing[0]
fields = header.split(",")

with open(FEEDBACK, "a", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=fields)
    for row in rows:
        writer.writerow(row)

print(f"appended {len(rows)} historical rows (F011-F{10 + len(rows):03d})")

# Keep jobs_completed plausible against the new feedback volume.
counts = {}
for row in rows:
    counts[row["client_id"]] = counts.get(row["client_id"], 0) + 1

lines = CLIENTS.read_text(encoding="utf-8").rstrip().splitlines()
cols = lines[0].split(",")
cid_i, jobs_i = cols.index("client_id"), cols.index("jobs_completed")
out = [lines[0]]
for line in lines[1:]:
    parts = line.split(",")
    extra = counts.get(parts[cid_i], 0)
    if extra:
        parts[jobs_i] = str(int(parts[jobs_i]) + extra)
    out.append(",".join(parts))
CLIENTS.write_text("\n".join(out) + "\n", encoding="utf-8")
print("jobs_completed topped up:", counts)
