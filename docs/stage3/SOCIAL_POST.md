# Stage-3 social post

*Created for the All Things Agentic Hackathon. #AllThingsAgenticHackathon*

---

## Primary (X / LinkedIn)

> Built CrisisMesh for the #AllThingsAgenticHackathon — created for this
> hackathon: a 7-agent Google ADK fleet on Gemini 3.5 Flash that coordinates a
> school's emergency response after a human reports one.
>
> The feature I'd defend is the one nobody watches. Declare an incident and a
> loop starts pinging whoever hasn't checked in, re-pings them, and at a cap
> stops pinging and hands that person to their floor warden **by name**.
>
> Four bugs lived in that paragraph and every one passed its tests:
>
> • it sent "X hasn't answered, please find X" **to X**
> • once escalated, it paged the same warden about the same person every 25s
>   forever
> • it counted any non-empty Slack id as reachable — reported 34/34 when the
>   true number was 4
> • the tick guard was process-local, so at 4 instances one silent teacher got
>   pinged 4 times
>
> None were bad functions. They were seams — a guard in the function the tests
> called instead of the one the loop called; a movement policy that ran on the
> fan-out path and not the query path, so it answered "fastest route out?"
> during an active shooter with corridor directions.
>
> It now says **4 of 34 reachable**. That's worse and it's true. Unreachable is
> a fact a commander acts on, not a gap to paper over.
>
> 1,299 tests. Cloud Run + Firestore + Pub/Sub + Model Armor + Vertex AI Agent
> Engine Memory Bank.
>
> github.com/4KInc/CrisisMesh

---

## Short (X, single tweet)

> CrisisMesh — created for the #AllThingsAgenticHackathon.
>
> An autonomous accountability loop that chases whoever hasn't checked in, then
> hands them to a named human when it runs out of ways to reach them.
>
> It reports 4 of 34 people reachable. 34/34 would demo better and be a lie.
>
> github.com/4KInc/CrisisMesh

---

## Notes for posting

* Both carry `#AllThingsAgenticHackathon` and an explicit *created for this
  hackathon* line, as the Stage-3 rules require.
* Best still: the Demo User DM showing two handoffs arriving unprompted — it is
  the only frame where the loop is visibly acting on its own.
* Do not claim 34/34, "detects incidents", or "prevents" anything. The system
  coordinates a response after a human reports one, alongside 911.
