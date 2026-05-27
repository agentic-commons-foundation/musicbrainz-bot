# musicbrainz-bot

A bot that adds Latin-script aliases for CJK-language (Chinese / Japanese / Korean) artists on [MusicBrainz](https://musicbrainz.org), backed by multiple independent external sources.

Operated by the [Agentic Commons](https://agentic-commons.org) project as the account [AgenticCommonsBot](https://musicbrainz.org/user/AgenticCommonsBot).

## What it does

For a CJK-language artist on MusicBrainz who:

- has an established Latin-script (typically English) name on independent external sources (Wikidata, English Wikipedia, official platforms), and
- does **not** yet have a corresponding alias on MusicBrainz,

this bot submits a single `Artist name` alias edit, with an edit note citing **at least two independent external sources** as evidence.

It does not vote on any edits — not on its own submissions, not on anyone else's.

## How it works

Each invocation submits **one** alias edit. The proposal (which artist, which alias, which sources) is generated upstream by a human-supervised, QA-reviewed pipeline and passed in as a JSON file.

```
proposal.json  ──►  mb_alias_bot.py  ──►  POST /artist/{mbid}/add-alias
                                          (with edit note citing sources)
```

The script is intentionally small and dependency-free (Python standard library only) so the actual MusicBrainz-facing code stays auditable in one place.

## Usage

```bash
# Required env vars
export MB_BOT_USERNAME=AgenticCommonsBot   # default if unset
export MB_BOT_PASSWORD=...                 # required

# Dry-run (default) — does NOT submit
python3 mb_alias_bot.py --proposal path/to/proposal.json

# Live submission
python3 mb_alias_bot.py --proposal path/to/proposal.json --live
```

### Proposal JSON shape

Either a single proposal:

```json
{
  "mbid": "...",
  "artist_name_primary": "...",
  "proposed_alias": {
    "name": "Latin-script name",
    "type": "Artist name",
    "locale": "en",
    "sort_name": "...",
    "primary_for_locale": false
  },
  "edit_note": "Source 1: <url>\nSource 2: <url>\n..."
}
```

Or a wrapper with multiple items (use `--item-index` to select):

```json
{ "items": [ { ... }, { ... } ] }
```

## Compliance with MusicBrainz Bot Code of Conduct

| Requirement | Status |
|---|---|
| Bot user type approved | Following the [Code of Conduct for Bots](https://musicbrainz.org/doc/Code_of_Conduct/Bots) approval process |
| Source code open-source | This repository |
| User page identifies maintainer | Done |
| User page links to source code | Done |
| Bot logs in on every run | Done |
| Bot does not vote on any edits | Done |
| Daily edit cap (1000/day) | Planned cap during ramp-up: ≤ 100/day |
| Open edit cap (2500 hard) | Monitored by upstream pipeline |
| Owner responds to edit notes | Routed to wiki-bot@agentic-commons.org |

## Contact

- **Operator**: Agentic Commons project
- **Contact**: wiki-bot@agentic-commons.org
- **MusicBrainz user page**: https://musicbrainz.org/user/AgenticCommonsBot

For questions, concerns, or to report a problematic edit: please email the operator, or comment on the edit in question — we will respond.

## License

[MIT](LICENSE)
