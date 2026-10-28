# Handoff: Baseball Reference as a validation source

Written 2026-10-28. Audience: whoever runs the next session on this repo.

**Scope.** The live thread in this session was a request to scrape service-time
figures off Baseball Reference, and what to do instead about Justin Verlander,
who really is wrong. Earlier threads from the same session (the dark
"instrument" look, Cloudflare analytics, the explainer page, the stale-data
outage, the paid-expansion brief) are already written up in `CLAUDE.md` — see
"Other threads from this session" at the bottom so you know where they landed
rather than re-deriving them.

Nothing was built. One decision was taken, and some facts were measured. No
code, data or config changed.

---

## 1. The request

The owner found where B-R publishes service time and asked for it to be pulled
for as many players as possible, on the basis that some of our estimates still
look wrong — Verlander specifically.

The recipe they supplied is accurate and worth keeping, because it is a
correct description of B-R's page even though we are not going to automate
against it:

- The figure is **not in any stats table**. It is plain text in the bio block,
  `div#meta`, on the same line as contract status:
  `Service Time (01/2026): 14.070 • Free Agent: 2031`
- Format is `YEARS.DAYS`. It is a **January snapshot**, so it excludes the
  current season's days.
- Player URL: `/players/{first letter of surname}/{bbrefid}.shtml`, e.g.
  `/players/t/troutmi01.shtml`; or the site search endpoint, which redirects
  on a unique match.
- Parse by regex over `div#meta`'s text, not by tag structure:
  `r"Service Time \((\d{2}/\d{4})\):\s*([\d.]+)"`
- Year-by-year opening-day figures are in `div#all_br-salaries`, which B-R
  wraps in an HTML comment, so a parser skips it unless you strip the comment
  markers first.
- Verification targets: Trout `14.070` (01/2026), Sale `15.061` (01/2026).

## 2. The decision: declined, and it should stay declined

Three reasons, each sufficient on its own.

1. **B-R's terms prohibit automated extraction.** This is the same line the
   project already drew over MLB's player photographs: our service-time
   figures are our own reconstruction and defensible as such; someone else's
   data republished is not.
2. **The recipe's step 2 is an access-control bypass, not a politeness
   measure.** B-R returns `403` to default `python-requests`/`curl` user
   agents *in order to* block automated access. Spoofing a browser UA to get
   past it is evading the control that enforces the terms. The 3-second sleep
   and `Retry-After` handling are good manners on top of that, but they do not
   change what the UA swap is for.
3. **It is already the project's recorded decision, in three places** —
   `CLAUDE.md` ("Why Baseball Reference is not scraped, and what is done
   instead"), the `make_reference_worksheet.py` docstring, and the `_README`
   block at the top of `data/reference_service_time.json`.

If the owner raises it again, the thing to re-read is reason 2, because it is
the one that is specific to this recipe rather than general to the project.

## 3. The point that actually matters: B-R is an oracle, not an input

This is the part worth carrying forward, because it is a technical fact rather
than a judgement, and it means the request would not have achieved its goal.

**Scraping B-R cannot fix Verlander.** B-R figures never enter the
computation. Verified in this session:

```
readers of data/reference_service_time.json:
  scripts/validate_service_time.py        <- the check
  scripts/make_reference_worksheet.py     <- picks who to hand-check

pipeline scripts that read it:
  update_service_time.py   no
  service_time.py          no
  backfill_history.py      no
  write_player_pages.py    no
```

(`docs/data/config/cba/2022.json` also matches on a grep, but it is a prose
citation inside a `note` field, not a reader.)

So the reference file is the scoreboard. Published figures come from MLB
transactions via `service_time.py`. Had all 5,000 B-R figures arrived
tomorrow, Verlander would still read exactly what he reads now. The only way a
scraped figure changes the site is by **publishing B-R's number instead of
ours**, which is republishing their data — the thing already declined.

What more B-R figures *would* buy is a wider independent check. That is
genuinely valuable (the roster check validates us against the same feed we are
built from, so a systematic misreading of MLB's semantics passes it), and it
is available by hand — see §5.

## 4. Verlander: he is wrong, and the cause is measured

The owner is right that he is off. What is new here is that the discrepancy is
confined to a region the data itself labels.

Current record (`docs/data/service_time.json`, as of the 2026-10-25 run):

```
id                   434378
name                 Justin Verlander
service_time         21.091
service_days_total   3703
mlb_debut            2005-07-04
first_transaction    2015-04-08     <- nothing in the feed before this
missing_seasons      10
history_complete     False
on_40_man            True
```

His figure moves by a day most days in season, so expect a different number
than `21.091`; the shape below is the stable part.

His season rows, first eleven:

```
2005  d=91   src=presumed   t=[116]
2006  d=172  src=presumed   t=[116]
2007  d=172  src=presumed   t=[116]
2008  d=172  src=presumed   t=[116]
2009  d=172  src=presumed   t=[116]
2010  d=172  src=presumed   t=[116]
2011  d=172  src=presumed   t=[116]
2012  d=172  src=presumed   t=[116]
2013  d=172  src=presumed   t=[116]
2014  d=172  src=presumed   t=[116]
2015  d=172  src=read       t=[116]
```

**2005 through 2014 are all `presumed`** — credited from his debut date at the
full 172-day cap because the feed says nothing about him until a 2015 IL
placement. That is exactly the ten seasons `missing_seasons` counts, and the
recorded reference residual (**+89 days** against B-R's `20.002` at the
2026-01-26 snapshot) lives entirely inside them. He is not wrong by a rule; he
is wrong by a presumption.

Two things worth knowing before anyone tries to improve it:

- `t=[116]` on every presumed season means **MLB's own year-by-year splits
  place him on Detroit in all of them.** That is why finding #12's
  corroboration rule keeps those seasons rather than zeroing them, and it is
  independent evidence he really was rostered — the question is only how many
  days, not whether.
- Finding #17's rule governs what counts as a defect here: *probe against
  MLB's own historical rosters first; only a disagreement with the rosters is
  a defect in this project.* Twice now (Fulford, Agnos) B-R disagreed with us
  and the rosters agreed with us. Verlander's presumed decade has never been
  probed, which is the actual gap.

## 5. The two things to do instead, both sanctioned

Neither was run — the sandbox has no network route (see §7).

**(a) Probe Verlander against MLB's own rosters.** This is the real fix
attempt: it converts guessed seasons into measured ones, from a free,
unlimited, sanctioned endpoint that is already wired up.

```
Actions -> "Validate Service Time" -> check: player
  player_id 434378
  team      116     (Detroit)
  season    2009
  interval  7
```

2009 first because it sits inside the era `CLAUDE.md` calls well-covered, so a
null result there is informative. **It also answers an open question nobody
has settled: how far back the roster endpoint honours a date.** It is verified
to 2011–2012; 2005–2008 is unknown. If 2009 works, walk the rest of the
presumed decade one season at a time.

**(b) Widen the hand-entered check, if more B-R coverage is still wanted.**
The owner looking players up in their own browser is their own browsing and is
entirely fine — the established process is that they read the figures and we
wire them in. `make_reference_worksheet.py` exists to spend that effort well:
it picks players by *churn* (how many roster moves the parser had to classify)
spread across service bands and debut eras, and `--json` emits paste-ready
rows.

Current state of `data/reference_service_time.json`: **19 rows, all 19 carry a
figure.** Mike Trout and Chris Sale — the owner's two verification targets —
are **not** among them, so they would be new rows.

⚠️ The worksheet will not offer Verlander, and that is correct: it only picks
complete-history players, because a player with `missing_seasons > 0` scores
`GAP` rather than pass/fail and so cannot detect a regression.

## 6. A trap hit while measuring, worth not repeating

My first pass at reading Verlander's record guessed the field names and got
silent wrong answers: `credited_days` came back `None` and `by_season` came
back empty, which reads like a missing breakdown rather than a bad key.

The record's actual keys are:

```
id                    not player_id
service_days_total    not credited_days
seasons               not by_season
```

This is the same shape as finding #14's measurement trap (a hand-rolled filter
for nested `"id"` keys dropped every row because the cache stores them flat).
**Print `sorted(record.keys())` before trusting a field read.** A wrong key
here does not raise; it returns `None` and looks like a finding.

## 7. Environment constraint

Both hosts are refused at the egress proxy in this sandbox:

```
statsapi.mlb.com:443          connect_rejected
www.baseball-reference.com:443 connect_rejected
```

So anything needing the live API has to run through Actions, and the B-R
figures could not have been fetched from here even had we decided to. This
matches what `CLAUDE.md` records for findings #21 and the schedule fetch.

## 8. Open questions

- **How far back does `/teams/{id}/roster?rosterType=40Man&date=...` honour a
  date?** Verified to 2011–2012. Unknown before that. Blocks measuring
  Verlander's 2005–2008.
- **Does the roster endpoint place Verlander on Detroit's 40-man through
  2009–2014?** If yes, his presumed seasons can be replaced with measured
  ones. If the endpoint is silent, we match it and the +89 is the known limit
  of public data — the Fulford/Agnos outcome a third time.
- **Would a rules change even follow?** Replacing a presumption for one player
  is not a rule. If the probe shows the presumption systematically
  over-credits durable veterans, that *is* a rule, and it needs a
  `SERVICE_TIME_RULES_VERSION` bump plus a full recompute and both gates
  re-run. Do not change the rule on one player's evidence.
- **`CLAUDE.md` does not cover the October work.** It ends at 2026-09-11
  content, but `git log` shows merges for a contract-clock rebuild, site
  navigation, and a statistics tab. Someone should bring it current; it is the
  file every new session reads first.

## 9. Discussed but not built

- **A B-R scraper.** Declined — §2. Do not build it, and in particular do not
  build it with a spoofed User-Agent.
- **Publishing B-R's figures instead of our own** for players where we
  disagree. Same objection as scraping, plus it would make the site a mirror
  of someone else's data rather than its own reconstruction.
- **The worksheet run.** Offered, not executed — the owner had not chosen
  between it and the probe when the session turned to this document.
- **Flooring a debuted player's credited days at one** (findings #20/#21).
  Untouched here. Already measured as worth 2 days total, so still not worth a
  recompute.

## 10. Files that matter

| file | why |
|---|---|
| `data/reference_service_time.json` | the 19 hand-entered B-R figures; its `_README` states the no-scraping rule |
| `scripts/validate_service_time.py` | the only thing that scores against those figures |
| `scripts/make_reference_worksheet.py` | picks who is worth hand-checking; its docstring records why scraping is refused |
| `scripts/probe_player.py` | the day-by-day roster probe; the tool for §5(a) |
| `scripts/validate_against_rosters.py` | the broad roster gate |
| `.github/workflows/validate.yml` | how to run either from Actions |
| `docs/data/service_time.json` | the database; see §6 for its real field names |
| `scripts/service_time.py` | the accrual engine — where a rule change would go, if one were ever justified |

## Other threads from this session

Already documented in `CLAUDE.md`; listed so you can find them rather than
rediscover them.

- **The dark "instrument" look** — "The look: a telemetry instrument, dark by
  default". Includes the `--club-d` / `prefers-color-scheme` regression and
  the rule that theme variation must key on `[data-theme="light"]`.
- **Cloudflare Web Analytics** — "Immediate next steps" item 2, including why
  not GoatCounter, and the trap that the token lives in *five* places because
  `docs/index.html` cannot read the `ANALYTICS` constant.
- **The five-day silent data outage** — "A scheduled run does not start when
  it is scheduled". GitHub cron starts late; the old gate required the Eastern
  hour to read exactly `08`; eight consecutive runs reported SUCCESS while
  doing nothing. The gate now asks whether the work is done.
- **The explainer page** (`docs/service-time.html`) and the table defaulting
  to current players.
- **The paid-expansion brief** — became the duty-day and contract tools, built
  free and client-side. See "The second tool" and "The third tool".
- **A new-chat starter message** for the expansion work was drafted in chat.
  Superseded: that work shipped, and `CLAUDE.md` is the handoff now.
