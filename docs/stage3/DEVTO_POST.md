# dev.to post — paste-ready

Editor: https://dev.to/new

---

## Title

```
Four bugs in one paragraph: what I learned building an autonomous loop that chases people who don't answer
```

## Tags (max 4)

```
googlecloud
ai
python
hackathon
```

## Cover image

Optional. The strongest one is the Slack DM showing two escalations arriving
unprompted — it is the only frame where the loop is visibly acting on its own.

---

## Body — paste everything below this line

*I built CrisisMesh for the [All Things Agentic Hackathon](https://allthingsagentic.devpost.com/), and I created this post for the purposes of entering that hackathon. #AllThingsAgenticHackathon*

CrisisMesh is a seven-agent fleet on Google ADK and Gemini 3.5 Flash that coordinates a school's response after a human reports an emergency. It doesn't detect anything. Somebody sends the message they'd already have sent — a Slack command, a WhatsApp text — and the fleet takes it from there.

The interesting part wasn't getting agents to talk to each other. It was everything that happened once the system started making claims.

## The loop is the product

The feature I'd defend is the one nobody watches. A declared incident starts a scheduler. Every tick it looks at whoever hasn't checked in, pings them, re-pings them, and at a configured cap **stops pinging and hands that person to their floor warden by name**, on the warden's own channel.

Four bugs lived in that one paragraph. Every one of them passed its tests.

**It sent the escalation to the person it couldn't find.** *"Mrs. Rodriguez has not answered — please locate her"* went to Mrs. Rodriguez. The message was correct. The recipient was the one person it couldn't help.

**It never stopped.** Once escalated, a person stayed actionable, so on a timer the same warden was paged about the same person every 25 seconds, forever. I fixed it with a terminal state — and then the same bug came back, because the guard went into the function the tests called and the running loop called a different one. Green the entire time.

```python
# tests called this one
def should_act(incident_id, person_id, tick):
    if get_state(...).status in TERMINAL_FOR_THE_LOOP:   # the guard
        return False
    ...

# the loop called this one
def safe_should_act(incident_id, person_id, tick):
    if state.status == ACCOUNTED:      # ACCOUNTED only. not ESCALATED.
        return False
```

**It counted people it couldn't reach as reachable.** Any non-empty Slack id counted, including the roster's placeholders like `U_PRINCIPAL`, so the loop reported 34 of 34 reachable and chased thirty people down channels that addressed nobody. It now verifies ids against the workspace and reports **4 of 34**.

That number is worse and it's true. The difference matters: unreachable is a fact a commander acts on, not a gap to paper over.

**The tick guard was process-local.** Harmless at one instance. At four, every container runs its own tick N and one silent teacher gets pinged four times. Ticks are now claimed with a create-if-absent lease in Firestore.

## Every real bug was in a seam

Not one of these was a bad function. They were all connections: a guard in the wrong function, a check-in written to one ledger and not the other, a critic that ran on one path and not another.

The sharpest example: a teacher asked *"what's the fastest route out of east wing"* during an active shooter, and the system answered with corridor directions. The movement policy that exists to prevent exactly that ran inside the fan-out. A query answer is a transport reply, and it never passed through.

Unit tests for the policy: green. Unit tests for the query desk: green.

So the tests changed shape. They stopped asserting that functions return values and started asserting things about the world:

```python
def test_the_escalation_does_not_go_to_the_person_being_looked_for(self):
def test_no_route_through_a_reported_sighting_is_offered(self):
def test_a_checkin_recorded_by_one_instance_is_counted_by_another(self):
def test_an_unreadable_ledger_says_so_and_counts_everyone_missing(self):
```

Those fail for real reasons.

## Managed services find bugs that mocks cannot

Moving the Memory Bank onto Vertex AI Agent Engine took an afternoon of writing and ten minutes of reality demolishing it.

Vertex persists `fact` and `scope` and **silently drops `display_name` and `description`** — which is where I'd put the structured record, so retrieval returned memories it couldn't read. And scope matching is exact on the whole map, not a subset, so putting metadata in scope makes a lesson findable only by someone who already knows its id.

Neither is in the docs. Both took one live call. I made the test double lossy in the same way afterwards, so the double can no longer pass while the service fails.

Model Armor was worse, and the failure was mine. The pillar was marked *managed* while the deployed service ran the regex fallback, so none of it had ever executed in production. Three defects were sitting there:

1. The client was built against the global endpoint. Templates are regional, so every scan returned "template not found" — which fell into the error path below.
2. Both error paths returned `blocked: False`, under a comment claiming they failed closed for ambiguous cases.
3. The block signal was read as `"MATCH_FOUND" in str(state)`. `str()` of that enum is `"2"`. **Model Armor had never blocked anything.**

Then turning it on correctly broke the product. The RAI *dangerous* classifier refused *"Smoke near the science lab, floor 2"* — while letting *"active shooter reported in the east wing"* through. A system that receives reports of danger can't treat danger as grounds for refusal. That filter is off now, in the template and in code, so a template edit can't quietly stop it accepting emergencies.

## What it refuses to say

The parts I'm most confident in are the refusals.

It won't name a route clear when it couldn't read the sighting log — the brief says `EGRESS ASSESSMENT WITHHELD`. It won't report zero unaccounted because the ledger was unreadable; the roster is the denominator, so a lost record counts as missing. It won't publish an assembly point during a lockdown. It won't send a tactical brief over WhatsApp, because that document names where people with mobility limitations are.

And the numbers say which scale they're on. Managed semantic recall returns a vector distance; the local store returns Jaccard tag overlap. A correct top hit reads **0.166** on one and **0.75** on the other, so every result carries the basis that produced it. Presenting one as the other is the same class of claim as 34 of 34.

## Where it's weak

Slack reaction check-ins are still process-local — they under-report, never over-report. The audit bundle reflects the instance serving the request. And SMS isn't a live channel at all: the route, signature verification and keyword mapping are written and tested, but the A2P 10DLC campaign is unapproved, so zero SMS have been sent or received. It's in the README as upcoming rather than counted as a transport.

All three are in the README under Known Limits with their failure mode named. A README that claims more than the runtime does is the same failure the runtime spends its effort avoiding.

## Stack

Google ADK · Gemini 3.5 Flash and 2.5 Flash Lite on Vertex AI · Cloud Run · Firestore · Pub/Sub · Model Armor · Vertex AI Agent Engine Memory Bank · Slack Events API · Twilio WhatsApp. 1,299 tests, none of which need GCP credentials.

**Repo:** https://github.com/4KInc/CrisisMesh
**Live:** https://crisismesh-1031148889398.us-central1.run.app

---

## Notes before publishing

* dev.to has an **AI Disclosure** button in the editor — this post describes an
  AI-assisted build, so tick whatever is accurate for you.
* The post must be **public, not unlisted**, for the Devpost bonus to count.
* Paste the published URL into the Devpost field *"OPTIONAL for Bonus Points
  Link to a piece of content"*.
* The attribution line at the top is required wording — it says both that the
  project was built for the hackathon and that the post was created for the
  purpose of entering it. Don't trim it.
