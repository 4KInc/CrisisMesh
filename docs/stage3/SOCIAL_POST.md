# Stage-3 social post: paste-ready

Devpost field: *OPTIONAL for Bonus Points: Link to a social media post*.
Post one of these, then paste the resulting URL into that field.

## About the hashtag

The Devpost field says *"Include the hashtag #AllThingsAgentic Hackathon"*, with
a space, which is ambiguous: it could be `#AllThingsAgenticHackathon` or
`#AllThingsAgentic` followed by the word Hackathon. Every draft below carries
**both tags**, which costs a few characters and removes the guess. Do not drop
either one to save room.

Each post also carries an explicit "created for this hackathon" line, which the
Stage-3 rules require and which is separate from the hashtag.

---

## X / Twitter

Counted against the 280-character limit, with URLs counted as 23 characters the
way X counts them.

### Option 1: the elevator pitch verbatim (277/280, recommended)

Says the same thing the Devpost pitch says, so a judge reading both hears one
claim rather than two descriptions of the same project.

```
Created for the #AllThingsAgenticHackathon #AllThingsAgentic.

Autonomous coordination on your org's own data: your rooms, staff, routes. Runs fire, active-threat, cyber and medical response, chases whoever hasn't answered, hands them to a named human.

https://dev.to/blockintel/four-bugs-in-one-paragraph-what-i-learned-building-an-autonomous-loop-that-chases-people-who-dont-4hdl
```

### Option 2: leads with the loop (263/280)

Narrower, and better if the audience is engineers rather than schools.

```
Created for the #AllThingsAgenticHackathon #AllThingsAgentic.

CrisisMesh chases whoever hasn't answered during a school emergency, then hands them to a named human.

Four bugs lived in that sentence. Every one passed its tests. Write-up:

https://dev.to/blockintel/four-bugs-in-one-paragraph-what-i-learned-building-an-autonomous-loop-that-chases-people-who-dont-4hdl
```

### Option 3: shortest (244/280, leaves room for an image)

```
Created for the #AllThingsAgenticHackathon #AllThingsAgentic.

CrisisMesh: a loop that chases whoever hasn't checked in, then hands them to their warden by name.

4 of 34 reachable. 34/34 would demo better and be a lie.

https://dev.to/blockintel/four-bugs-in-one-paragraph-what-i-learned-building-an-autonomous-loop-that-chases-people-who-dont-4hdl
```

Option 1 is the one to post. It matches the elevator pitch word for word, and
the link still sends anyone who wants the detail to the write-up.

---

## LinkedIn

No practical length limit, so this one can carry the reasoning. Same required
lines at the top.

> Written for a company page rather than a personal account, since that is where
> these are going. If you post from a personal profile instead, change "we" to
> "I" throughout.

```
Created for the #AllThingsAgenticHackathon #AllThingsAgentic.

We built CrisisMesh for the All Things Agentic Hackathon: a seven-agent Google ADK fleet on Gemini 3.5 Flash that coordinates a school's emergency response after a human reports one. It does not detect anything and does not replace 911.

The feature we would defend is the one nobody watches. A declared incident starts a scheduler. Every tick it pings whoever has not checked in, re-pings them, and at a cap it stops pinging and hands that person to their floor warden by name, on the warden's channel rather than theirs.

Four bugs lived in that paragraph and every one of them passed its tests:

- it sent "X hasn't answered, please find X" to X
- once escalated, it paged the same warden about the same person every 25 seconds, forever
- it counted any non-empty Slack id as reachable, so it reported 34 of 34 when the true number was 4
- the tick guard was process-local, so at four instances one silent teacher was pinged four times

None of them were bad functions. They were seams: a guard in the function the tests called instead of the one the loop called; a movement policy that ran on the fan-out path and not the query path, so it answered "fastest route out?" during an active shooter with corridor directions.

It now reports 4 of 34 reachable. That is worse than 34 and it is true, and the difference matters: unreachable is a fact a commander acts on, not a gap to paper over.

1,309 tests, none of which need cloud credentials. Cloud Run, Firestore, Pub/Sub, Model Armor and Vertex AI Agent Engine Memory Bank.

Write-up: https://dev.to/blockintel/four-bugs-in-one-paragraph-what-i-learned-building-an-autonomous-loop-that-chases-people-who-dont-4hdl
Code: https://github.com/4KInc/CrisisMesh
```

---

## Before you post

* The dev.to URL is already filled in below. Post from the account whose link
  you will paste into Devpost.
* The account must be public, or Devpost cannot see the link.
* Best image: the Slack DM showing two escalations arriving unprompted. It is
  the only frame where the loop is visibly acting on its own.
* Do not claim 34 of 34, that the system detects incidents, or that it prevents
  anything. It coordinates a response after a human reports one, alongside 911.
