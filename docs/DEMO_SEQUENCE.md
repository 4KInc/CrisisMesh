# CrisisMesh — Demo Sequence (Active Shooter)

**Live:** https://crisismesh-1031148889398.us-central1.run.app
**Slack room:** `#fr-live-demo` · **WhatsApp:** +1 772 297 1783
**Your handset** `+1 669 216 7706` = **p001 Principal Johnson** — Incident Commander and floor
warden for the people who go silent, so the escalations land back on your phone.

Delivery is **on**. Every message in this script really sends.

| Setting | Value |
|---|---|
| Tick interval | **25s** (demo). Production would use minutes — nobody re-pings a
teacher every 25 seconds during a real lockdown. |
| Re-ping cap | 2, then escalate |
| Reach | 4 verified of 34 — the honest number, see step 6 |

---

## Before you start — two minutes, once

1. **Send `SAFE` to +1 772 297 1783.** Deploys clear the WhatsApp 24-hour session
   window and the in-memory board; without this, nothing reaches your handset.
2. **Prime the resolve token.** Open the console, declare a throwaway incident,
   click **Resolve incident**, and enter the token once. The browser keeps it, so
   during the real run the dialog is just your name and a click. Doing this cold in
   front of an audience is a password prompt on your closing beat.
3. Have `#fr-live-demo` and the console open side by side, WhatsApp on the phone.

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

### 2 — The autonomous loop starts itself — do not wait for it

No one asks it to. A tick runs every 25s from the moment of declaration:

| | |
|---|---|
| **0:00** Tick 1 | pings everyone unaccounted |
| **0:25** Tick 2 | re-pings — *"you are not yet accounted for (request 2)"* |
| **0:50** Tick 3 | cap reached → **escalates to the floor warden by name** |

**Keep talking and keep typing through all of it.** The loop is autonomous; standing
still watching a phone is the one thing that makes it look like it is not. Go straight
into step 3 — the escalation will arrive on your handset partway through, and you
interrupt yourself to point at it.

Once everyone is either accounted for or escalated, the loop goes **quiet** — both are
terminal. It does not keep paging a warden about someone it has already handed over.

> Until this week it did exactly that: the same three names on the warden's phone every
> 25 seconds. The terminal guard was in the function the tests call, not the one the
> loop calls.

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
| `where is the shooter now` | the reported trail, marked **UNCONFIRMED** |
| `what's the fastest route out of east wing` | *(during a lockdown: refused, with the reason)* |
| `/incident status` | live count, **every missing name**, and who declared it |

**Slack:** `@CrisisMesh who hasn't answered`  ·  **WhatsApp:** just type it.

`/incident status` names all 34 rather than ten and an "and 24 more". Those are the
people someone has to go and find; a count is the problem restated as a number. It
also names the declarer — `Principal Johnson (via WhatsApp)` — because an incident
declared from a handset has no Slack account to @-mention, and the card used to just
print a dash.

Ask for a **route** during the lockdown and it refuses — a corridor direction is a
movement instruction, and the threat is a person, not a hazard. A fire still gets its
route: the rule is the incident type, not the word.

> It did *not* refuse until this week. The movement critic ran inside the fan-out, and
> a query answer is a transport reply that never passed through it — so the query desk
> published the one output the whole policy exists to prevent.

**The sighting trail.** Report movement and then ask:

```
you   ▸ shooter last seen heading toward the gym
      ◂ Noted and added to the incident log.
you   ▸ where is the shooter now
      ◂ Last reported location: gym. Reported trail: east wing -> gym.
        Reported by Principal Johnson. UNCONFIRMED — this is a reported
        sighting, not a confirmed position, and it may have moved.
```

Two positions say which way it is moving, which is the difference between arriving
behind it and arriving in front of it. Never without UNCONFIRMED attached, and never
attributed to a raw phone number. With no sighting at all it says nobody has reported
one rather than reaching for the declaration's wording as if it were a position.

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

The header reads **34 staff tracked**, not "34 staff/students": the roster is staff,
and the ~525 students are counted by their teachers, room by room.

One thing that looks wrong and is not: *Room 104 — Mrs. Davis: 23 safe* while
**Mrs. Davis is still listed under Missing**. You filed that room report from
Principal Johnson's handset, so Johnson is accounted for and Davis is not.

The brief carries no medical notes. Mobility limitations appear as a flag with a
location, because a responder needs to know someone cannot use the stairs — not why.

---

### 8 — Stand down

**Slack:** `/incident resolve` → the RESOLVED card, and the all-clear on every phone.
`#fr-live-demo` gets **no second message** — the card is already there.

**Console Resolve incident** → the same all-clear on every phone, **plus** a post into
`#fr-live-demo` reading *"Resolved via the web console"*, because nobody in that room
has seen anything.

The dialog asks for a name and shows what resolving reaches before it happens:

```
Resolve incident
  This ends ACTIVE_THREAT-2026-… for Slack, SMS and WhatsApp too, stops
  reconciliation, and sends an all-clear to everyone reachable.

  Resolving as — name or role   [ Principal Johnson ]
  A resolution has to be attributable.
```

> This used to be four native browser dialogs in a row — prompt, confirm, prompt,
> alert — each one painting the browser's own chrome over the board the operator is
> trying to read, at the moment they can least spare the attention. The token prompt
> also named an environment variable on screen. One dialog now, Enter to confirm,
> and the outcome as a banner rather than something to dismiss before you can see
> the board it describes.

The token is never in the page — the console is public, so an embedded token would be
no gate at all. It lives in your browser, and a rejected one is discarded rather than
replayed.

> Which of those two happens is decided by where the stand-down was typed, not where
> the incident was declared. Getting that backwards announced *"Resolved via WhatsApp"*
> for an incident stood down in Slack, and posted an ALL CLEAR directly beneath the
> RESOLVED card that the command had just produced.

Ticks stop **before** the all-clear sends, so nobody is chased about an incident that
is already over.

Only `AUTHORIZED_IC_IDS` may resolve. Unconfigured, that gate refuses everyone rather
than allowing everyone.

---

## The 4-minute run

Nothing in this waits on the loop. The escalation lands during step 3 and you cut to it.

| Clock | Do | Say |
|---|---|---|
| **0:00** | WhatsApp: `/incident active shooter reported in the east wing, gunshots heard` | "Nobody opens a laptop during a lockdown." |
| **0:10** | Point at `#fr-live-demo` announcing it | "It came from a phone. The room heard it anyway." |
| **0:35** | WhatsApp: `room 104: 23 students are safe, 1 unaccounted` | "One message, 23 students." |
| **0:50** | *Escalation arrives — stop and read it out* | "Nobody asked it to do that." |
| **1:10** | Slack: `/incident status` | "Every name. Not 'and 24 more'." |
| **1:40** | WhatsApp: `shooter last seen heading toward the gym` | "A witness reports. That is a sighting, not a question." |
| **1:55** | WhatsApp: `where is the shooter now` | "east wing → gym. Two positions say which way it is moving." |
| **2:10** | Slack: `@CrisisMesh arrival brief` | "Two headcounts, both labelled." |
| **3:00** | Console **Resolve incident** → type your name → **Resolve** | "All-clear to every phone, and back to Slack." |

Leaves roughly a minute of slack in a 4–5 minute slot.

**If you are running long**, cut the sighting pair (1:40–1:55) — the brief is the
stronger moment. **If you are running short**, add the route refusal:
`what's the fastest route out of east wing` → it will not give one during a lockdown.

## What to say while it runs

> "Nobody declares an incident from a laptop during a lockdown. It came from a phone,
> and the room heard it. The loop is chasing people nobody asked it to chase, and when
> it runs out of ways to reach someone it hands that person to a named human instead of
> quietly giving up. It says four of thirty-four are reachable, because four is true."
