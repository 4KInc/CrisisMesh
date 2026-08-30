# CrisisMesh — Demo Sequence (Active Shooter)

**Live:** https://crisismesh-1031148889398.us-central1.run.app
**Slack room:** `#fr-live-demo` · **WhatsApp:** +1 772 297 1783
**Your handset** `+1 669 216 7706` = **p001 Principal Johnson** — Incident Commander and
floor-1 warden.

**The escalations do not come to you.** The two people who go silent are on floor 2, so
their warden is **Mrs. Nguyen (p018)** — mapped to the **Demo User** Slack account. Have
that DM on screen. It is the only frame where the loop is visibly acting on its own, and
it happens somewhere you are not looking unless you put it there.

> Earlier versions mapped everyone to floor 1, so every escalation resolved back to p001:
> the loop paging whoever was already running the demo. That demonstrates nothing.
> `scripts/seed_demo_identities.py` chooses the mapping so the handoff lands on somebody
> else.

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
2. Have `#fr-live-demo` and the console open side by side, WhatsApp on the phone.
3. **Optional opener — seed the school from CSVs.** Drop
   **`CrisisMesh/data/seed/*.csv`** into `#fr-live-demo` **before declaring**. That is
   the whole set CrisisMesh reads — eight files, no more and no fewer — so all eight
   load and nothing is refused:

   > :white_check_mark: `personnel.csv` loaded — 34 rows. Roster now 34 people, 22 rooms, 8 zones.

   Drop `personnel.csv` alone if you want the point in one message instead of eight.
   Dropping a CSV CrisisMesh does not read — a runbook, a network inventory — is
   ignored with a log line rather than a refusal in the channel.
   **Never mid-incident:** the reload is atomic, but the board and check-ins are keyed
   to a live incident and reloading under it muddies what the numbers mean.

The run closes in Slack, so **you never touch the console's resolve token**. It exists
because the console is deployed publicly and a stranger with the URL must not be able
to end a live incident — a real property, but not a beat. Keep it for the Q&A.

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
| **0:50** Tick 3 | cap reached → **hands Mr. Patel and Ms. Clark to Mrs. Nguyen by name** |

**Keep talking and keep typing through all of it.** The loop is autonomous; standing
still watching a phone is the one thing that makes it look like it is not. Go straight
into step 3 — the handoffs land in the Demo User DM partway through, and you interrupt
yourself to cut to them.

Once everyone is either accounted for or escalated, the loop goes **quiet** — both are
terminal. It does not keep paging a warden about someone it has already handed over.

> It did exactly that once: the same names on the warden's phone every 25 seconds. The
> terminal guard was in the function the tests call, not the one the loop calls.

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

Watch the **Demo User** DM, not yours:

```
CrisisMesh: Mr. Patel has not answered repeated check-in requests.
Mrs. Nguyen — please attempt to locate or contact them, without entering
an unsafe area. If this is life-threatening, call 911.
```

A second one arrives for **Ms. Clark**. Two handoffs, one warden, named — then silence,
because both are terminal for the loop.

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

**Point at the egress assessment first — it is the strongest thing on the page.**

```
🚪 EGRESS ASSESSMENT — cross-checked against reported sightings: east wing, gym
  ✅ No reported sighting on these paths:
    · Door 1 (West Exit) — from west-wing-f1, west-wing-f2  [step-free available]
    · Door 5 (Cafeteria Exit) — from west-wing-f1, cafeteria
    · Door 2 (Main Entrance) — from library
  ⚠️ Do not use — reported sighting or floor-plan block:
    · Door 3 (East Exit) — from east-wing-f1, east-wing-f2 — sighting: east wing
    · Door 7 (Gym Exit) — from east-wing-f1, gym — sighting: east wing, gym
    · Door 2 (Main Entrance) — from east-wing-f2 — sighting: east wing
    · Door 8 (Field Exit) — from gym — sighting: gym
```

Thirteen routes, two reported positions. The system holds both, so the system does
the join — not a responder reading a floor plan in a corridor.

Three things to point at, in this order:

* **It picked doors.** Door 1, Door 5, Door 2 — not "here is a list, good luck".
* **The step-free one is marked**, because the brief names two people who cannot
  use stairs, and a clear route they cannot physically take is not an answer for them.
* **Nothing says "safe".** *Clear* means one thing and never drifts: no reported
  sighting lies on this path. Not swept, not cleared, and blind to a threat nobody
  reported. That sentence is attached to every answer it gives.

If every route were compromised, the clear list would be **empty and say so** — the
least-bad route does not get promoted to a safe one.

**Door 2 appears in both lists, and that is the point.** Clear from the library, not
clear from east-wing-f2. The exit is not what is safe or unsafe — the path to it is.

**The brief arrives as two messages.** Part 1 ends on the caveat, immediately after
the assessment; part 2 is resources, command contact and responding services. The
whole answer is in part 1 — scroll there and stay.

> Two versions ago the brief printed `Last known location: gym` and then, four lines
> below, `✅ Safe Routes: … Door 7 (Gym Exit)` — leaving the reader to notice the
> collision. The version after that flagged the bad door and stopped, which answered
> a different question than the one being asked.

Route data is static building layout. It knows where the doors are and has no idea
where the threat is. Every route is checked against **every** reported sighting, not
just the latest: a threat seen in the east wing and then the gym has been in both, and
the east wing is not clear because it moved on. A fire still gets `Safe Routes` — a
hazard does not follow anyone down a corridor.

**Then the two headcounts.** They are labelled, and they have to be:

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

## If a message gets no reply

Don't retype it — check whether Twilio even delivered it:

```
curl -s -u "$TWILIO_API_KEY_SID:$TWILIO_API_KEY_SECRET" \
  "https://api.twilio.com/2010-04-01/Accounts/$TWILIO_ACCOUNT_SID/Messages.json?PageSize=10" \
| python3 -c "import json,sys;[print(m['direction'],m['status'],m.get('error_code'),m['body'][:40]) for m in json.load(sys.stdin)['messages']]"
```

`error_code 11200` means Twilio's POST to the webhook never completed — the message
reached Twilio and died there. That used to happen when the pipeline ran inside the
webhook and overran Twilio's 15-second budget; the route acknowledges first now, so it
should not recur. Anything else, the message is in the service and the fault is ours.

## The 4-minute run

Nothing in this waits on the loop. The escalation lands during step 3 and you cut to it.

| Clock | Do | Say |
|---|---|---|
| **0:00** | WhatsApp: `/incident active shooter reported in the east wing, gunshots heard` | "Nobody opens a laptop during a lockdown." |
| **0:10** | Point at `#fr-live-demo` announcing it | "It came from a phone. The room heard it anyway." |
| **0:35** | WhatsApp: `room 104: 23 students are safe, 1 unaccounted` | "One message, 23 students." |
| **0:50** | *Cut to the Demo User DM — two handoffs arrive* | "Nobody asked it to do that." |
| **1:10** | Slack: `/incident status` | "Every name. Not 'and 24 more'." |
| **1:40** | WhatsApp: `shooter last seen heading toward the gym` | "A witness reports. That is a sighting, not a question." |
| **1:55** | WhatsApp: `where is the shooter now` | "east wing → gym. Two positions say which way it is moving." |
| **2:05** | WhatsApp: `what's the fastest route out of east wing` | "Every way out of here is compromised — and it names the ones that aren't." |
| **2:20** | Slack: `@CrisisMesh arrival brief` | "It worked out which door is clear. Nobody did that by hand." |
| **3:00** | Slack: `/incident resolve` | "One command. All-clear to every phone." |

Leaves roughly a minute of slack in a 4–5 minute slot.

**If you are running long**, cut the sighting pair (1:40–1:55) — the brief is the
stronger moment. **If you are running short**, add the route refusal:
`what's the fastest route out of east wing` → it will not give one during a lockdown.

## What to say while it runs

> "Nobody declares an incident from a laptop during a lockdown. It came from a phone,
> and the room heard it. The loop is chasing people nobody asked it to chase, and when
> it runs out of ways to reach someone it hands that person to a named human instead of
> quietly giving up. It handed two people to Mrs. Nguyen by name, on her channel, not
> theirs. And it says four of thirty-four are reachable, because four is true."
