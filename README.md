# Michigan Labor Bill Scraper

A self-updating scraper that tracks labor-related bills in the Michigan Legislature
(2025–2026 session). It performs an initial full scrape, then re-checks the bills on a
schedule to detect newly introduced and modified bills, logging every change. The result
is a structured dataset published as a browsable [Datasette](https://datasette.io/) app.

## Live data

**Explore the data:** https://michigan-labor-bills.vercel.app/

Three linked tables (`bills`, `actions`, `cosponsors`), joinable on `bill_id`.

## Data source and scope

Data comes from the Michigan Legislature website (`legislature.mi.gov`).

"Labor bills" are operationally defined as the **union of three categories** on the
Michigan Legislature's bill classification, deduplicated by bill ID:

- `Labor`
- `Employment security`
- `Worker's compensation`

This yields **160 bills** for the 2025–2026 session (34 Senate, 126 House). Because this
is a category-based definition, it is a defensible-but-imperfect proxy: a bill that touches
labor issues but is filed under a different primary category would not be captured.

## What is collected

Each bill record contains:

| Field | Description |
|---|---|
| `bill_id` | Unique identifier, e.g. `2025-HB-4001` |
| `chamber` | `Senate` or `House` |
| `title` | Bill title |
| `subject` | What the bill does (from the bill's subject line) |
| `primary_sponsor` | Primary sponsor with district |
| `cosponsors` | List of cosponsors |
| `actions` | Full action history (date + description) |
| `full_text` | Full text of the introduced bill |
| `content_hash` | Change-detection fingerprint (see below) |
| `url` / `text_url` | Source detail page and full-text document |
| `last_scraped` | Timestamp of last scrape |

Flattened into three tables for analysis, the dataset holds ~160 bill rows, ~850 action
rows, and ~2,900 cosponsor rows.

## Architecture

The scraper runs in two modes that share the same parsing core:

- **Initial scrape** (`scrape_all`): collects every labor bill once to build the baseline
  `data.json`. Uses checkpointing (saves after each bill) and resume (skips already-saved
  bills), so an interrupted run can be restarted without losing progress.
- **Update scrape** (`update_scrape`): re-fetches the current labor bill list, then for
  each bill decides:
  - not in `data.json` → **added** (new bill introduced)
  - hash changed → **modified** (new action or update)
  - hash unchanged → **skip**

Every run maintains:

- `data.json` — the core database (`bill_id` → record)
- `change_log.json` — what was added/modified this run
- `error_log.json` — any bills that failed, with the error

Each bill is scraped inside a `try/except`, so one broken page never halts the whole run.

### Change detection via content hash

Change detection uses an MD5 hash of the bill's **entire action history**, not just the
latest action. This is deliberate: Michigan's action history table is **not in
chronological order**, so a newly added action can appear in the middle of the table.
Hashing all actions joined together ensures any change — wherever it lands — alters the
hash.

### Proxy for bot protection

The Michigan Legislature site uses Barracuda/InfiSecure bot protection that blocks
repeated automated requests (and would block GitHub Actions' datacenter IPs). All requests
are routed through the [ScrapeOps](https://scrapeops.io/) proxy, which rotates IPs. The API
key is read from the environment (`SCRAPEOPS_API_KEY`) and never committed.

### Automation

A GitHub Actions workflow (`.github/workflows/scraper.yml`) runs `update_scrape` twice a
week and commits any changed data back to the repository. The commit history therefore
doubles as a record of what changed and when (a "git scraping" pattern).

## Repository layout

scraper.py # scraper: collect, parse, initial + update runs
requirements.txt # Python dependencies
data.json # scraped database (bill_id -> record)
change_log.json # changes from the most recent update run
error_log.json # failures from the most recent run
.github/workflows/scraper.yml # scheduled GitHub Actions workflow
.env # SCRAPEOPS_API_KEY (not committed)


## Running locally

```bash
pip install -r requirements.txt
```

Create a `.env` file with your ScrapeOps key:

SCRAPEOPS_API_KEY=your_key_here


Then run an update:

```bash
python scraper.py
```

## Limitations and notes

- **`full_text` is excluded from the published Datasette app** to keep the deployed
  database small. The full text remains in `data.json` in this repository; each record
  also keeps a `text_url` link to the original document.
- The "labor" definition is category-based (see Scope) and does not guarantee complete
  coverage of every labor-relevant bill.
- Minimal cleaning is applied at scrape time by design; raw source values (e.g. duplicated
  "Representative Rep." strings from the source site) are preserved for later analysis.
