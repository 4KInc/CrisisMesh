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

- [ ] **Replace every `[[PLACEHOLDER]]`** in `static/privacy.html`,
      `static/sms-terms.html`, and `static/sms-optin.html`. They are highlighted
      in red on the rendered pages: `[[LEGAL_ENTITY_NAME]]`,
      `[[BUSINESS_ADDRESS]]`, `[[SUPPORT_EMAIL]]`, `[[SUPPORT_PHONE]]`. The legal
      entity name must match brand `BN859b518187ea261c57126329975a0f95` exactly.
- [ ] **Deploy**, then confirm all three URLs return 200 with no login —
      `/privacy`, `/sms-terms`, `/sms-optin`. Error 30921 is an automatic
      rejection if any page is gated.
- [ ] **Set `CRISISMESH_SUPPORT_EMAIL`** on the Cloud Run service. It is
      interpolated into the SMS HELP reply; the default `support@crisismesh.app`
      is a placeholder.
- [ ] **Set `CRISISMESH_PUBLIC_URL`** if the deployed hostname differs from
      `https://crisismesh-1031148889398.us-central1.run.app`.
- [ ] **Run one opt-in end to end yourself** and screenshot: the filled form, the
      confirmation SMS, your `YES` reply, and the `STOP` and `HELP` replies.
      Twilio frequently asks for these mid-review.
- [ ] **Update the Messaging Service** to point at the same URLs, and confirm the
      brand's website field resolves (errors 30907 / 30919).

---

## 4. Campaign field copy

### Campaign description

> CrisisMesh is an emergency coordination service used by K-12 schools,
> nonprofits, and similar organizations to coordinate their internal response
> during a fire, severe-weather, medical, or active-threat incident. Staff and
> designated responders who have opted in receive incident situation reports and
> safety check-in requests by SMS, and reply with their status (SAFE, SOS,
> INJURED, or EVACUATED) so the incident commander knows who is accounted for.
> All messages are conversational or transactional. The program sends no
> marketing or promotional content.

### Call to action / message flow

Paste verbatim into the campaign's **Message Flow** field. This is what answers
30909, and it must keep matching what the live pages actually do.

> End users opt in through one of two paths, both requiring express written
> consent.
>
> (1) WEB FORM. Staff and designated responders at a subscribing organization
> visit the public opt-in page at
> https://crisismesh-1031148889398.us-central1.run.app/sms-optin — reachable
> from the footer of every CrisisMesh page and distributed by the organization's
> safety administrator during staff onboarding. The form collects full name,
> organization, and mobile number, alongside a consent checkbox that is
> UNCHECKED by default and must be actively selected before the form will
> submit. The text beside that checkbox reads: "I agree to receive emergency
> coordination and safety check-in text messages from CrisisMesh at the mobile
> number provided. Message frequency varies by incident. Message and data rates
> may apply. Reply STOP to unsubscribe or HELP for help. Consent is not a
> condition of employment, enrollment, or any purchase." The SMS Terms &
> Conditions and Privacy Policy are linked directly beside the checkbox. On
> submission CrisisMesh records the consent text, version, UTC timestamp, and
> source IP, then sends a double opt-in confirmation SMS: "CrisisMesh: you
> signed up for emergency coordination alerts for [organization]. Reply YES to
> confirm, STOP to cancel, HELP for help. Msg frequency varies by incident. Msg
> & data rates may apply." No further messages are sent unless the user replies
> YES.
>
> (2) TEXT TO JOIN. A staff member texts START (or JOIN) to the CrisisMesh
> number their organization has published internally. The inbound message is
> itself express consent, and CrisisMesh replies with the program name,
> frequency disclosure, rate disclosure, and STOP/HELP instructions.
>
> Consent is never purchased, rented, or shared, and is never a condition of
> employment or enrollment. Opt-in data is not shared with any third party.

**Opt-in screenshot to attach:** `/sms-optin`, showing the unchecked consent box
with its disclosure text and the two policy links.

### Sample messages

1. > CrisisMesh: you signed up for emergency coordination alerts for Lincoln
   > High School. Reply YES to confirm, STOP to cancel, HELP for help. Msg
   > frequency varies by incident. Msg & data rates may apply.

2. > Incident reported: fire (high). ID: INC-20260821-004. CrisisMesh agent
   > fleet is coordinating response. Reply SAFE, SOS, INJURED, or EVACUATED to
   > check in. Reply STOP to unsubscribe, HELP for help. If 911 has not been
   > called, do so immediately.

3. > Check-in recorded: A. Chen — safe. If this is a life-threatening emergency,
   > call 911. Reply STOP to unsubscribe.

### Keyword replies

| Keyword | Reply sent |
|---|---|
| START, UNSTOP, YES, JOIN | You are subscribed to CrisisMesh emergency coordination alerts for your organization. Msg frequency varies by incident. Msg & data rates may apply. Reply HELP for help, STOP to cancel. In an emergency, call 911. |
| STOP, STOPALL, UNSUBSCRIBE, CANCEL, END, QUIT | You have been unsubscribed from CrisisMesh emergency alerts and will receive no further messages. Reply START to resubscribe. In an emergency, call 911. |
| HELP, INFO | CrisisMesh emergency coordination alerts. Msg frequency varies by incident. Msg & data rates may apply. Reply STOP to cancel. Support: [support email]. Terms: …/sms-terms Privacy: …/privacy In an emergency, call 911. |

### URLs

| Field | URL |
|---|---|
| Privacy Policy | `https://crisismesh-1031148889398.us-central1.run.app/privacy` |
| Terms & Conditions | `https://crisismesh-1031148889398.us-central1.run.app/sms-terms` |
| Opt-in page | `https://crisismesh-1031148889398.us-central1.run.app/sms-optin` |

---

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
| `src/services/sms_transport.py` | Carrier keywords handled before incident classification; `HELP` removed from `CHECKIN_KEYWORDS` and replaced by `SOS`/`NEEDHELP`/`ASSIST`; `send_sms` suppresses opted-out numbers; incident ack now carries the STOP/HELP notice. |
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
| `CRISISMESH_SUPPORT_EMAIL` | `support@crisismesh.app` (placeholder) | Surfaced in the SMS HELP reply. |
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
