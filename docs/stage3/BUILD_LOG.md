# Building CrisisMesh: what an autonomous loop owes the people it cannot reach

*Created for the [All Things Agentic Hackathon](https://allthingsagentic.devpost.com/).
#AllThingsAgenticHackathon*

CrisisMesh is a seven-agent fleet on Google ADK and Gemini 3.5 Flash that
coordinates a school's response after a human reports an emergency. It does not
detect anything. Somebody sends the message they would already have sent — a
Slack command, a WhatsApp text — and the fleet takes it from there.

The interesting part was not getting the agents to talk to each other. It was
everything that happened once the system started making claims.

## The loop is the product

The feature I would defend is the one nobody watches. A declared incident starts
a scheduler. Every tick it looks at whoever has not checked in, pings them,
re-pings them, and at a configured cap **stops pinging and hands that person to
their floor warden by name**, on the warden's own channel.

Four bugs lived in that one paragraph, and each of them looked fine in tests.

**It sent the escalation to the person it could not find.** *"Mrs. Rodriguez has
not answered — please locate her"* went to Mrs. Rodriguez. The message was
correct; the recipient was the one person it could not help.

**It never stopped.** Once escalated, a person stayed actionable, so on a timer
the same warden was paged about the same person every 25 seconds, forever. The
fix was a terminal state — and then the same bug came back, because the guard
went into the function the tests called and the running loop called a different
one. The tests were green the entire time.

**It counted people it could not reach as reachable.** Any non-empty Slack id
counted, including the roster's placeholders, so the loop reported 34 of 34
reachable and chased thirty people down channels that addressed nobody. It now
verifies ids against the workspace and reports **4 of 34**. That number is worse
and it is true, and the difference matters: unreachable is a fact a commander
acts on, not a gap to paper over.

**The tick guard was process-local.** Harmless at one instance. At four, every
container runs its own tick N and one silent teacher is pinged four times. Ticks
are now claimed with a create-if-absent lease in Firestore.

## Every real bug was in a seam

Not one of these was a bad function. They were all connections: a guard in the
wrong function, a check-in written to one ledger and not the other, a critic that
ran on the fan-out path and not the query path.

The sharpest example: a teacher asked *"what's the fastest route out of east
wing"* during an active shooter, and the system answered with corridor
directions. The movement policy that exists to prevent exactly that ran inside
the fan-out. A query answer is a transport reply, and it never passed through.
Unit tests for the policy: green. Unit tests for the query desk: green.

So the tests changed shape. They stopped asserting that functions return values
and started asserting things about the world — *the loop hands this person to
somebody who is not them*, *no route through a reported sighting is offered*,
*a check-in recorded on one instance is counted on another*. Those tests fail for
real reasons.

## Managed services find bugs that mocks cannot

Moving the Memory Bank onto Vertex AI Agent Engine took an afternoon of writing
and ten minutes of reality demolishing it. Vertex persists `fact` and `scope` and
silently drops `display_name` and `description` — where I had put the structured
record, so retrieval returned memories it could not read. Scope matching is exact
on the whole map, not a subset, so metadata in scope makes a lesson findable only
by someone who already knows its id.

Neither is in the docs. Both took one live call. I made the test double lossy in
the same way afterwards, so the double can no longer pass while the service
fails.

Model Armor was worse, and the failure was mine. The pillar was marked *managed*
while the deployed service ran the regex fallback, so none of it had ever
executed in production. Three defects were sitting there: the client was built
against the global endpoint when templates are regional; both error paths
returned `blocked: False` under a comment claiming they failed closed; and the
block signal was read as `"MATCH_FOUND" in str(state)` where `str()` of that enum
is `"2"`, so it never matched and **Model Armor had never blocked anything**.

Then turning it on correctly broke the product: the RAI *dangerous* classifier
refused *"Smoke near the science lab, floor 2"*. A system that receives reports
of danger cannot treat danger as grounds for refusal. That filter is off now, in
the template and in code, so a template edit cannot quietly stop it accepting
emergencies.

## What it refuses to say

The parts I am most confident in are the refusals.

It will not name a route clear when it could not read the sighting log — the
brief says `EGRESS ASSESSMENT WITHHELD` instead. It will not report zero
unaccounted because the ledger was unreadable; the roster is the denominator, so
a lost record counts as missing. It will not publish an assembly point during a
lockdown. It will not give a tactical brief over WhatsApp, because that document
names where people with mobility limitations are.

And the numbers say which scale they are on. Managed semantic recall returns a
vector distance; the local store returns Jaccard tag overlap. A correct top hit
reads 0.166 on one and 0.75 on the other, so every result carries the basis that
produced it. Presenting one as the other is the same class of claim as 34 of 34.

## Where it is weak

Slack reaction check-ins are still process-local — they under-report, never
over-report. The audit bundle reflects the instance serving the request. `/sms`
still runs its pipeline inside the webhook, so a slow pipeline can lose a message
to Twilio's 15-second budget the way WhatsApp used to. All three are in the
README under Known Limits with their failure mode named, because a README that
claims more than the runtime does is the same failure the runtime spends its
effort avoiding.

**Repo:** https://github.com/4KInc/CrisisMesh
**Live:** https://crisismesh-1031148889398.us-central1.run.app
