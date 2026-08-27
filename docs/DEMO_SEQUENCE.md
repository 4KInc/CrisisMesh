# CrisisMesh — Demo Sequence (Active Shooter)

**Live:** https://crisismesh-1031148889398.us-central1.run.app
**Slack room:** `#fr-live-demo` · **WhatsApp:** +1 772 297 1783
**Your handset** `+1 669 216 7706` = **p001 Principal Johnson** — Incident Commander and floor
warden for the people who go silent, so the escalations land back on your phone.

Delivery is **on**. Every message in this script really sends.

| Setting | Value |
|---|---|
| Tick interval | 45s |
| Re-ping cap | 2, then escalate |
| Reach | 4 verified of 34 — the honest number, see step 6 |

---

## Before you start

Deploys clear the WhatsApp 24-hour session window and the in-memory board.
**Send `SAFE` to +1 772 297 1783 first** to open the window, then begin.

---

### 1 — Declare from the phone, not the console

**WhatsApp → +1 772 297 1783:**
```
/incident active shooter reported in the east wing, gunshots heard
```

The `/incident` prefix is stripped — WhatsApp has no slash commands, and the person
means the words after it.

**Watch two things happen at once:**

* **WhatsApp** replies with the incident ID and check-in instructions.
* **`#fr-live-demo` announces it** — this is the new half of the loop:

```
🚨 INCIDENT DECLARED — THREAT-2026-xxx
Type: ACTIVE THREAT  ·  Severity: critical
Location: East Wing Floor 2
Reported via: WhatsApp by Principal Johnson
> active shooter reported in the east wing, gunshots heard
```

> **The point:** nobody opens a laptop during a lockdown. The report comes from the
> phone in someone's hand; the room where the response is run has to hear it anyway.
> Until this week that direction was silent.

Note what the alert does **not** say: no assembly point, no "evacuate". Movement
policy strips rally points for lockdown incidents — sending 34 people into a corridor
where the shooter is would be the system's own doing.

---

### 2 — The autonomous loop starts itself

No one asks it to. Every 45s a tick runs. Leave it alone and watch WhatsApp.

**Tick 1** pings everyone unaccounted.
**Tick 2** re-pings the silent ones — *"you are not yet accounted for (request 2)"*.
**Tick 3** hits the cap and escalates.

---

### 3 — Check in from three directions

**WhatsApp:** `SAFE`
**Slack:** `/checkin safe`
**WhatsApp (room report):** `room 104: 23 students are safe, 1 unaccounted`

All three land on the same board. The room report is one message that accounts for
23 students — the reason a teacher under a desk can use this at all.

Now run **`/incident status`** and check the reporter is no longer listed as missing.

> The loop used to mark the reporter accounted while the status card still listed her
> as missing and reported zero check-ins — two ledgers counting the same people
> differently. Both are written through one funnel now.

Note what the room report does **not** do: "23 of 25 safe" never says *which* 23, so
only the reporter is accounted for. A falsely accounted person is one nobody goes
looking for.

---

### 4 — Escalation names a human

After the cap, the loop stops pinging and hands the person to their floor warden **by
name**, on the warden's own channel:

```
CrisisMesh: VP Martinez has not answered 2 accountability requests.
You are the floor warden. Please locate them.
```

> Two bugs lived here. The first sent *"X has not answered, please locate them"* **to X**.
> The second left the escalated person marked as needing action, so on a timer the same
> warden was paged about the same person every 45 seconds, forever.

If a warden is unreachable, the loop does not silently drop the person — it flags it to
the IC. Unreachable is a fact to report, not a reason to stop.

---

### 5 — Ask it anything, from either channel

Same answers whether you type in Slack or WhatsApp:

| Ask | Gets |
|---|---|
| `who is still unaccounted` | **every name**, not "and 25 more" |
| `show the classroom board` | per-room counts |
| `who is on call right now` | on-call staff |
| `what's the fastest route out of east wing` | *(during a lockdown: refused, with the reason)* |
| `/incident status` | live count, **every missing name**, and who declared it |

**Slack:** `@CrisisMesh who hasn't answered`  ·  **WhatsApp:** just type it.

`/incident status` names all 34 rather than ten and an "and 24 more". Those are the
people someone has to go and find; a count is the problem restated as a number. It
also names the declarer — `Principal Johnson (via WhatsApp)` — because an incident
declared from a handset has no Slack account to @-mention, and the card used to just
print a dash.

Ask for a **route** during the lockdown and it refuses — a corridor direction is a
movement instruction, and the threat is a person, not a hazard.

---

### 6 — The number it refuses to inflate

`/incident status` reports **4 reachable of 34**. That is real: 30 roster entries have
no verified channel. Slack IDs are checked against the workspace before being counted,
so a plausible-looking ID that resolves to nobody counts as unreachable.

> Showing 34/34 would demo better and be a lie. The system reports the reach it has.

---

### 7 — Law-enforcement handoff

**WhatsApp:** `send the arrival brief` → **refused.** A tactical brief is not something
to read off a phone screen.

**Slack:** `@CrisisMesh arrival brief`

```
Last known location: east wing — reported 4 min ago (UNCONFIRMED)
```

Sourced from the witness trail, timestamped, and marked unverified. With no witness
report yet it falls back to the opening message and times it to the declaration —
never "unknown".

**Read the two headcounts.** They are labelled, and they have to be:

```
Headcount (tracked staff roster): 34 total | 1 accounted | 33 unaccounted
  Totals (room-reported occupants): 48 safe · 1 missing
```

> Both numbers are true — 34 is the staff roster, 48 is students counted by their
> teachers. The brief printed them one above the other with no labels, so a responder
> reading it fast saw the system contradict itself.

Silent rooms are broken out separately, and the ones **inside the threat zone** are
listed first — those are the doors to open first.

The brief carries no medical notes. Mobility limitations appear as a flag with a
location, because a responder needs to know someone cannot use the stairs — not why.

---

### 8 — Stand down

**Slack:** `/incident resolve`  ·  or the **Resolve** button in the console.

The all-clear fans out to every phone **and** posts back into `#fr-live-demo`. Ticks
stop before the all-clear sends, so no one gets chased about an incident that is over.

Only `AUTHORIZED_IC_IDS` may resolve. Unconfigured, that gate refuses everyone rather
than allowing everyone.

---

## If you have 90 seconds

1. WhatsApp: `/incident active shooter in the east wing` → **Slack announces it**
2. Wait 90s → watch the re-pings, then the escalation naming VP Martinez
3. Slack: `@CrisisMesh who hasn't answered` → every name
4. `/incident resolve`

## What to say while it runs

> "Nobody declares an incident from a laptop during a lockdown. It came from a phone,
> and the room heard it. The loop is chasing people nobody asked it to chase, and when
> it runs out of ways to reach someone it hands that person to a named human instead of
> quietly giving up. It says four of thirty-four are reachable, because four is true."
