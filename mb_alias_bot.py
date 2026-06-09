#!/usr/bin/env python3
# Copyright 2026 Agentic Commons (in formation)
# SPDX-License-Identifier: Apache-2.0
"""
MusicBrainz alias-submission bot.

Reads a proposal JSON (typically produced by an upstream research agent and
human-reviewed for quality) and submits a single alias edit to MusicBrainz.

Required env vars:
  MB_BOT_USERNAME — defaults to "AgenticCommonsBot"
  MB_BOT_PASSWORD — required, no default

Usage:
  python3 mb_alias_bot.py --proposal path/to/proposal.json            # dry-run
  python3 mb_alias_bot.py --proposal path/to/proposal.json --live     # submit

Proposal JSON shape — either a single proposal:
  {"mbid": "...", "proposed_alias": {...}, "edit_note": "..."}
or an artifact wrapper containing one or more proposals:
  {"items": [{"mbid": "...", "proposed_alias": {...}, ...}, ...]}
When multiple items are present, --item-index N selects which (default 0).

Stdlib only (no requests / BeautifulSoup). Handles MetaBrainz's JS
proof-of-work challenge on first protected GET if encountered.
"""

import argparse
import hashlib
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

# ---------- HTTP setup ----------

BASE = "https://musicbrainz.org"
UA = "AgenticCommonsBot/0.1 ( https://agentic-commons.org ; wiki-bot@agentic-commons.org )"

def make_opener():
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar),
        urllib.request.HTTPRedirectHandler(),
    )
    opener.addheaders = [
        ("User-Agent", UA),
        ("Accept-Language", "en-US,en;q=0.9"),
    ]
    return opener, jar

def get(opener, url):
    print(f"  GET  {url}", file=sys.stderr)
    req = urllib.request.Request(url)
    with opener.open(req, timeout=30) as r:
        body = r.read().decode("utf-8", errors="replace")
        return r.geturl(), r.status, body

def post(opener, url, data, referer=None):
    print(f"  POST {url}", file=sys.stderr)
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    if referer:
        req.add_header("Referer", referer)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with opener.open(req, timeout=30) as r:
        return r.geturl(), r.status, r.read().decode("utf-8", errors="replace")

# ---------- proof-of-work solver (in case MB challenges us) ----------

POW_RE = re.compile(
    r'const c="([0-9a-f]+)",t="(\d+)",d=(\d+),', re.S
)

def looks_like_pow_challenge(body):
    return "Verifying your browser" in body and "/__meb_verify" in body

def solve_pow(body):
    """MetaBrainz PoW: find n s.t. SHA-256(c+str(n)) hex starts with d zeros."""
    m = POW_RE.search(body)
    if not m:
        raise RuntimeError("PoW page detected but couldn't parse challenge parameters")
    c, t, d = m.group(1), m.group(2), int(m.group(3))
    target = "0" * d
    print(f"  [pow] solving challenge c={c[:12]}... d={d} ...", file=sys.stderr)
    start = time.time()
    n = 0
    while True:
        h = hashlib.sha256((c + str(n)).encode()).hexdigest()
        if h.startswith(target):
            elapsed = time.time() - start
            print(f"  [pow] solved n={n} in {elapsed:.2f}s (hash={h[:12]}...)", file=sys.stderr)
            return {"c": c, "t": t, "n": str(n), "d": str(d)}
        n += 1

def maybe_solve_pow(opener, url, body):
    """If body is a PoW challenge, solve and POST verification, return new body."""
    if not looks_like_pow_challenge(body):
        return body
    solution = solve_pow(body)
    solution["r"] = urllib.parse.urlsplit(url).path  # original path to return to
    final_url, status, new_body = post(opener, BASE + "/__meb_verify", solution, referer=url)
    if looks_like_pow_challenge(new_body):
        raise RuntimeError("PoW solved but server still serves challenge — aborting")
    print(f"  [pow] verification accepted, now at {final_url}", file=sys.stderr)
    return new_body

# ---------- form parsing helpers ----------

INPUT_NAME_VALUE_RE = re.compile(
    r'<input\b[^>]*?\bname="([^"]+)"[^>]*?\bvalue="([^"]*)"', re.I
)
INPUT_VALUE_NAME_RE = re.compile(
    r'<input\b[^>]*?\bvalue="([^"]*)"[^>]*?\bname="([^"]+)"', re.I
)

def extract_inputs(body):
    """Extract all <input name=X value=Y> pairs (name → value)."""
    fields = {}
    for m in INPUT_NAME_VALUE_RE.finditer(body):
        fields[m.group(1)] = m.group(2)
    for m in INPUT_VALUE_NAME_RE.finditer(body):
        fields.setdefault(m.group(2), m.group(1))
    return fields

# ---------- main flow ----------

def login(opener, username, password):
    print(f"\n[1] GET /login to extract CSRF tokens", file=sys.stderr)
    url, status, body = get(opener, BASE + "/login")
    body = maybe_solve_pow(opener, url, body)
    fields = extract_inputs(body)
    csrf_session_key = fields.get("csrf_session_key")
    csrf_token = fields.get("csrf_token")
    if not csrf_session_key or not csrf_token:
        raise RuntimeError(f"could not find csrf fields on /login (got: {list(fields)})")
    print(f"  csrf_token={csrf_token[:12]}... session_key={csrf_session_key[:12]}...", file=sys.stderr)

    print(f"\n[2] POST /login with credentials", file=sys.stderr)
    payload = {
        "csrf_session_key": csrf_session_key,
        "csrf_token": csrf_token,
        "username": username,
        "password": password,
        "remember_me": "1",
    }
    url, status, body = post(opener, BASE + "/login", payload, referer=BASE + "/login")
    body = maybe_solve_pow(opener, url, body)
    if "Incorrect username or password" in body or 'name="password"' in body and "Log in" in body:
        raise RuntimeError("login appears to have FAILED (returned login form again)")
    print(f"  landed at {url} (status {status}) — login looks OK", file=sys.stderr)

def fetch_alias_form(opener, mbid):
    url = f"{BASE}/artist/{mbid}/add-alias"
    print(f"\n[3] GET add-alias form", file=sys.stderr)
    final_url, status, body = get(opener, url)
    body = maybe_solve_pow(opener, final_url, body)
    if "/login" in final_url:
        raise RuntimeError("redirected to /login — not authenticated")
    fields = extract_inputs(body)
    if not fields:
        raise RuntimeError("no <input> fields found in alias form response")
    # detect the name prefix MB uses (e.g. "edit-alias.name")
    name_field = next((k for k in fields if k.endswith(".name") and "alias" in k.lower()), None)
    prefix = name_field.rsplit(".", 1)[0] if name_field else None
    print(f"  detected form prefix: {prefix!r}", file=sys.stderr)
    print(f"  detected {len(fields)} input fields", file=sys.stderr)
    return final_url, body, fields, prefix

def discover_alias_type_id(body, type_name):
    """Find the option value for given alias-type display name in the <select>."""
    m = re.search(r'<select\b[^>]*name="[^"]*\.type_id"[^>]*>(.*?)</select>', body, re.S | re.I)
    if not m:
        return None
    options = re.findall(r'<option\s+value="(\d+)"[^>]*>([^<]+)</option>', m.group(1))
    for value, label in options:
        if label.strip().lower() == type_name.lower():
            return value
    return None

def build_alias_payload(fields, prefix, type_id, proposal):
    """Compose the POST payload from a proposal dict + the form's CSRF fields."""
    csrf_session_key = fields.get("csrf_session_key", "")
    csrf_token = fields.get("csrf_token", "")
    pa = proposal["proposed_alias"]
    begin = pa.get("begin_date") or {}
    end = pa.get("end_date") or {}
    payload = {
        "csrf_session_key": csrf_session_key,
        "csrf_token": csrf_token,
        f"{prefix}.name": pa["name"],
        f"{prefix}.sort_name": pa["sort_name"],
        f"{prefix}.locale": pa.get("locale", "en"),
        f"{prefix}.type_id": pa.get("type_id") or type_id or "1",
        f"{prefix}.primary_for_locale": "1" if pa.get("primary_for_locale") else "0",
        f"{prefix}.begin_date.year": str(begin.get("year") or ""),
        f"{prefix}.begin_date.month": str(begin.get("month") or ""),
        f"{prefix}.begin_date.day": str(begin.get("day") or ""),
        f"{prefix}.end_date.year": str(end.get("year") or ""),
        f"{prefix}.end_date.month": str(end.get("month") or ""),
        f"{prefix}.end_date.day": str(end.get("day") or ""),
        f"{prefix}.ended": "1" if pa.get("ended") else "0",
        f"{prefix}.edit_note": proposal["edit_note"],
        f"{prefix}.make_votable": "0",
    }
    return payload

REQUIRED_PROPOSAL_KEYS = ("mbid", "proposed_alias", "edit_note")
REQUIRED_ALIAS_KEYS = ("name", "sort_name", "locale")

def load_proposal(path, item_index):
    """Read a proposal JSON file and return the single proposal dict to submit."""
    with open(path) as f:
        doc = json.load(f)
    # artifact wrapper {"items": [...]} OR single proposal {"mbid": ...}
    if isinstance(doc, dict) and "items" in doc:
        items = doc["items"]
        if not items:
            raise ValueError(f"proposal file {path}: 'items' is empty")
        if item_index < 0 or item_index >= len(items):
            raise ValueError(f"--item-index {item_index} out of range (have {len(items)} items)")
        proposal = items[item_index]
    elif isinstance(doc, dict) and "mbid" in doc:
        proposal = doc
    else:
        raise ValueError(f"proposal file {path}: expected single proposal or {{items:[...]}} wrapper")

    for k in REQUIRED_PROPOSAL_KEYS:
        if k not in proposal:
            raise ValueError(f"proposal missing required field: {k!r}")
    pa = proposal["proposed_alias"]
    for k in REQUIRED_ALIAS_KEYS:
        if k not in pa:
            raise ValueError(f"proposed_alias missing required field: {k!r}")
    return proposal

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--proposal", required=True,
                    help="Path to proposal JSON file (single proposal or {items:[...]} wrapper).")
    ap.add_argument("--item-index", type=int, default=0,
                    help="When proposal file is an {items:[...]} wrapper, which item to submit (default 0).")
    ap.add_argument("--live", action="store_true",
                    help="Actually submit. Default is DRY-RUN (does not POST the alias).")
    args = ap.parse_args()

    username = os.environ.get("MB_BOT_USERNAME", "AgenticCommonsBot")
    password = os.environ.get("MB_BOT_PASSWORD")
    if not password:
        print("ERROR: MB_BOT_PASSWORD env var is required.", file=sys.stderr)
        sys.exit(2)

    proposal = load_proposal(args.proposal, args.item_index)
    target_mbid = proposal["mbid"]
    target_label = f"{proposal.get('artist_name_primary','?')} → {proposal['proposed_alias']['name']}"

    print("=" * 70, file=sys.stderr)
    print(f"MusicBrainz alias-submission bot — {'LIVE' if args.live else 'DRY-RUN'}", file=sys.stderr)
    print(f"Proposal: {args.proposal} (item {args.item_index})", file=sys.stderr)
    print(f"Target:   {target_label}", file=sys.stderr)
    print(f"MBID:     {target_mbid}", file=sys.stderr)
    print(f"User:     {username}", file=sys.stderr)
    print("=" * 70, file=sys.stderr)

    opener, jar = make_opener()
    login(opener, username, password)

    final_url, body, fields, prefix = fetch_alias_form(opener, target_mbid)
    if not prefix:
        raise RuntimeError("could not detect alias form field prefix")
    declared_type = proposal["proposed_alias"].get("type", "Artist name")
    type_id = discover_alias_type_id(body, declared_type)
    print(f"  detected type_id for {declared_type!r}: {type_id}", file=sys.stderr)

    payload = build_alias_payload(fields, prefix, type_id, proposal)

    print("\n[4] Prepared POST payload:", file=sys.stderr)
    for k, v in payload.items():
        display = v if len(v) < 80 else v[:77] + "..."
        print(f"    {k} = {display!r}", file=sys.stderr)

    if not args.live:
        print("\n[DRY-RUN] Would POST the above payload to:", file=sys.stderr)
        print(f"           {BASE}/artist/{target_mbid}/add-alias", file=sys.stderr)
        print("[DRY-RUN] Not submitting. Re-run with --live to actually submit.", file=sys.stderr)
        return

    print(f"\n[5] LIVE submission — POST to /artist/{target_mbid}/add-alias", file=sys.stderr)
    submit_url = f"{BASE}/artist/{target_mbid}/add-alias"
    result_url, status, result_body = post(opener, submit_url, payload, referer=final_url)
    print(f"  landed at: {result_url}", file=sys.stderr)
    print(f"  status: {status}", file=sys.stderr)
    edit_id_match = re.search(r"/edit/(\d+)", result_url)
    if edit_id_match:
        edit_id = edit_id_match.group(1)
        print(f"\n✅ SUCCESS — edit ID: {edit_id}", file=sys.stderr)
        print(f"   URL: {BASE}/edit/{edit_id}", file=sys.stderr)
        print(edit_id)
    else:
        # Maybe redirected to artist page after auto-apply
        m_art = re.search(r"/artist/" + re.escape(target_mbid), result_url)
        if m_art:
            print(f"\n⚠️  Redirected to artist page — alias likely applied, but no edit ID in URL.", file=sys.stderr)
            print(f"   Check {BASE}/user/{username}/edits", file=sys.stderr)
        else:
            print(f"\n❌ Submission may have failed. Check response saved to /tmp/mb_alias_bot_result.html", file=sys.stderr)
            with open("/tmp/mb_alias_bot_result.html", "w") as f:
                f.write(result_body)

if __name__ == "__main__":
    main()
