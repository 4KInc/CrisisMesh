"""Map real demo identities onto roster people so the loop's arc is visible.

The reconciliation loop only pings people it can actually reach, and the seed
roster carries placeholder Slack ids (`U_PRINCIPAL`) that address nobody — so
out of the box the loop correctly reports almost everyone unreachable and the
ping -> re-ping -> warden-handoff arc happens to nobody you can watch.

This maps a handful of real workspace members onto roster people *chosen so the
arc is legible*, and prints the environment variables to set. It never writes
them to a tracked file: workspace ids and phone numbers stay out of the repo,
which is the whole reason CRISISMESH_DEMO_SLACK_MAP exists.

Who gets mapped, and why it is not arbitrary:

  * One **floor-2 warden** (Mrs. Nguyen, p018). Every floor-2 escalation is
    handed to her by name, so the handoff visibly lands on somebody who is not
    the person running the demo.
  * Two **floor-2 non-wardens**. They are reachable, so they get pinged and
    re-pinged, and at the cap they escalate — to Mrs. Nguyen, above.
  * The **incident commander** (p001), who is also the floor-1 warden and the
    handset the demo declares from.

Everyone else stays unmapped on purpose. The unreachable list is the half of
the loop's output an incident commander acts on, and a demo where all 34 are
reachable would be showing something the deployment cannot do.

    python scripts/seed_demo_identities.py                 # print the env vars
    python scripts/seed_demo_identities.py --apply         # set them on Cloud Run
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request

SERVICE = "crisismesh"
REGION = "us-central1"

# person_id -> why this person. Order is the order real accounts are assigned.
ROLES = [
    ("p001", "incident commander, floor-1 warden, the demo handset"),
    ("p018", "floor-2 warden — receives every floor-2 handoff by name"),
    ("p019", "floor-2, not a warden — pinged, re-pinged, escalated to p018"),
    ("p020", "floor-2, not a warden — pinged, re-pinged, escalated to p018"),
]


def _project() -> str:
    return (os.environ.get("GOOGLE_CLOUD_PROJECT")
            or subprocess.run(["gcloud", "config", "get-value", "project"],
                              capture_output=True, text=True).stdout.strip())


def _bot_token(project: str) -> str:
    if os.environ.get("SLACK_BOT_TOKEN"):
        return os.environ["SLACK_BOT_TOKEN"]
    out = subprocess.run(
        ["gcloud", "run", "services", "describe", SERVICE, "--region", REGION,
         "--project", project, "--format=json"],
        capture_output=True, text=True).stdout
    container = json.loads(out)["spec"]["template"]["spec"]["containers"][0]
    return next((e["value"] for e in container.get("env", [])
                 if e["name"] == "SLACK_BOT_TOKEN"), "")


def _ic_id(project: str) -> str:
    """The operator's Slack id, from the authorised-commander list.

    Placeholders like `U_PRINCIPAL` sit in that list too and address nobody, so
    the first id that looks like a real Slack user id wins.
    """
    if os.environ.get("AUTHORIZED_IC_IDS"):
        raw = os.environ["AUTHORIZED_IC_IDS"]
    else:
        out = subprocess.run(
            ["gcloud", "run", "services", "describe", SERVICE, "--region", REGION,
             "--project", project, "--format=json"],
            capture_output=True, text=True).stdout
        container = json.loads(out)["spec"]["template"]["spec"]["containers"][0]
        raw = next((e["value"] for e in container.get("env", [])
                    if e["name"] == "AUTHORIZED_IC_IDS"), "")
    for candidate in raw.replace("^", ",").split(","):
        candidate = candidate.strip()
        if candidate.startswith("U") and candidate[1:].isalnum() and len(candidate) >= 9:
            return candidate
    return ""


def _members(token: str) -> list[dict]:
    request = urllib.request.Request(
        "https://slack.com/api/users.list?limit=100",
        headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if not payload.get("ok"):
        raise SystemExit(f"Slack refused users.list: {payload.get('error')}")
    return [m for m in payload.get("members", [])
            if not m.get("is_bot") and not m.get("deleted") and m["id"] != "USLACKBOT"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="set the variables on the Cloud Run service")
    parser.add_argument("--phone", default=os.environ.get("CRISISMESH_DEMO_PHONE", ""),
                        help="handset to resolve to p001 (never committed)")
    parser.add_argument("--me", default="",
                        help="your Slack id; pinned to p001. Defaults to the first "
                             "real id in AUTHORIZED_IC_IDS on the service.")
    args = parser.parse_args()

    project = _project()
    token = _bot_token(project)
    if not token:
        print("No SLACK_BOT_TOKEN found in the environment or on the service.")
        return 2

    members = _members(token)
    if len(members) < 2:
        print(f"Only {len(members)} human member(s) in the workspace — the "
              "handoff needs at least two so it lands on somebody else.")
        return 1

    # p001 is the incident commander, the floor-1 warden and the handset the
    # demo declares from — it has to be the operator's own account, not whichever
    # member users.list happens to return first. Anchoring it anywhere else sends
    # the IC's own messages to somebody who is not in the room.
    me = args.me or _ic_id(project)
    ordered = ([m for m in members if m["id"] == me]
               + [m for m in members if m["id"] != me]) if me else members
    if me and ordered and ordered[0]["id"] != me:
        print(f"  {me} is not a member of this workspace — cannot anchor p001 to it.")
        return 1

    pairs = []
    print(f"  {len(members)} workspace member(s); mapping {min(len(ordered), len(ROLES))}:\n")
    for (person_id, why), member in zip(ROLES, ordered):
        pairs.append(f"{person_id}={member['id']}")
        name = member.get("real_name") or member.get("name")
        print(f"    {person_id} <- {member['id']}  ({name})")
        print(f"        {why}")

    # `^` because gcloud reserves the comma in --update-env-vars. Set with a
    # comma and the whole string parses as one id that matches nobody.
    slack_map = "^".join(pairs)
    print("\n  Environment:\n")
    print(f"    CRISISMESH_DEMO_SLACK_MAP={slack_map}")
    if args.phone:
        print(f"    CRISISMESH_DEMO_PHONE={args.phone}")
        print("    CRISISMESH_DEMO_PERSON=p001")

    if not args.apply:
        print("\n  Re-run with --apply to set these on Cloud Run. Nothing was written "
              "to disk: these ids and numbers stay out of the repo.")
        return 0

    env = [f"CRISISMESH_DEMO_SLACK_MAP={slack_map}"]
    if args.phone:
        env += [f"CRISISMESH_DEMO_PHONE={args.phone}", "CRISISMESH_DEMO_PERSON=p001"]
    # ^ delimits the list itself too, since the values contain ^ already.
    subprocess.run(
        ["gcloud", "run", "services", "update", SERVICE, "--region", REGION,
         "--project", project, "--quiet",
         "--update-env-vars", "^|^" + "|".join(env)],
        check=True)
    print("\n  Applied. Reach is still not 34 of 34, and should not be.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
