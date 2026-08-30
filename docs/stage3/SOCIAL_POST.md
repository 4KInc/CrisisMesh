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

Counted against the 280-character limit, with URLs counted as 23 characters as
X does. Replace `YOUR_POST` with the published dev.to URL.

### Option 1: leads with the loop (280/280, uses the repo link)

```
Created for the #AllThingsAgenticHackathon #AllThingsAgentic.

CrisisMesh: an autonomous loop that chases whoever hasn't checked in, then hands them to their floor warden by name.

It reports 4 of 34 people reachable. 34/34 would demo better and be a lie.

https://github.com/4KInc/CrisisMesh
```

### Option 2: leads with the write-up (263/280, recommended once dev.to is live)

```
Created for the #AllThingsAgenticHackathon #AllThingsAgentic.

CrisisMesh chases whoever hasn't answered during a school emergency, then hands them to a named human.

Four bugs lived in that sentence. Every one passed its tests. Write-up:

https://dev.to/YOUR_POST
```

### Option 3: shortest (244/280, leaves room for an image or a quote-tweet)

```
Created for the #AllThingsAgenticHackathon #AllThingsAgentic.

CrisisMesh: a loop that chases whoever hasn't checked in, then hands them to their warden by name.

4 of 34 reachable. 34/34 would demo better and be a lie.

https://dev.to/YOUR_POST
```

Option 2 is the one to post if the dev.to article is already up: it sends people
somewhere that explains the claim rather than asking them to take it on trust.

---

## LinkedIn

No practical length limit, so this one can carry the reasoning. Same required
lines at the top.

```
Created for the #AllThingsAgenticHackathon #AllThingsAgentic.

I built CrisisMesh for the All Things Agentic Hackathon: a seven-agent Google ADK fleet on Gemini 3.5 Flash that coordinates a school's emergency response after a human reports one. It does not detect anything and does not replace 911.

The feature I would defend is the one nobody watches. A declared incident starts a scheduler. Every tick it pings whoever has not checked in, re-pings them, and at a cap it stops pinging and hands that person to their floor warden by name, on the warden's channel rather than theirs.

Four bugs lived in that paragraph and every one of them passed its tests:

- it sent "X hasn't answered, please find X" to X
- once escalated, it paged the same warden about the same person every 25 seconds, forever
- it counted any non-empty Slack id as reachable, so it reported 34 of 34 when the true number was 4
- the tick guard was process-local, so at four instances one silent teacher was pinged four times

None of them were bad functions. They were seams: a guard in the function the tests called instead of the one the loop called; a movement policy that ran on the fan-out path and not the query path, so it answered "fastest route out?" during an active shooter with corridor directions.

It now reports 4 of 34 reachable. That is worse than 34 and it is true, and the difference matters: unreachable is a fact a commander acts on, not a gap to paper over.

1,309 tests, none of which need cloud credentials. Cloud Run, Firestore, Pub/Sub, Model Armor and Vertex AI Agent Engine Memory Bank.

Write-up: https://dev.to/YOUR_POST
Code: https://github.com/4KInc/CrisisMesh
```

---

## Before you post

* Replace `YOUR_POST` with the real dev.to URL, or switch to Option 1, which
  links the repo instead. A post with a placeholder in it is worse than no post.
* The account must be public, or Devpost cannot see the link.
* Best image: the Slack DM showing two escalations arriving unprompted. It is
  the only frame where the loop is visibly acting on its own.
* Do not claim 34 of 34, that the system detects incidents, or that it prevents
  anything. It coordinates a response after a human reports one, alongside 911.
