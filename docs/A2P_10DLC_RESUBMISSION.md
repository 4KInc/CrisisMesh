# A2P 10DLC Campaign Rejection — Diagnosis & Resubmission

**Campaign `CMbfa34a08b273a6d57670d9f2b3670086` was rejected on 2026-08-21** with
errors **30908**, **30909**, and **30882**. This document is the complete record:
what Twilio objected to, what was actually broken in CrisisMesh, what changed in
the codebase, and the paste-ready field values for the resubmission.

| Field | Value |
|---|---|
| Account | My First Twilio Account |
| Account SID | *(set in env: TWILIO_ACCOUNT_SID)* |
| Campaign SID | *(see Twilio console)* |
| Brand Registration SID | *(see Twilio console)* |
| Messaging Service | *(see Twilio console)* |
| Use case | STARTER |
| Submitted | 2026-08-21T19:00:13.368Z |
| Status | Rejected — resubmission pending the checklist in §3 |

---

## 1. The rejection

### Error 30908 — Compliant privacy policy required

> The privacy policy in your registration could not be verified as compliant.
> This usually means the privacy policy was missing from the website or
> `message_flow`, contained conflicting information, or did not include the
> required statement that mobile information and messaging consent are not
> shared with third parties or affiliates for marketing or promotional purposes.

### Error 30909 — Message flow / call to action insufficient

> The Message Flow or Call to Action does not give reviewers enough information
> to verify how end users consent to receive messages. Include a complete
> description of every opt-in path you use and the required disclosures.

Related codes that can accompany it: 30896 (opt-in), 30907 (website URL
validation), 30917 (incomplete workflow descriptions), 30919 (site lacks
business/use-case info), 30921 (site requires authentication), 30924
(non-compliant consent language), 30925 (opt-in must be unchecked by default),
30933 (privacy policy URL required), 30934 (terms URL required).

### Error 30882 — Terms and conditions non-compliant

> The terms and conditions associated with the campaign do not meet A2P 10DLC
> review requirements. This can include campaigns whose use case conflicts with
> prohibited third-party marketing rules.

---

## 2. What was actually wrong in CrisisMesh

Three separate gaps, one per error code — plus a real bug that surfaced during
the audit.

| Code | What Twilio means | CrisisMesh's actual gap |
|---|---|---|
| 30908 | Privacy policy missing or non-compliant | No `/privacy` page existed. The Cloud Run service served only `static/index.html` (`src/core/server.py`, `_serve_static`). Nothing carried the required third-party-sharing statement. |
| 30909 | No verifiable consent path | **There was no opt-in path at all.** Phone numbers were loaded straight from the roster CSV into `_phone_to_person` (`src/services/sms_transport.py`) — nobody had ever agreed to be texted. |
| 30882 | Terms non-compliant | No SMS terms page, and no STOP/HELP/START handling anywhere in the inbound path. |

### The bug this audit surfaced

`CHECKIN_KEYWORDS` mapped `"help"` → `need_help`. `HELP` is a carrier-reserved
keyword that must always return program information. The consequence was worse
than a compliance miss:

- A staff member texting `HELP` during a live incident was recorded as
  **needing rescue**, inflating the incident commander's "needs assistance"
  count with people who were only asking what the service was.
- They never received the mandated program-info reply.
- `STOP` was not recognized at all, so a person trying to unsubscribe mid-incident
  had their message classified as a **new incident report**, firing the full
  agent fleet.

Fixed: `HELP`/`INFO` return program info, the emergency check-in keyword is now
`SOS`/`NEEDHELP`, and all carrier keywords are handled before any incident
classification.

---

## 3. Blocking checklist — do these before resubmitting

Twilio re-reviews the live pages, not the form text. A reviewer who hits a
placeholder or a 404 rejects again, and repeat rejections slow later reviews.

- [x] **Business identity filled in** across `static/privacy.html`,
      `static/sms-terms.html`, and `static/sms-optin.html`:
      Blockintel Inc · 803 Division St, Nashville, TN 37203 ·
      heartlinmachado@blockintelai.com · +1 (669) 216-7706.
- [ ] **Confirm "Blockintel Inc" matches brand `BN859b518187ea261c57126329975a0f95`
      exactly** — including any "Inc." punctuation the brand record uses. A
      mismatch between the policy page and the registered brand is its own
      rejection reason.
- [ ] **Redeploy**, then confirm all three URLs return 200 with no login and no
      `[[` placeholders — `/privacy`, `/sms-terms`, `/sms-optin`. Error 30921 is
      an automatic rejection if any page is gated.
- [ ] **Set `CRISISMESH_PUBLIC_URL`** if the deployed hostname differs from
      `https://crisismesh-1031148889398.us-central1.run.app`.
- [ ] **Run one opt-in end to end yourself** and screenshot: the filled form, the
      confirmation SMS, your `YES` reply, and the `STOP` and `HELP` replies.
      Twilio frequently asks for these mid-review.
- [ ] **Fix the two URL fields in the campaign form.** Both currently point at
      the site root, which serves the command console. They must be `/privacy`
      and `/sms-terms` — see §4.
- [ ] **Replace the consent answer.** The current text says the roster admin
      supplies the number and that texting in is "implicit opt-in"; both are
      invalid under A2P 10DLC. Paste the §4 replacement.
- [ ] **Tick "Embedded links" and "Phone numbers"** under "Select any content
      your messages may contain" — the HELP reply carries URLs and every message
      carries the 911 line.
- [ ] **Update the Messaging Service** to point at the same URLs, and confirm the
      brand's website field resolves (errors 30907 / 30919).

---

## 4. Console form — field by field

Screens captured 2026-08-22 from
`console.twilio.com/.../senders-onboarding/PN76d09b37c755cd853c14f89a552cbeac/checklist/a2p`.
Two of the three rejection causes are still present in the form as filled, so
fix these before touching anything else:

> **The privacy policy and terms links both point at the site root**
> (`https://crisismesh-1031148889398.us-central1.run.app`), which serves the
> command console. A reviewer following either link finds no policy — that is
> error 30908 and error 30882 directly.
>
> **The consent answer says the roster admin supplies the number and that
> texting in "constitutes implicit opt-in."** Implicit opt-in and
> administrator-supplied consent are both invalid under A2P 10DLC; consent must
> be given by the subscriber themselves. That is error 30909.

Every field below is paste-ready and matches what the code actually sends.

### Campaign description

> CrisisMesh sends emergency coordination messages to opted-in staff and
> designated responders at schools, nonprofits, and similar organizations during
> a crisis incident. Messages include incident acknowledgments, safety check-in
> requests, and situation reports (SITREPs). Recipients opt in themselves,
> either through the public web opt-in form with a double opt-in SMS
> confirmation, or by texting START to the CrisisMesh number. Users reply SAFE,
> SOS, INJURED, or EVACUATED to check in during an incident. Message frequency
> varies and is low — messages are sent only during an active incident. This
> program sends no marketing or promotional content.

*Changed from the rejected version: removed "Users opt in by registering in the
organization's personnel roster" (invalid consent) and swapped HELP for SOS.*

### Sample message #1

> CrisisMesh: you signed up for emergency coordination alerts for Lincoln High
> School. Reply YES to confirm, STOP to cancel, HELP for help. Msg frequency
> varies by incident. Msg & data rates may apply.

### Sample message #2

> CrisisMesh: Incident reported: FIRE (critical). ID: INC-20260821-001.
> CrisisMesh agent fleet is coordinating response. Reply SAFE, SOS, INJURED, or
> EVACUATED to check in. Reply STOP to unsubscribe, HELP for help. If 911 has
> not been called, do so immediately.

### Sample message #3

> CrisisMesh SITREP — INC-20260821-001. Type: FIRE, Severity: CRITICAL. 30/34
> personnel accounted. 4 unaccounted. Assembly point: Athletic Field. Nearest
> fire station: Central Fire Station (ETA 4min). If 911 has not been called, do
> so immediately. Reply STOP to opt out.

### Sample message #4

> CrisisMesh: check-in recorded: Jane Smith — safe. If this is a life-threatening
> emergency, call 911. Reply STOP to unsubscribe.

### Sample message #5

> CrisisMesh emergency coordination alerts. Msg frequency varies by incident.
> Msg & data rates may apply. Reply STOP to cancel. Support:
> heartlinmachado@blockintelai.com. Terms:
> https://crisismesh-1031148889398.us-central1.run.app/sms-terms In an emergency,
> call 911.

### "Select any content your messages may contain"

- [x] **Embedded links** — the HELP reply carries the terms and privacy URLs.
- [x] **Phone numbers** — every message carries the 911 escalation line.
- [ ] Direct lending or loan arrangement — no.
- [ ] Age-gated content — no.

Leaving *Embedded links* unchecked while the HELP reply contains URLs is its own
rejection risk. Over-declaring costs nothing.

### Link to the Campaign's privacy policy

```
https://crisismesh-1031148889398.us-central1.run.app/privacy
```

### Link to the Campaign's terms of service

```
https://crisismesh-1031148889398.us-central1.run.app/sms-terms
```

### How do end-users consent to receive messages?

This is the field that answers 30909. Paste it verbatim.

> End users opt in themselves through one of two paths. Consent is never
> supplied by an administrator on their behalf.
>
> (1) WEB FORM. Staff and designated responders at a subscribing organization
> visit the public opt-in page at
> https://crisismesh-1031148889398.us-central1.run.app/sms-optin — linked from
> the footer of every CrisisMesh page and distributed by the organization's
> safety administrator during staff onboarding. The form collects full name,
> organization, and mobile number, alongside a consent checkbox that is
> UNCHECKED by default and must be actively selected before the form will
> submit. The text beside that checkbox reads: "I agree to receive emergency
> coordination and safety check-in text messages from CrisisMesh at the mobile
> number provided. Message frequency varies by incident. Message and data rates
> may apply. Reply STOP to unsubscribe or HELP for help. Consent is not a
> condition of employment, enrollment, or any purchase." The SMS Terms &
> Conditions and Privacy Policy are linked directly beside the checkbox. On
> submission CrisisMesh records the consent text, its version, a UTC timestamp,
> and the source IP address, then sends a double opt-in confirmation SMS:
> "CrisisMesh: you signed up for emergency coordination alerts for
> [organization]. Reply YES to confirm, STOP to cancel, HELP for help. Msg
> frequency varies by incident. Msg & data rates may apply." No further messages
> are sent unless the user replies YES.
>
> (2) TEXT TO JOIN. A staff member texts START (or JOIN) to the CrisisMesh
> number their organization has published internally. The inbound message is
> itself express consent, and CrisisMesh replies with the program name, the
> frequency disclosure, the rate disclosure, and STOP/HELP instructions.
>
> Consent is never purchased, rented, shared, or inferred from roster
> membership, and is never a condition of employment or enrollment. Opt-in data
> is not shared with any third party.

**Attach as the opt-in screenshot:** `/sms-optin`, showing the unchecked consent
box with its disclosure text and the two policy links.

### Opt-in keywords

```
START,YES,UNSTOP,JOIN,CONFIRM,OPTIN
```

*The rejected version listed only START,YES,UNSTOP. The other three are live in
`OPT_IN_KEYWORDS` and must be declared.*

### Opt-in message

> You are subscribed to CrisisMesh emergency coordination alerts for your
> organization. Msg frequency varies by incident. Msg & data rates may apply.
> Reply HELP for help, STOP to cancel. In an emergency, call 911.

*The rejected version told users to "Reply SAFE, HELP, INJURED, or EVACUATED",
which both conflicts with HELP being a reserved keyword and omits the required
frequency and rate disclosures.*

### Opt-out keywords

```
CANCEL,QUIT,STOP,OPTOUT,UNSUBSCRIBE,STOPALL,REVOKE,END
```

*Already correct — this matches `OPT_OUT_KEYWORDS` exactly. Leave it.*

### Opt-out message

> You have been unsubscribed from CrisisMesh emergency alerts and will receive
> no further messages. Reply START to resubscribe. In an emergency, call 911.

*Adds the brand name, which the rejected version omitted.*

### Help keywords

```
HELP,INFO
```

*Already correct — matches `INFO_KEYWORDS`. Leave it.*

### Help message

> CrisisMesh emergency coordination alerts. Msg frequency varies by incident.
> Msg & data rates may apply. Reply STOP to cancel. Support:
> heartlinmachado@blockintelai.com. Terms:
> https://crisismesh-1031148889398.us-central1.run.app/sms-terms In an emergency,
> call 911.

**265 characters — Twilio caps this field at 320.** The privacy URL was dropped
to fit; CTIA requires only the program name, a customer-care contact, the rate
disclosure, and opt-out instructions in a HELP reply, and the terms page links to
the privacy policy anyway. `_handle_compliance_keyword` sends this exact string,
and a test enforces the cap so the registered text and the live reply cannot
drift apart.

*The rejected version was "Reply STOP to unsubscribe. Msg&Data Rates May Apply."
— no program name and no support contact, both of which carriers require in the
HELP reply.*

## 5. What changed in the codebase

### New files

| File | Purpose |
|---|---|
| `src/services/sms_consent.py` | Consent store: opt-in / double opt-in / opt-out lifecycle, carrier keyword sets, E.164 normalization, JSONL audit log, per-phone and per-IP throttling. |
| `static/privacy.html` | Privacy policy (error 30908). |
| `static/sms-terms.html` | SMS terms & conditions (error 30882). |
| `static/sms-optin.html` | Public opt-in form with an unchecked consent checkbox (error 30909). |
| `tests/test_sms_consent.py` | 29 tests covering the consent lifecycle, carrier keywords, throttling, and outbound suppression. |

### Modified files

| File | Change |
|---|---|
| `src/core/server.py` | `GET /privacy`, `GET /sms-terms` (alias `/terms`), `GET /sms-optin` (alias `/sms-opt-in`), and `POST /sms/optin` — validates consent, name, organization and phone, records the consent, then sends the double opt-in SMS. |
| `src/services/sms_transport.py` | Carrier keywords handled before incident classification; `HELP` removed from `CHECKIN_KEYWORDS` and replaced by `SOS`/`NEEDHELP`/`ASSIST`; `send_sms` suppresses opted-out numbers; incident ack now carries the STOP/HELP notice; check-in ack now carries the brand name and the STOP notice. |
| `tests/test_sms_transport.py` | Updated for the corrected `HELP` behavior; consent log isolated to a tmp path. |
| `tests/test_server.py` | Route tests for the three pages plus the opt-in endpoint, including an assertion that the consent checkbox is never pre-selected. |
| `pyproject.toml`, `Dockerfile` | Declared `requests` — the outbound SMS path imports it directly but only received it transitively through `google-cloud-*`. |
| `README.md` | Check-in keyword references updated to `SOS`; A2P 10DLC note added to the SMS section. |
| `.gitignore` | `data/consent/` — the consent log holds personal data and must never be committed. |

### Mapping fixes to error codes

| Error | Fix |
|---|---|
| 30908 | `static/privacy.html` + `GET /privacy`, carrying the required sentence: "No mobile information will be shared with third parties or affiliates for marketing or promotional purposes… this information will not be shared with any third parties." |
| 30882 | `static/sms-terms.html` + `GET /sms-terms`: program description, both opt-in paths, frequency, rates, full keyword table, carrier disclaimer, 911 notice. |
| 30909 / 30924 / 30925 | Unchecked consent checkbox, disclosure text and version stored per record, double opt-in via SMS `YES`, JSONL consent audit log, throttled public endpoint. |
| 30921 | All three pages served with no authentication. |
| — | `HELP` no longer registers an emergency check-in status; `STOP` no longer opens an incident; outbound SMS suppressed after `STOP`. |

### New environment variables

| Variable | Default | Purpose |
|---|---|---|
| `CRISISMESH_SUPPORT_EMAIL` | `heartlinmachado@blockintelai.com` | Surfaced in the SMS HELP reply. |
| `CRISISMESH_PUBLIC_URL` | the Cloud Run URL | Base for the terms/privacy links in the HELP reply. |
| `CRISISMESH_CONSENT_LOG` | `data/consent/sms_consent.jsonl` | Consent audit log path. |

### Verification

- `python3 -m pytest tests/ -q` → **474 passed**.
- Local server smoke test: `/privacy`, `/sms-terms`, `/sms-optin`, `/terms`, and
  `/sms-opt-in` all return `200 text/html`; `POST /sms/optin` records a pending
  consent row with the disclosure text, version, timestamp, and IP; a submission
  without `consent: true` is rejected with HTTP 400.
- Lint: the new files are clean under `ruff`. The remaining findings in
  `src/core/server.py` and `src/services/sms_transport.py` are pre-existing.

---

## 6. Known limitation

The consent log is a JSONL file inside the container. On Cloud Run that
filesystem is in-memory and per-instance, so consent records **do not survive a
revision rollout** and are not shared across instances. That is adequate for a
demo and for carrier review, but before real staff are enrolled the store in
`src/services/sms_consent.py` should be backed by Firestore — already a project
dependency — so consent survives restarts and is auditable for the four-year
retention the terms promise.

---

## 7. Next steps

1. Fill in the four placeholders and deploy.
2. Run the end-to-end opt-in and capture the screenshots.
3. Update the campaign in the Twilio Console with the §4 copy, then resubmit for
   carrier review.
4. Before production enrollment, move the consent store to Firestore (§6).
