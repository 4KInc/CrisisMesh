# Devpost submission: answers

Copy-paste answers for
`devpost.com/submit-to/30845-all-things-agentic-hackathon`, submission
`1149244-crisismesh`.

Everything factual here was checked against the code or the deployed service.
Fields marked **DECIDE** need something only you can supply.

---

## Project overview

**Project name** (50 char limit)

```
CrisisMesh
```

**Elevator pitch** (200 char limit; this is 189)

```
Autonomous coordination on your org's own data: your rooms, staff, routes. Runs fire, active-threat, cyber and medical response, chases whoever hasn't answered, hands them to a named human.
```

> Two other framings, if you want a different emphasis. Both fit the limit.
>
> **Leads with the autonomy** (193). The strongest single differentiator, but it
> does not say the data is yours or that it is free:
> `Autonomous accountability for school emergencies. Declare from any channel; it chases whoever hasn't answered, then hands them to a named human. It reports 4 of 34 reachable, because 4 is true.`
>
> **Leads with the upload** (200), closest to the original draft framing:
> `Upload your building, staff and routes. It runs the response to fire, active threat, cyber or medical, chasing whoever hasn't answered, then handing them to a named human. Free, and it never guesses.`
>
> One note on wording. "For Slack" was in the draft and is no longer accurate:
> WhatsApp is the channel the demo declares from, and Slack is where the room
> coordinates, so naming one undersells the thing that makes it work in a
> lockdown.

---

## Project story: "About the project"

> Paste the whole block below into the *About the project* box. It uses the
> headings Devpost pre-fills.

```markdown
## Inspiration

During a fire, an active-threat event or severe weather, a school district coordinates across buildings on frantic group chats, failing phone trees and paper rosters. The incident commander cannot answer the questions that matter: who is safe, who is unaccounted for, which route is blocked, who cannot use the stairs. Enterprise incident platforms solve this at $21+/user/month, which is not a number a public elementary school reaches for.

The specific thing that shaped the build: **nobody opens a laptop during a lockdown.** Whatever this became, the first message had to be able to arrive from a phone, in a corridor, from somebody who is frightened.

## What it does

A human sends the message they would already have sent: a Slack command, a WhatsApp text. CrisisMesh does not detect anything and does not replace 911. It coordinates the organisational response after a person reports one, using the organisation's own building data: rooms, staff, routes, resources, assembly points, all loaded from CSVs the school controls.

The feature I would defend is the one nobody watches. A declared incident starts a scheduler. Every tick it looks at whoever has not checked in, pings them, re-pings them, and at a configured cap it **stops pinging and hands that person to their floor warden by name**, on the warden's channel rather than theirs. Once each. Then it goes quiet, because an escalated person is finished as far as the loop is concerned.

Around that:

- **Cross-channel sync.** Declare on WhatsApp and the Slack room hears it. Declare in Slack and every reachable phone does.
- **Room-level check-in.** `room 104: 23 students are safe, 1 unaccounted`. One message from a teacher under a desk accounts for 23 people.
- **A threat trail, not a point.** Reported sightings accumulate (`east wing -> gym`), timestamped and attributed, every answer marked UNCONFIRMED.
- **An egress assessment that does the join.** Thirteen routes and every reported position, cross-checked: which doors carry no reported sighting, which do, step-free options marked. Not a floor plan handed to a responder in a corridor.
- **A law-enforcement arrival brief** with two separately labelled headcounts, silent rooms prioritised by threat-zone proximity, and no medical detail beyond a mobility flag.

Ten incident types are covered by playbooks: fire, active threat, severe weather, medical, flood, cyber ransomware, data breach, utility outage, hazmat and bomb threat.

## How we built it

Seven agents on **Google ADK**, a coordinator delegating to intake, accountability, safety intel, SITREP, learning and compliance, running **Gemini 3.5 Flash** on Vertex AI. **Gemini 2.5 Flash Lite** handles follow-up questions through the Google GenAI SDK.

Five Google-managed services do real work, and `/health` names each one so a silent fallback cannot masquerade as success:

- **Cloud Run** for the service, at `--max-instances=4`
- **Firestore** for incident state, the reconciliation state machine (compare-and-set), the witness log, the room board and the check-in ledger
- **Pub/Sub** for the event bus, 18 typed events
- **Model Armor** for prompt-injection and jailbreak filtering
- **Vertex AI Agent Engine Memory Bank** for cross-incident lessons, retrieved by semantic similarity

Transports: Slack Events API with Block Kit, Twilio WhatsApp, and a web console. An SMS transport is written and tested, and the Twilio number's webhook points at it, but it is not a live channel: the A2P 10DLC campaign is unapproved, so US carriers will not carry the traffic and zero SMS have been sent or received.

The discipline that mattered: tests assert claims about the world rather than return values. *The loop hands this person to somebody who is not them.* *No route through a reported sighting is offered.* *A check-in recorded on one instance is counted on another.* There are 1,309 of them, none needing GCP credentials.

## Challenges we ran into

**Every real bug was in a seam.** Not one was a bad function.

The escalation was sent to the person it could not find. *"Mrs. Rodriguez has not answered, please locate her"*, delivered to Mrs. Rodriguez. Then it never stopped: an escalated person stayed actionable, so the same warden was paged about the same person every 25 seconds, forever. Fixed with a terminal state, and then the same bug came back, because the guard went into the function the tests called while the running loop called a different one. Green the whole time.

A teacher asked *"what's the fastest route out of east wing"* during an active shooter and got corridor directions. The movement policy that exists to prevent exactly that ran inside the fan-out; a query answer is a transport reply and never passed through it.

The system reported **34 of 34 reachable** because any non-empty Slack id counted, including the roster's placeholders, so the loop chased thirty people down channels that addressed nobody.

And the one that only a live call could find: turning on Model Armor correctly broke the product. Its RAI *dangerous* classifier refused *"Smoke near the science lab, floor 2"*. A system that receives reports of danger cannot treat danger as grounds for refusal.

## Accomplishments that we're proud of

**It says 4 of 34 reachable.** Thirty roster entries have no verified channel, and Slack ids are checked against the workspace before they count. That number is worse than 34 and it is true. Unreachable is a fact a commander acts on, not a gap to paper over.

The refusals generally. It will not name a route clear when it could not read the sighting log; the brief says EGRESS ASSESSMENT WITHHELD. It will not report zero unaccounted because the ledger was unreadable: the roster is the denominator, so a lost record counts as missing. It will not publish an assembly point during a lockdown. It will not send a tactical brief over WhatsApp, because that document names where people with mobility limitations are.

And the numbers say which scale they are on. Managed semantic recall returns a vector distance, the local store returns Jaccard tag overlap, and a correct top hit reads 0.166 on one and 0.75 on the other, so every result carries the basis that produced it.

## What we learned

**Managed services break assumptions that mocks cannot.** Vertex Memory Bank persists `fact` and `scope` and silently drops `display_name` and `description`, which is where the structured record was, so retrieval returned memories it could not read. Scope matching is exact on the whole map rather than a subset. Neither is in the docs; both took one live call. The test doubles were made lossy in the same way afterwards, so a double can no longer pass while the service fails.

**A comment claiming a property is not the property.** Both Model Armor error paths returned `blocked: False` under a comment saying they failed closed for ambiguous cases. And the block signal was read as `"MATCH_FOUND" in str(state)`, where `str()` of that enum is `"2"`, so it never matched and Model Armor had never blocked anything.

**Prose is not executable.** The README claimed 495 tests when there were 1,215, described a "7-beat demo" above an eight-row table, and documented an authorisation gate as accepting anyone when it refuses everyone. The checkable claims are now pinned by a test that fails when a number drifts.

## What's next for CrisisMesh

Migrate the last process-local state, Slack reaction check-ins and the observability trace store, so the audit bundle is instance-independent. Give `/sms` the acknowledge-first treatment WhatsApp already has, keeping the carrier-mandated STOP/HELP paths synchronous. Finish A2P 10DLC so the SMS transport, written and tested but carrying no traffic today, becomes a channel rather than code. And multi-facility: the registry and Memory Bank are already shared across sites, so the per-facility knowledge bases are the remaining work.
```

---

## Built with

> Up to 25 tags. Paste one at a time.

```
google-adk
gemini
vertex-ai
google-genai
cloud-run
firestore
pub-sub
model-armor
agent-engine
python
slack-api
twilio
whatsapp
server-sent-events
docker
pytest
```

---

## "Try it out" links

```
https://crisismesh-1031148889398.us-central1.run.app
https://github.com/4KInc/CrisisMesh
```

---

## Project media

**Video demo link: DECIDE.** Record from `docs/DEMO_SCRIPT_4MIN.md`, which
carries the beat sheet, the Google Cloud proof to capture at each beat, and a
mid-take failure table. Must be public, not unlisted.

**Image gallery**, suggested in order:

1. The Demo User DM showing two handoffs arriving unprompted. The only frame
   where the loop is visibly acting on its own.
2. The arrival brief's egress assessment.
3. `/incident status` with every missing name and 4 of 34.
4. `#fr-live-demo` receiving a WhatsApp-declared incident.
5. The architecture diagram.

---

## Additional info

| Field | Answer |
|---|---|
| **Submitter Type** | **DECIDE.** *Individual* unless you are entering as Blockintel Inc |
| **Submitter country of residence** | United States |
| **Which Category are you submitting to?** | **Fortified Enterprise Fleet** |
| **Organization name** (required field) | **DECIDE.** `Blockintel Inc`, or `N/A` if submitting as an individual |
| **What date did you start this project?** | `08-19-26`, the first commit: *Scaffold CrisisMesh multi-agent crisis-coordination fleet* |
| **URL to your public or private code repo** | `https://github.com/4KInc/CrisisMesh` |
| **Did you add Reproducible Testing instructions to your README?** | **Yes** |
| **Hosted project URL** | `https://crisismesh-1031148889398.us-central1.run.app` |

**Which Google SDK did you use?** (select all that apply)

- Agent Development Kit (ADK)
- Google GenAI SDK (google-genai)

**Which Google Cloud Service(s) did you use?** (select all that apply)

- Cloud Run
- Firestore
- Pub/Sub
- Plus any of these the list also offers: Vertex AI, Model Armor, Cloud Build,
  Cloud Logging

> The dropdown showed five options before scrolling. Select Vertex AI and Model
> Armor if they appear. Both are genuinely used and both are load-bearing for
> the Architecture axis.

**Which Google AI Models did you use?**

```
Gemini 3.5 Flash (agent fleet, via ADK on Vertex AI); Gemini 2.5 Flash Lite (follow-up queries, via the Google GenAI SDK); text-embedding-005 (Vertex AI Agent Engine Memory Bank)
```

**Architecture diagram: ready.** Upload
`docs/diagram/CrisisMesh-Architecture.pdf`. Two pages, 186KB, well inside the
35MB limit. Page 1 is the full system, page 2 is the reconciliation loop's state
machine plus two tables: which pillars are managed and the command that verifies
each, and what the system refuses to do. Regenerate it from
`docs/diagram/architecture.html` through headless Chrome if anything changes.

**Startup Prize fields: DECIDE.** Only if you are entering as Blockintel Inc,
which requires the incorporated organisation name and a corporate email
(`heartlinmachado@blockintelai.com`). Leave both blank otherwise.

---

## Testing instructions (optional field, seen by judges, not public)

**255 characters maximum, on a single line.** Devpost rejects anything longer.
Paste exactly this (253 characters):

```
No GCP creds: pip install -e ".[dev]" && pytest tests/ -q = 1,309 offline tests. GET /health on the hosted URL shows the live managed backends (vertex/pubsub/model_armor). scripts/verify_memory_bank.py and verify_durable_stores.py hit the real services.
```

> Chosen for what a judge cannot get anywhere else on the form: that the suite
> needs no credentials, that the managed claims are checkable from outside the
> process, and that two scripts verify them against the real services rather
> than mocks. The hosted URL is left out because it has its own field, and the
> install line is included because it is the one step that has to be right.

**The long version lives in the README**, under `## Reproducible Testing`, which
is what the *Did you add Reproducible Testing instructions to your README?*
answer refers to. It covers the offline suite, the live checks against
`/health`, `/armor/scan` and the agentic stream, and both verification scripts
with the reason they are scripts rather than tests: they cost money, need
credentials, and would make the suite depend on a network.

> Worth knowing why that section exists. This field named
> `scripts/verify_memory_bank.py` and `verify_durable_stores.py`, and the form
> answered Yes to the README question, while the README documented neither. The
> answer was true about the file existing and false about what was in it. Four
> tests now hold it: the section must exist, must say no credentials are needed,
> must name both scripts, both scripts must exist, and the line above must fit
> 255 characters.

---

## Bonus-points fields

**Link to a piece of content: DECIDE.** The article is written and ready to
paste at `docs/stage3/DEVTO_POST.md`, with its title, four tags and body. It
carries the required "created for this hackathon" line. Publish it at
`dev.to/new`, public rather than unlisted, and paste the URL here.
`docs/stage3/BUILD_LOG.md` is the longer version it was adapted from; you do not
need both.

**Link to a social media post: DECIDE.** `docs/stage3/SOCIAL_POST.md` has two
drafts, both tagged `#AllThingsAgenticHackathon` with the attribution line. Post
one and paste the URL.

---

## Before you hit submit

- [ ] Architecture diagram uploaded from `docs/diagram/`
- [ ] Video recorded from `DEMO_SCRIPT_4MIN.md`, public rather than unlisted
- [ ] `/health` reports `vertex`, `pubsub`, `model_armor` at recording time
- [ ] Repo public, or shared with `testing@devpost.com` and
      `cloudhackathons@google.com`
- [ ] dev.to article and social post live, URLs pasted
- [ ] Category is **Fortified Enterprise Fleet**
