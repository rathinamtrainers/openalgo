# Iteration 03 — UC-03: Decline the marginal trade

> **UC-03 — Decline the marginal trade**
>
> **Why this slice:** the catalog's MVP order runs UC-01 → UC-02 → UC-03, iterations 01 and 02 built the first two, so UC-03 is the next un-built entry by catalog priority. It is also the one the desk most needs: the tick already refuses to trade several times an hour, but every refusal is a free-text sentence assembled inline in the decision table and counted nowhere. FR-3 asks for something stricter — a no-trade outcome that is a first-class record, distinguishable by cause, countable and reportable per day and per reason. That is what this iteration builds, and it is the product's primary use case rather than its exception path: refusing churn is where the edge lives.
>
> **Actor:** the Supervisor writes the classified decision; the Trader (Amit) reads the day back with `strike-desk declines`. No new agent joins the desk in this slice.
>
> **Preconditions:** the iteration-02 desk is deployed and ticking on the host, with its journal at `/var/lib/strike-desk/strike_desk.db` holding decisions from earlier sessions. Python 3.12 and `uv` are installed, and OpenAlgo is running with a valid broker session.
>
> **Depends on:** UC-01's decision table, journal and span plumbing and UC-02's regime reads, both of which this slice classifies and counts rather than reshapes. The Options Strategist (UC-04) is still unregistered, so a tradeable regime continues to end the tick as a decline naming the missing `strategist` role — one of the reason codes this iteration classifies.

## 1. What this iteration builds

This iteration builds exactly three things and nothing beyond them: a **decline taxonomy** that is the single home for every reason code the desk can record, the **classification** of every decision row and every `tick.decide` span against that taxonomy, and a **report** that counts a day — or a window of days — by reason, by category and by disposition, from the append-only journal alone.

The taxonomy turns nine loose strings into nine entries. Each entry names its code, the outcome it belongs to, a **category** (`book`, `data`, `regime`, `specialist`, `system`) answering *what kind of thing stopped the trade*, a **disposition** (`routine`, `degraded`, `defect`) answering *how worried you should be*, a one-line summary for reports, and the sentence templates the trader actually reads. The decision table stops writing sentences with f-strings and starts naming a code and handing the taxonomy its facts, so the wording of a verdict lives in exactly one place and is pinned by a golden file.

The `decisions` table gains two nullable columns, `reason_category` and `reason_disposition`, written on both paths that append a decision — the graph's `persist` node and the runner's internal-error fallback. They are a denormalised convenience, not the source of truth: the report resolves a category from the taxonomy by reason code at read time, which is the only way rows written by iterations 01 and 02 can be classified at all, because an append-only table can never be backfilled.

The report is a terminal command, `strike-desk declines`, and it is the whole of the reporting surface — Strike Desk exposes no new remote surface, and the trader reaches it over the same shell he already uses to run `status`. It prints the day's total split by outcome, the per-reason table with each code's category, disposition and summary, the same counts rolled up to category and disposition, the day's first and last decision in IST, the token cost those decisions carried, the regime labels the day's ticks read, and a record-health block that names anything the journal itself got wrong. `--since N` widens it to a window, `--json` prints the identical numbers for a script, and the exit code is 2 when the window contains a defect, so a habit or a timer notices without anyone reading the text.

## 2. Main success scenario

1. A tick runs exactly as iteration 02 left it: the gate allows it, the book is flat, the Regime Analyst answers, and the decision table lands on a branch.
2. The branch names a reason code — say `regime-not-tradeable` — and hands the taxonomy the facts that belong in the sentence: the label it read, and the analyst's rationale.
3. The taxonomy renders one line from that code's template, collapses it to a single line, appends `Analyst: <rationale>` if there is room inside the 400-character cap, and truncates the rationale rather than the verdict if there is not.
4. The `decide` node looks the code up once more to stamp `decision.reason_category = regime` and `decision.reason_disposition = routine` onto the `tick.decide` span next to the outcome and code it already carried.
5. The `persist` node appends the decision row with the sentence and both classification columns, and the tick ends as it always did.
6. After the close the trader runs `strike-desk declines`. The command opens the journal, adds any missing column, and groups the day's rows by outcome, reason code and stored category in one query.
7. Every group is classified through the taxonomy by its code — not by what the row stored — and the counts are rolled up by category and disposition; the stored value is compared against the taxonomy only to count rows that are unstamped or have drifted.
8. The command emits one `report.declines` span carrying the taxonomy artifact, the day, the total, the defect count and the health verdict, prints the report, and exits 0 because the day held no defect and no unknown code.

## 3. Alternate flows

**A1 — A window instead of a day.** `strike-desk declines --since 5` asks the journal for the five most recent days that hold a decision, builds each day's report, and prints a per-day table followed by the same disposition, category and reason breakdowns aggregated across the window. Bare `--since` uses the configured default. The window is built from day reports, so a number in the summary can always be traced to the day it came from.

**A2 — A machine reads it.** `--json` prints the same report as a JSON document stamped with the taxonomy version and digest. Nothing is computed differently for it; the text and the JSON are two renderings of one object, which is why they cannot disagree.

**A3 — A day with nothing in it.** A day the desk never ticked — a holiday, or a day it was stopped — reports zero decisions, empty breakdowns and a clean health block, and exits 0. An empty day is a fact about the desk, not an error.

**A4 — An older journal.** Opening a database written before this release adds the two columns in place and reports its rows normally: they classify from the taxonomy and are counted under `unstamped rows` in the health block, so the gap is visible without pretending it is a defect.

## 4. Exception flows

**E1 — A code the taxonomy does not define.** A row carrying an unrecognised reason code — a journal from a future release, or a hand-inserted row — is classified `unknown`/`defect`, listed by name in the health block, and makes the command exit 2. Nothing is dropped and nothing is silently bucketed into a real category.

**E2 — A stored category that disagrees with the taxonomy.** When a row's `reason_category` differs from what its code resolves to today, the row is counted under the taxonomy's answer and the disagreement is counted as `taxonomy drift`. Drift means a code changed category between the write and the read, which is exactly the kind of change the version and digest exist to attribute.

**E3 — A malformed argument.** A `--day` that is not an ISO date, a `--since` that is not a positive whole number, or `--day` and `--since` together are rejected by the argument parser before the journal is opened, with a message naming what was wrong.

**E4 — A template field the branch forgot to supply.** The renderer substitutes `unspecified` and logs a warning rather than raising inside a tick. A tick that cannot phrase its verdict still records it; a missing field is a defect to fix, never a reason to lose a decision.

**E5 — A malformed taxonomy.** An entry with an unknown category, an unknown disposition, no summary, no default template or a duplicated code raises at import, so the service fails to start rather than running with a taxonomy it cannot trust.

## 5. Operational behaviour

This slice runs no model call and no agent loop: it classifies and counts what the reasoning plane already produced. Its operational work is therefore the *Ops layer the architecture assigns to the record itself.

**AgentOps.** The two new span attributes on `tick.decide` mean a trace answers "why did this tick not trade, and what kind of not-trading was it?" without joining to the journal, and the `report.declines` span records that a report was taken, over what, and what it concluded — so the act of looking is itself in the append-only trace.

**PromptOps, applied to the record rather than the prompt.** The taxonomy is a versioned artifact exactly as the prompt registry is: `TAXONOMY_VERSION` plus a content digest over every entry, printed in `status`, in the report and on the report span. A wording change moves the digest, and the golden file in the test suite fails until the new sentence is written down deliberately. That is what stops the trader-facing explanation from drifting one f-string at a time.

**Guardrails.** Two of them bind structurally rather than by instruction. The **parity check** reflects over the graph's `REASON_*` constants and the taxonomy's entries and fails the build in either direction, so a new reason code cannot ship uncategorised and a retired entry cannot linger. The **health block** refuses to hide a bad record: an unknown code, a drifted category or an incomplete trace is named and changes the exit code, because a report that flatters the journal is worse than no report.

## 6. Data touched

The journal's `decisions` table gains `reason_category` (`VARCHAR(24)`, nullable, indexed) and `reason_disposition` (`VARCHAR(16)`, nullable), and `SCHEMA_VERSION` moves to 3. Both are nullable with no default, which is what lets `ALTER TABLE ADD COLUMN` widen an existing table without touching a row and lets the previous release keep writing if you roll back. Rows written before this release keep `NULL` in both columns forever — the append-only triggers make backfill impossible, and that is the intended posture, not a limitation to work around.

Reads are all local and read-only: the day's `decisions` grouped by outcome, code and stored category; the first and last `created_at_utc` of the day; the sum of `token_cost_micros` on those decisions; the count of rows with `trace_complete = 0`; the day's `regime_reads` with `source = 'tick'` grouped by label; and the distinct recent `trading_day` values. No table outside Strike Desk's own database is touched, no market data is fetched, and no model is called.

## 7. Acceptance criteria

1. **AC-1** — Every decision row the desk writes, from the graph's `persist` node and from the runner's internal-error path alike, carries a `reason_code` the taxonomy defines plus `reason_category` and `reason_disposition` equal to that code's entry.
2. **AC-2** — The taxonomy gives every code exactly one category from `book`, `data`, `regime`, `specialist`, `system` and one disposition from `routine`, `degraded`, `defect`; a code the graph can emit but the taxonomy does not define, or an entry no branch emits, fails the build.
3. **AC-3** — `reason_text` is produced only by the taxonomy: one line, at most `STRIKE_DESK_REASON_TEXT_MAX_CHARS` characters, naming the decisive fact, and identical for identical inputs. The analyst's rationale is appended after `Analyst: ` and is the only part ever truncated.
4. **AC-4** — `strike-desk declines` prints, for one IST trading day: the total and its split by outcome; per-reason counts and shares with each code's category, disposition and summary; the same counts rolled to category and disposition; the day's first and last decision in IST; and the token cost carried on those decisions.
5. **AC-5** — The same report prints the regime labels the day's ticks read, counted from `regime_reads` rows with `source = 'tick'`.
6. **AC-6** — `--since N` reports the most recent N journalled trading days as a per-day table plus the window's aggregated breakdowns; `--json` prints the identical numbers as a JSON document; `--day` and `--since` are mutually exclusive, and a malformed date or count is rejected before the journal is opened.
7. **AC-7** — Every number comes from the append-only journal at read time, so re-running a past day returns identical output, and a category is resolved from the taxonomy by reason code so rows written before these columns existed are still classified.
8. **AC-8** — The report names record-health problems rather than absorbing them: rows with no stored category (`unstamped`), rows whose stored category disagrees with the taxonomy (`drift`), decisions with an incomplete trace, and reason codes the taxonomy does not know.
9. **AC-9** — Opening a journal written by the previous release adds both columns in place with no row read, rewritten or deleted, leaves the append-only `UPDATE` and `DELETE` triggers refusing exactly as before, and `create_schema()` is safe to call repeatedly.
10. **AC-10** — `tick.decide` carries `decision.reason_category` and `decision.reason_disposition` beside the outcome and code it already carried, and every report emits one `report.declines` span carrying the taxonomy artifact, the day count, the total, the defect count and the health verdict.
11. **AC-11** — `strike-desk declines` exits 0 when the window holds no defect-disposition decision and no unknown code, and 2 when it holds either; routine and degraded declines never make the exit non-zero. `strike-desk status` prints its per-day breakdown from the same report code, so the two commands cannot disagree.
12. **AC-12** — The taxonomy is a versioned artifact: `TAXONOMY_VERSION` and a content digest over its entries appear in `status`, in the report and on the report span, and a frozen golden file pins every trader-facing sentence so a wording change fails CI until the file is updated deliberately.

## 8. The flow

```mermaid
sequenceDiagram
    participant S as Supervisor tick
    participant T as Decline taxonomy
    participant J as Journal (append-only)
    participant O as Trace (spans)
    participant A as Trader

    S->>S: decision table picks a branch
    S->>T: render(reason_code, facts, rationale)
    T-->>S: one capped, deterministic sentence
    S->>T: describe(reason_code)
    T-->>S: category + disposition
    S->>O: tick.decide + reason_category/disposition
    S->>J: append decision (code, sentence, both columns)

    A->>J: strike-desk declines [--day | --since N]
    J-->>A: grouped counts + regime labels + health
    A->>T: classify every code read back
    T-->>A: category, disposition, summary
    A->>O: report.declines (taxonomy, total, defects, healthy)
    A-->>A: text or JSON; exit 0 clean, 2 on a defect
```
