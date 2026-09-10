# Valuing a player by career stage — the peer-reviewed literature

Compiled 2026-09-08, to answer: *what does published, peer-reviewed research say
about what a major league player is worth at each stage of his service clock,
and what should the Contract Clock do with that?*

---

## ⚠️ Read this before using any figure below

**No primary source could be opened.** The build environment blocks web *fetch*
at the network egress layer — Kennesaw, DePaul, arXiv, every publisher tried —
while web *search* works. This is the identical constraint recorded for the tax
rates in CLAUDE.md, and it has the identical consequence:

> Every citation and every number here comes from **search-result metadata and
> abstracts**, not from a paper anyone has read.

So this file uses the project's own tier vocabulary rather than inventing a new
one:

| tier | meaning |
|---|---|
| `cited` | title, authors, journal and year corroborated across two or more independent search results |
| `single_source` | one search result only; the citation itself needs checking |
| `abstract_only` | the figure comes from an abstract or a summary of one, not from the paper's tables |
| `not_peer_reviewed` | real and widely used, but industry or blog work — must never ship wearing this site's authority |

**Nothing in here is fit to publish on the site until a human has opened the
paper.** That is the same bar `verified` means everywhere else in this project,
and it exists for the same reason: a number nobody checked, presented with
confidence, is the one failure mode this project cannot afford.

---

## 1. The frame the whole literature is built on

**Scully, G. W. (1974). "Pay and Performance in Major League Baseball."**
*American Economic Review* **64(6)**, December 1974, 915–930. JSTOR 1815242. —
`verified`

**Read in full 2026-09-10.** ⚠️ Issue number corrected: **64(6), not 64(5).**

Table 2 gives rates of monopsonistic exploitation by player quality, and the
**gross** rate is remarkably flat:

| | gross rate `(GMRP−S)/GMRP` | net rate `(NMRP−S)/NMRP` |
|---|---|---|
| mediocre hitter | .88 | 1.47 |
| mediocre pitcher | .91 | 2.02 |
| average hitter | .89 | .79 |
| average pitcher | .89 | .80 |
| star hitter | .89 | .85 |

Two things worth carrying. The gross rate is **.88–.91 regardless of quality**,
which is where the familiar "players get about 10–20% of their value" line comes
from — and it is close to Blair, Humphreys & Pyun's 0.887 seventy years of
baseball later. But **net MRP is negative for mediocre players** (hence rates
above 1): once training costs and non-player inputs are subtracted, a mediocre
player does not cover himself. Scully's own table therefore already contains the
training-cost argument that article #3 later built on.

The founding paper. Estimates a player's **marginal revenue product** (MRP) in
two stages: a production function (performance → wins) and a revenue function
(wins → revenue). The difference between MRP and salary is the measure of
monopsony power.

Reported finding (`abstract_only`): under the reserve clause, players received
roughly **20% of their MRP**.

**Krautmann, A. C. (1999). "What's Wrong with Scully-Estimates of a Player's
Marginal Revenue Product."** *Economic Inquiry* 37(2), 369–381. — `verified`

**Read in full 2026-09-10, and it settles the central question in this file —
against publishing any share-of-value figure at all.**

### The two methods differ by a factor of twelve

Krautmann applies both to the same population. Per team, surplus extracted from
its reserve-clause players:

| method | surplus per team |
|---|---|
| free-market returns (FMR) | **~$4.5 million** |
| Scully | **over $57 million** |

And for the arbitration-eligible journeyman specifically:

| method | journeyman is paid |
|---|---|
| free-market returns | **~85% of his value** |
| Scully | **~25% of his MRP** |

**Same players, same data, same author — an order of magnitude apart.** The
choice of method, not any fact about baseball, produces most of the spread this
file has been trying to reconcile.

### His falsification test, which is the persuasive part

If clubs really extracted $57M a year from restricted players, that money
should appear as franchise profit. Between 1990 and 1996 the average team's real
accounting profit was about **$4 million**, with roughly **40% of franchises
claiming losses**. The FMR figure (~$4.5M) is about enough to cover the $3–6M a
team spends developing players; the Scully figure is not credible on its face.

### Why Scully-method MRP is biased upward

The method allocates team performance to rostered players in proportion to their
share of at-bats, which "means that the marginal products of non-player inputs
(e.g. coaches and managers) will be inappropriately apportioned to rostered
players." Players are credited with the whole team's wins. He also shows the
technique is "extremely sensitive to the manner in which marginal product is
measured."

### The state of the literature, in his own summary

He documents the disagreement rather than hiding it, and it is worse than this
file assumed:

| source | claim |
|---|---|
| Scully (1989) | typical free agent paid ~**28%** of MRP |
| Zimbalist (1992) | average free agent paid **23% MORE** than his marginal value |
| Zimbalist (1992) | typical journeyman ~**60%** of MRP |
| Krautmann et al. (1997) | journeymen **slightly overpaid** |

Free agents at 28% versus +23% is not a range, it is a contradiction.

### His own result

> "the average apprentice receives about **25% of his MRP**, while the typical
> journeyman receives a salary that is **essentially commensurate with his
> value**."

**This corroborates article #3 and isolates article #1.** Two independent
Krautmann papers, seven years apart, agree the arbitration-eligible player is
paid roughly what he is worth. Blair, Humphreys & Pyun's MER of 0.753 uses the
method this paper argues is biased upward.

---

## 2. The single most useful result — NOW VERIFIED FROM THE PAPER

**Blair, R. D., Humphreys, B. R., & Pyun, H. (2017). "Monopsony Exploitation in
Professional Sport: Evidence from Major League Baseball Position Players,
2000–2011."** *Managerial and Decision Economics* 38(5), July 2017, 676–688.
doi:10.1002/mde.2793 — `verified`

⚠️ **The published article has THREE authors — Roger D. Blair is first.** The
2015 WVU working paper had two. Corroborated on IDEAS/RePEc and Wiley
independently of the source that raised it. Cite it as **Blair, Humphreys &
Pyun (2017)**; the figures below are the working paper's and must be attributed
to that version.

**Read in full 2026-09-10** from the WVU Economics Working Paper (No. 15-48,
30 November 2015). ⚠️ **This is the working paper, not the published
*Managerial and Decision Economics* 38(5) 2017 article.** Numbers can move
between the two. Cite the working paper, or obtain the published version
before citing that.

### The MER definition, settled

The open question was whether the player's share is `MER` or `1 − MER`. The
paper answers it in its own words (p. 17):

> "For rookie players, who are all subject to the reserve clause, the mean MER
> is 0.887; **88.7% of each rookie player's MRP is expropriated** by the team
> that owns his contract; **rookie players are paid only 11% of their MRP**."

So **`1 − MER` is the player's share**, and every share figure previously
recorded here was the right way up. A positive MER means paid below MRP; a
negative MER means paid *above* it.

### Table 4, transcribed exactly

| group | N | mean MER | s.d. | min | max |
|---|---|---|---|---|---|
| **All players** | 3,851 | 0.504 | 0.712 | −10.27 | 0.994 |
| Rookies | 742 | **0.887** | 0.204 | −1.346 | 0.994 |
| Arbitration eligible | 1,161 | **0.753** | 0.283 | −1.743 | 0.989 |
| Free agents | 1,948 | **0.209** | 0.872 | −10.27 | 0.988 |
| CBA 2000–2002 | 950 | 0.556 | 0.598 | −4.50 | 0.994 |
| CBA 2003–2006 | 1,284 | 0.526 | 0.706 | −10.27 | 0.989 |
| CBA 2007–2011 | 1,617 | 0.456 | 0.773 | −7.87 | 0.987 |

Player's share of MRP, as `1 − mean MER`: rookies **11%**, arbitration
**25%**, free agents **79%**. The rookie figure is "remarkably close" (the
paper's phrase) to Scully's 0.89 for 1968–69 — so the two sources that looked
like they disagreed at ~11% vs ~20% **agree**; the ~20% attributed to Scully in
search summaries was the wrong number for that comparison.

### ⚠️ Three caveats that change what may be published

1. **The free-agent mean is not a typical free agent.** Mean 0.209 against a
   standard deviation of **0.872** and a minimum of **−10.27**. **409 of 1,948
   free agents have a negative MER** — paid more than their MRP. The largest is
   Jeff Bagwell's final season (2005). A single "79%" figure would misdescribe
   a distribution this wide. The restricted groups are far tighter (s.d. 0.204
   and 0.283, with only 7 of 742 and 25 of 1,161 negative), so **the
   pre-arbitration and arbitration figures are the publishable ones and the
   free-agent figure is not.**
2. **Position players only, 2000–2011.** No pitchers. Fifteen years old at time
   of reading, and it spans three expired CBAs.
3. **The groups are close to ours but not identical.** The paper uses MLB
   service time and defines a year as 172 days — the same unit this site
   computes. Rookies are 1–2 years, arbitration eligible 3 to under 6. But its
   own results section describes free agents as "seven or more years of
   experience" while its setup says 6, an internal inconsistency typical of a
   working paper. Check against the published version.

### What it also reports

MERs **fell across the three CBAs for free agents only**. For rookies they did
not move at all: "Around 90% of the gross MRP of rookies is still exploited by
teams," and the paper reads rising league minimums as tracking inflation rather
than mitigating monopsony. It uses **gross** MRP throughout — Scully's net-MRP
adjustment for training costs is not applied, because the training-cost data
does not exist. That is the direct link to Krautmann, Gustafson & Hadley (2000)
below, and it means this paper does **not** settle the exploitation-versus-
training-cost question.

## 3. The rest of the stage-by-stage evidence

**Krautmann, A. C., von Allmen, P., & Berri, D. J. (2009). "The Underpayment of
Restricted Players in North American Sports Leagues."** *International Journal
of Sport Finance* 4(3), 155–169. — `cited`

Compares monopsony power across MLB, NBA and NFL directly. Finds owners in all
three exercise it while player movement is restricted, and that **apprentices
are relatively more underpaid than journeymen** — i.e. the gap is widest at the
front of the clock and narrows through arbitration. That *shape* is more useful
to this project than any single level, because the shape is what a career-stage
tool renders.

**Krautmann, A. C., Gustafson, E., & Hadley, L. (2000). "Who pays for minor
league training costs?"** *Contemporary Economic Policy* 18(1), January 2000,
37–47. — `verified`

**Read in full 2026-09-10.** Citation confirmed exactly as recorded. And it
does **not** say what the search summary said it said.

### It contradicts Humphreys & Pyun on the arbitration stage — in sign

This is the most important thing found so far, and it is not a nuance.

| stage | Humphreys & Pyun (2015 wp) | Krautmann, Gustafson & Hadley (2000) |
|---|---|---|
| pre-arbitration | MER 0.887 — paid **11%** of MRP | surplus of **$475k/yr** extracted |
| arbitration eligible | MER 0.753 — paid **25%** of MRP | surplus is **negative** — slightly **overpaid** |

In their own words: "the average journeyman receives a wage that **slightly
exceeds his value** (i.e. his surplus is negative)... the arbitration process
insulates journeymen from the extraction of any surplus, **forcing owners to
recover their investment in training exclusively from apprentices**."

The conclusion presses it further — "the swelling **overpayment to journeymen**
found in this analysis suggests that it is in the owners' interest to make the
arbitration-eligible period as short as possible."

⚠️ **So the arbitration figure is not merely uncertain, it is contested in
sign.** The pre-arbitration finding is robust — both papers agree the gap is
large and concentrated there. The arbitration finding is not, and **must not be
published as a number.**

Why they might differ, none of which is resolvable from here:
* **Method.** Humphreys & Pyun use Scully's MRP; this paper benchmarks against
  what the player *would have earned as a free agent* — the free-market-returns
  approach Krautmann (1999) proposed precisely because he thinks Scully's
  estimates are wrong. **Article #6 is the referee for this disagreement**, which
  makes it far more important than its original ranking suggested.
* **Era.** 1988–1994 versus 2000–2011.
* **Population.** Hitters only (1,121 reserve-clause players: 659 apprentices,
  462 journeymen) versus all position players.

### The other findings, all `verified`

* **The largest surplus comes from the players who cost the least to train.**
  Above-average apprentices yield **$724,000**; below-average ones **$298,000** —
  more than double. This is evidence *against* the training-cost hypothesis and
  for what the authors call the availability hypothesis. **The paper's own title
  question is answered "not really."**
* **Clubs recoup only about half their training costs.** An average team
  extracts roughly **$3M/yr** from its reserve-clause players against player
  development expenses of about **$6M/yr**.
* The authors report a surplus extracted from minority apprentices **10–15%
  higher** than from white apprentices. Recorded because it is in the abstract;
  it bears on the paper's standing, not on anything this tool would compute.

### What this does to the recommendation

The stage panel is **still worth building and its shape changes.** Publishable:
that the gap is largest and best-evidenced in the pre-arbitration years, and
that it narrows sharply at arbitration. Not publishable: any single figure for
the arbitration stage, because two peer-reviewed sources disagree about whether
it is positive at all.

That is a better outcome than a clean number would have been. A tool that shows
where the evidence is strong and says plainly where it runs out is doing the
thing this project is for.


**The most important paper here for how the tool should be worded.** It offers
a competing explanation for the same gap: underpayment of restricted players
may be clubs **recouping the general training costs** they bore in the minors,
rather than pure exploitation. Attributed finding (`abstract_only`):
arbitration-ineligible players are paid about **20% of free-market value**.

The two readings produce the same arithmetic and very different sentences. A
tool that reports the gap as "exploitation" has taken a side in a live academic
dispute. **Report the gap; do not name its cause.** That is the same discipline
as `declineModelled: false`.

---

## 4. Aging — what the value curve does with age, not service

**Fair, R. C. (2008). "Estimated Age Effects in Baseball."** *Journal of
Quantitative Analysis in Sports* 4(1), Article 1. — `verified`

**Read in full 2026-09-10.** ⚠️ **"Peak 28" was a rounding that hides the
result.** The estimated peak ages are per measure:

| measure | peak age | decline by 37 |
|---|---|---|
| OPS (batters) | **27.6** | 0.73%/yr (OBP) |
| OBP (batters) | **28.3** | 0.73%/yr |
| ERA (pitchers) | **26.5** | **1.72%/yr** |

So **pitchers peak roughly two years earlier than hitters and decline more than
twice as fast.** A single peak age for "a player" is not what this paper found,
and for a tool that will be used by pitchers, the difference is the finding.

**Bradbury, J. C. (2009). "Peak athletic performance and ageing: Evidence from
baseball."** *Journal of Sports Sciences* 27(6), 599–610. — `cited`
86 seasons, multiple regression. Peak **~29** for both hitters and pitchers —
explicitly *later* than prior estimates. Different skills peak at different
ages.

**Hakes, J. K., & Turner, C. (2011). "Pay, productivity and aging in Major
League Baseball."** *Journal of Productivity Analysis* 35(1), 61–74.
doi:10.1007/s11123-009-0152-8 — `cited`

⚠️ **This was recorded here as Bradbury (2010). That was wrong on both the
authors and the year** — it is Hakes & Turner, 2011. Corroborated on Springer,
SSRN and RePEc. Free working-paper version: MPRA Paper 4326.

Two reported findings, both directly useful: **the best players peak about two
years later than marginal players**, with development and decline more
pronounced at the highest ability levels; and **free agents are paid
proportionately to production at all ability levels, while young players'
salaries are suppressed by similar amounts.** That second one is a third
independent voice on the stage question, and it agrees with Blair, Humphreys &
Pyun about the front of the clock.

### The correction that matters most to us

**Nguyen, Q., & Matthews, G. J. (2024). "Filling the gaps: A multiple
imputation approach to estimating aging curves in baseball."** *Journal of
Sports Analytics.* — `cited`
**Schuckers, M., et al. (2023).** Regression and imputation approaches to age
curves, incl. a "delta plus" extension of Lichtman's delta method. —
`single_source`

Both attack **survivorship bias**: an aging curve is fitted only on players good
enough to still be playing, so it **overestimates** late-career performance.
Imputing the unobserved seasons flattens or lowers the curve.

⚠️ **This is a direct critique of `scripts/accrual_model.py`.** Our bands are
conditioned on *being in the majors the previous season* — the same selection.
The docstring already argues that conditioning is what the reader is asking
about, and that argument still holds. But the honest statement is now stronger
than the docstring makes it:

> The bands describe **players who kept a job**. A reader who is about to lose
> one is not in that population, and the p20 column is not a floor.

The 5.000–6.000 band sitting flat at 172/172/172 is that bias made visible, not
a fact about durability. Worth a sentence on the page.

---

## 5. The extension decision — and the folk model is wrong

**Solow, J. L., & Krautmann, A. C. (2020). "Do You Get What You Pay for? Salary
and Ex Ante Player Value in Major League Baseball."** *Journal of Sports
Economics* 21(7), 705–722. — `cited`

Method worth copying: value a contract on what could be **anticipated at
signing** — forecast future productivity from recent performance *adjusted for
aging*, convert to expected marginal revenue using **team-specific** win values,
discount to present value. Explicitly rejects judging deals by hindsight.

Reported finding (`abstract_only`), over **106 long-term contracts**: the
agreements smooth compensation, and imply **greater relative risk aversion for
teams than for players** — teams often pay a **premium** for length, to hedge
market volatility and the risk of being unable to replace the player.

**Walters, S. J. K., von Allmen, P., & Krautmann, A. C. (2017). "Risk Aversion
and Wages: Evidence from the Baseball Labor Market."** *Atlantic Economic
Journal* 45(3), September 2017, 385–397. doi:10.1007/s11293-017-9545-7 —
`cited`

✅ **The open question is resolved, and it resolves against the earlier note
here.** These are **two distinct papers by different author teams**, sharing
only Krautmann. Confirmed on Springer and RePEc.

⚠️ **And the resolution reassigns the finding.** The bargaining model, the
**106 long-term contracts**, the **greater relative risk aversion for teams
than players**, and the **~$571,000 per player-year premium (>$248M total)**
all belong to **this paper — Walters, von Allmen & Krautmann (2017)** — not to
Solow & Krautmann (2020), which is where this file previously put them. The
search summaries had bled one paper's content into the other's entry.

**So the recommendation "do not imply an extension is a discount" rests on this
paper, not on the one ranked #2.** That moves it up the reading order
substantially. What Solow & Krautmann (2020) independently contributes is the
*ex ante valuation method* above, which is a different and also useful thing.

**Why this matters more than anything else in the file:** the intuitive story —
*a player trades money for security, a club pays less for the certainty* — is
the opposite of what these report. If the Contract Clock ever implies an
extension is inherently a discount, it will be asserting folk wisdom against the
published evidence.

### And the other side of the same coin

**Krautmann, A. C., & Oppenheimer, M. (2002). "Contract Length and the Return to
Performance in Major League Baseball."** *Journal of Sports Economics* 3(1). —
`cited`

**Krautmann, A. C., & Solow, J. L. (2009). "The Dynamics of Performance over the
Duration of Major League Baseball Long-Term Contracts."** *Journal of Sports
Economics* 10(1), 6–22. — `cited`
Uses OPS; reports an **inverse relationship between contract length and
performance**. Alongside Hakes & Turner (2008), Krautmann & Donley (2009),
Stiroh (2007) and Paulsen (2020) (`single_source` each), addressing selection
into multiyear deals and still finding diminished performance under them.

So: teams pay a premium for length **and** measured performance falls within
long contracts. Both are in the literature, they cut opposite ways, and a tool
that reported only one would be arguing a case.

---

## 6. Arbitration specifically

**Burgess, P., & Marburger, D. (1993). "Do Negotiated and Arbitrated Salaries
Differ Under Final-Offer Arbitration?"** *Industrial and Labor Relations
Review.* — `cited`
When management wins a hearing, its offer was about **9% below** negotiated
settlements for comparable players (`abstract_only`).

**Miller, P. (2000). "An Analysis of Final Offers Chosen in Baseball's
Arbitration System."** *Journal of Sports Economics.* — `single_source`

**Fizel, J., et al. (2002). "Equity and arbitration in Major League
Baseball."** *Managerial and Decision Economics.* — `cited`

**"Arbitrator bias and self-interest: Lessons from the baseball labor market."**
*Journal of Labor Research* (2005). — `single_source`

**What this says about Module B.** Arbitration salaries are set by
**final-offer arbitration against comparables** — backward-looking, and
dominated by a comparison set of other players' salaries. So an arbitration
projection is not a modelling problem this project could solve with better
math; it is a **data** problem, and the data is the thing we do not have and may
not scrape. That is now a sourced statement rather than an assertion.

---

## 6b. A seventeenth work, surfaced by the cross-check

**Krautmann, A. C. (2013). "What Is Right With Scully Estimates of a Player's
Marginal Revenue Product."** *Journal of Sports Economics* 14(1), 97–105. —
`cited`

Not on the original list, and it should have been. Blair, Humphreys & Pyun cite
"Krautmann (1999, 2013)" together, so this is the later instalment of the same
methodological argument — and its title says it revises the 1999 position
rather than repeating it.

**This is now the referee for the central disagreement in this file.** The
pre-arbitration finding is agreed by three independent papers; the arbitration
finding is contested in sign between the Scully-method and free-agent-benchmark
camps. Article #6 (Krautmann 1999) opened that argument and this closed it, or
at least moved it. **Read both, in order, before publishing anything about the
arbitration stage.**

## 7. What is NOT peer reviewed — and must not be treated as if it were

**Dollars per WAR is industry convention, not published research.** —
`not_peer_reviewed`
The $/WAR framework in general use traces to **Matt Swartz's** work at FanGraphs
and The Hardball Times, not to a peer-reviewed journal. Searching specifically
for an econometric treatment returned blogs, a student thesis (Oswald, *Major
Themes in Economics*, 2016 — an undergraduate journal) and FanGraphs. Recent
figures circulating are ~$8M per win in free agency.

**So the number the whole public conversation about player value runs on has no
peer-reviewed foundation.** It is not wrong, and it is not citable at the
standard the rest of this project holds. If the site ever shows a $/WAR figure
it must be labelled as an industry convention with a named source, in the same
way `estimate_unverified` tax rates are.

**Service-time manipulation** has no peer-reviewed economics literature that
this search could find. Coverage is in **law reviews** (Boston College Law
Review; DePaul Journal of Sports Law & Contemporary Problems) and in SABR /
Baseball Prospectus. Law reviews are student-edited, not peer-reviewed. The
Kris Bryant grievance was **decided for the Cubs** — the arbitrator found the
club complied with the CBA. CLAUDE.md's existing line, that grievance outcomes
are invisible to public transaction data, stands.

---

## 8. What this should change in the Contract Clock

Ordered by how much it improves the tool per unit of work, and **none of it
requires salary data**.

### 8a. The stage panel — REVISED after reading #4, #6 and #3

⚠️ **The original recommendation here was to publish the share-of-value figure
for each stage as a sourced range. Reading the papers has killed that**, and it
is worth recording why rather than quietly dropping it.

Krautmann (1999) applies both dominant methods to the same players and gets a
per-team surplus of **$4.5M or $57M** depending only on which he uses. On the
arbitration stage the literature does not disagree about magnitude, it disagrees
about **sign**: two Krautmann papers put the journeyman at roughly his full
value, Blair/Humphreys/Pyun put him at 25% of it. And Krautmann's summary of the
free-agent literature has Scully at 28% against Zimbalist at *plus* 23%.

**A "range" that spans a contradiction is not a range.** Publishing "somewhere
between 11% and 100%" would be worse than publishing nothing: it would look like
a measurement.

**What survives, and it is still worth shipping:**

* **The ordering.** Every source in this file agrees restricted players are paid
  less relative to their value than free agents, and that the gap is largest at
  the front of the clock. Nobody disputes the direction.
* **The mechanism.** *Why* the stages differ — no negotiating rights, then
  final-offer arbitration against comparables, then an open market — is
  documented, uncontested, and is the thing a reader actually wants to
  understand. It needs no number at all.
* **That the size is genuinely unsettled, and why.** A short, plain statement
  that published estimates disagree by an order of magnitude depending on
  method, with the two named. That is real information, it is unusual to say
  out loud, and it is exactly the register this site already uses about its own
  estimates.

So the panel becomes **an explanation with a citation, not a figure with a
range.** Less impressive, considerably more defensible, and still nothing else
free states it.

### 8b. Say what the projection's population actually is

One sentence on the projection panel, from §4: the bands describe players who
kept a job, and the flat 5–6 year band is survivorship, not durability.
Costs nothing and is the difference between a measured claim and an overclaim.

### 8c. Do not imply an extension is a discount

§5 says the evidence points the other way on average — on the authority of
**Walters, von Allmen & Krautmann (2017)**, which is where that finding actually
lives. Whatever the offer panel
grows into, it must not carry the folk model. The advice lint already guards
the vocabulary; this is about the *premise*.

### 8d. Adopt Solow & Krautmann's ex ante framing explicitly

"What could be anticipated at signing" is exactly what this tool does and what
hindsight-based commentary does not. Naming the method — and that it is a
published one — is free credibility for a page that currently reads as a
calculator.

### 8e. Aging is about age, not service

Every threshold on the page is service-time based; every valuation result in
§4 is **age** based, and the two come apart badly for a late debut. The
database holds birth dates. A 26-year-old rookie and a 22-year-old rookie have
the same clock and very different remaining value, and the tool currently
cannot tell them apart.

### What stays absent

Modules B and C are **unchanged**. Nothing found here supplies league-wide
salary data, an arbitration comp set, or a defensible decline branch. §6 makes
the case *stronger*: arbitration is decided on comparables, so the missing
piece is data, not modelling.

---

## 9. The next step, and it is not code

Every figure above is `abstract_only` or thinner. The highest-value next action
is to **read Humphreys & Pyun (2017) and Solow & Krautmann (2020) in full** —
those two carry §8a and §8c between them — and confirm:

1. how Humphreys & Pyun define MER (the `1 − MER` reading is unconfirmed);
2. whether the *Atlantic Economic Journal* 2017 paper and the *JSE* 2020 paper
   are distinct works;
3. whether the ~11% / ~20% / ~25% figures survive contact with the tables.

Both are paywalled. A public library, an alumni login, or emailing the authors
— sports economists reliably send PDFs — all work. Until then this file is a
reading list with provisional numbers, and **nothing in it belongs on the
live site.**
