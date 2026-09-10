# Iteration 04 — Manual Test Cases

These are the checks you run with your own hands before the suite in `04_test_automation.md`
runs them forever and before `05_deployment_guide.md` releases the code to the trading host.

**Read this first: the table below is split in two, and the split is the point.** Iteration 03
was testable end to end on a Saturday because it read a journal. This slice reads a live
option chain, and a chain cannot be read without a working broker session — the same session
that is currently returning `Incorrect api_key or access_token` on `/quotes`. So the cases
are partitioned by what they actually need:

| Block | Needs | When you can run it |
| --- | --- | --- |
| **A — off-hours** | Nothing but the repo. No broker, no market, no Anthropic key. | Now. Any day, any hour. |
| **B — market hours** | A reconnected Zerodha session, an Anthropic key, and NIFTY open. | Only after the broker is back. |

Block A is deliberately the larger of the two, and it covers the slice's central safety
property — model proposes, code verifies — in full, because the verification runs against a
**recorded** option chain saved in `tests/chain_fixtures.py` rather than a live one. Nothing
in block A is non-deterministic. Block B is where the agent meets a real ladder, and only
five cases genuinely need that.

## How to run these

Work in a scratch state directory so nothing here touches the host journal:

```bash
cd strike_desk
export STRIKE_DESK_OPENALGO_API_KEY='<the key from /apikey>'
export STRIKE_DESK_STATE_DIR="$PWD/.state-manual"
mkdir -p "$STRIKE_DESK_STATE_DIR"
uv sync

# Block A only: a consistent copy of the host's iteration-03 journal, so MT-05 and MT-06
# run against rows the desk actually wrote under dt-1 and SCHEMA_VERSION 3.
# Use the backup API, never cp: the database is in WAL mode with a live writer.
ssh desk "sudo -u strikedesk sqlite3 /var/lib/strike-desk/strike_desk.db \".backup '/tmp/legacy3.db'\""
scp desk:/tmp/legacy3.db "$STRIKE_DESK_STATE_DIR/legacy3.db"
```

For block B add the reasoning plane and a short cadence:

```bash
export STRIKE_DESK_ANTHROPIC_API_KEY='<key>'
export STRIKE_DESK_TICK_INTERVAL_SECONDS=300
```

Keep three terminals with that environment exported: one running `uv run strike-desk run`,
one for CLI commands, and one on `sqlite3 "$STRIKE_DESK_STATE_DIR/strike_desk.db"`.

## Block A — runs today, with no broker session

| ID | Title | Preconditions | Steps | Expected result | Covers |
| --- | --- | --- | --- | --- | --- |
| **MT-01** | A mutating tool cannot reach a whitelist | Scratch checkout | 1. Add `"place_options_order"` to `STRATEGIST_TOOLS` in `mcp_toolbox.py`. 2. `uv run python -c "import strike_desk.mcp_toolbox"`. 3. Try `"cancelorder"`, then `"close_all_positions"`. 4. Revert. | Each import raises `ValueError: whitelisted tools must be read-only; these are not: …` naming the tool. The module cannot be imported at all, so the service cannot start with it. The guardrail is import-time, not call-time. | AC-5 |
| **MT-02** | The strategist is bound to exactly seven read-only tools | Scratch checkout | 1. `uv run python -c "from strike_desk.mcp_toolbox import STRATEGIST_TOOLS as t; print(len(t)); print('\n'.join(sorted(t)))"`. 2. Confirm against the list in `01_use_case.md` §1. 3. `uv run pytest tests/test_options_strategist.py -k whitelist -q`. | Seven names, all `get_*`: `get_expiry_dates`, `get_momentum_snapshot`, `get_option_chain`, `get_option_greeks`, `get_option_symbol`, `get_quote`, `get_trend_snapshot`. The suite's whitelist test asserts the *bound* list the model actually sees contains no `place_*`, `modify_*`, `cancel_*`, `close_*` or `square_*` name, and that a call to one is answered with an error string and counted as `rejected`. | AC-5 |
| **MT-03** | The playbook rejects each violation by name | Recorded chain fixture | 1. `uv run pytest tests/test_playbook.py -v`. 2. Then by hand: `uv run python` and build a valid proposal from `tests.chain_fixtures.valid_proposal()`; call `playbook.check` and confirm it returns `[]`. 3. Mutate one field at a time — `delta=0.90`, `open_interest=100`, `breakeven` off by 5, `theta_per_day=+18`, `stop_price` above the entry band, `time_stop_ist="16:30"` — and re-check each. | The valid proposal returns an empty list. Each mutation returns exactly one violation whose text names the constraint, the value it got and the band it wanted — e.g. `|delta| 0.900 is outside the 0.35-0.60 band`. No mutation is silently absorbed, and none re-prices the contract. This is the whole model-proposes/code-verifies property, run with no broker. | AC-3 |
| **MT-04** | A proposal cannot cite what it never fetched | Recorded chain fixture | 1. `uv run python`; build an `EvidenceLedger`, record the fixture's chain and Greeks outputs into it. 2. Validate `valid_proposal()` — expect `None`. 3. Change the rationale to cite an IV of `41.7` that appears nowhere in the fixture and re-validate. 4. Restore the rationale but change `strike` to a level the chain never listed and re-validate. 5. Change `symbol` to `NIFTY02SEP2699999CE` and re-validate. | Step 2 returns `None`. Step 3 returns `rationale cites numbers absent from the evidence: 41.7`. Step 4 returns `proposed strike … was never observed in a tool output`. Step 5 returns `proposed symbol … was never returned by a tool`. Grounding binds on the sentence, on the structured numbers and on the symbol — a model cannot cite correctly while inventing the contract underneath. | AC-2 |
| **MT-05** | The taxonomy change is additive, and dt-1 rows still read clean | `legacy3.db` copied | 1. `cp legacy3.db strike_desk.db` in the scratch state dir. 2. `uv run strike-desk declines --since 10`. 3. Read the health block. 4. `uv run strike-desk status`. | Every legacy row still classifies, and the health block reads **`taxonomy drift 0`**. If it reads anything else, an existing entry's category or disposition was changed and the change must be reverted — this is the single check that protects a year of host rows. `status` shows `taxonomy: dt-2+<digest>` with a digest different from `dt-1`'s, because two variants were added. | AC-7 |
| **MT-06** | The journal gains the table in place | The migrated scratch journal | 1. `sqlite3 ... ".tables"` and `"SELECT count(*) FROM decisions;"` — note both. 2. `uv run strike-desk proposals`. 3. Repeat step 1. 4. `sqlite3 ... "UPDATE proposals SET status='x';"` on a seeded row. 5. Run the same report twice. | Before, there are three tables and no `proposals`; after, there are four and the `decisions` count is identical. The new table carries the same append-only triggers — step 4 fails with `proposals is append-only`. The second report prints byte-identical output. No `ALTER TABLE` runs at any point. | AC-8 |
| **MT-07** | New codes cannot ship half-wired, and wording cannot drift | Scratch checkout | 1. `uv run pytest tests/test_decline_taxonomy.py -q`. 2. Delete the `no-viable-contract` entry from `_ENTRIES` and rerun. 3. Restore it; add `REASON_EXPERIMENT = "experiment"` to `graph.py` and rerun. 4. Restore; change one word in the `proposal-invalid` template and rerun, then `uv run strike-desk status`. 5. Revert. | Step 2 fails `test_every_code_the_graph_can_emit_is_classified` naming `no-viable-contract`. Step 3 fails `test_no_taxonomy_entry_is_orphaned` naming `experiment`. Step 4 fails the golden file naming the old and new sentence, and the digest in `status` has moved while `dt-2` has not. The parity gate binds in both directions across the three new codes. | AC-7, AC-12 |
| **MT-08** | Bad arguments are refused before the journal opens | Any state | 1. `uv run strike-desk proposals --day 02-09-2026; echo "exit $?"`. 2. `uv run strike-desk proposals --since 0`. 3. `uv run strike-desk proposals --day 2026-09-02 --since 3`. 4. Delete `strike_desk.db`, then repeat step 1. | Each exits 2 naming what was wrong — a date that is not `YYYY-MM-DD`, a count below one, two mutually exclusive options. Step 4 exits 2 **without** creating the database, which proves the validation runs before the journal is opened. | AC-10 |
| **MT-09** | The text and the JSON are one object | A journal holding proposals (seed with `tests/chain_fixtures.seed_proposals()`) | 1. `uv run strike-desk proposals --json > a.json`. 2. `uv run strike-desk proposals > a.txt`. 3. For each row, compare symbol, entry band, delta, IV, OI, breakeven, stop, target, time-stop, verdict, violations and cost between the two. 4. `python -c "import json;d=json.load(open('a.json'));print(d['taxonomy'],d['playbook'])"`. | Every field agrees, because the text is a formatting of the same dict the JSON prints. The JSON carries both artifacts — `dt-2+<digest>` and `pb-1+<digest>`. There is no number in the text that is not in the JSON. | AC-10, AC-12 |
| **MT-10** | A budget that cannot hold two specialists refuses to start | Any state | 1. `STRIKE_DESK_TICK_BUDGET_SECONDS=40 uv run strike-desk status`. 2. `STRIKE_DESK_STRATEGIST_TIMEOUT_SECONDS=70 uv run strike-desk status`. 3. `STRIKE_DESK_PLAYBOOK_DELTA_MIN=0.7 uv run strike-desk status`. 4. Unset all three. | Steps 1 and 2 fail at settings construction with `specialist timeouts total 60s, which does not fit inside the 40s tick budget` — the arithmetic is refused at startup rather than surfacing later as a mystery `tick-timeout` decline. Step 3 fails with `playbook_delta_min must be below playbook_delta_max`. A misconfigured desk does not run. | §11 invariant |
| **MT-11** | `enter` is unreachable in this slice | Scratch checkout | 1. `grep -rn "OUTCOME_ENTER" src/strike_desk/` . 2. `uv run pytest tests/test_tick_proposals.py -k enter -q`. 3. `sqlite3 ... "SELECT DISTINCT outcome FROM decisions;"` on the migrated journal after any run. | `OUTCOME_ENTER` is defined and exported but returned by no branch of `_decide_outcome`. The suite's `test_enter_is_unreachable` drives a passing proposal through the graph and asserts the outcome is `decline` with reason `specialist-unavailable`. The journal holds only `decline` and `hold`. The absence of an entry is asserted, not merely observed. | AC-11 |

## Block B — needs a reconnected broker and an open market

Do not start these until `curl` against OpenAlgo's `/api/v1/quotes` returns a NIFTY quote.
If it still answers `Incorrect api_key or access_token`, every case here will report
`data-quality / chain` and prove nothing.

| ID | Title | Preconditions | Steps | Expected result | Covers |
| --- | --- | --- | --- | --- | --- |
| **MT-12** | A live proposal, end to end | Service running, market open, flat book, a trending session | 1. `uv run strike-desk tick-now`. 2. `uv run strike-desk proposals`. 3. `sqlite3 ... "SELECT status, symbol, playbook_verdict, tool_call_count, rejected_tool_count FROM proposals ORDER BY id DESC LIMIT 1;"` | One `proposals` row with status `proposed` and verdict `pass`. The rendering names index, expiry, strike, CE/PE, symbol, lots, lot size, entry band, delta, theta, IV, OI, breakeven, stop, target and time-stop — every field AC-1 requires. `rejected_tool_count` is 0. The rationale reads as a paragraph a trader would recognise, and every number in it appears in the evidence. If the session is not trending, wait for one rather than forcing it. | AC-1, AC-2, AC-3 |
| **MT-13** | A passing proposal still declines | The tick from MT-12 | 1. `sqlite3 ... "SELECT outcome, reason_code, reason_text FROM decisions WHERE tick_id = (SELECT tick_id FROM proposals ORDER BY id DESC LIMIT 1);"` | The outcome is `decline`, the code is `specialist-unavailable`, and the sentence is the `no_risk` variant naming the proposed symbol, the entry price and the missing `risk` role. The desk found a contract, verified it, journalled it — and did not trade. This is the slice's headline behaviour and the reason UC-05 is next. | AC-11 |
| **MT-14** | A refusal is a first-class answer | Market open; a session with wide spreads or thin OI — late afternoon on a far expiry is the easiest to find | 1. Tighten the playbook to force it: `STRIKE_DESK_PLAYBOOK_MAX_SPREAD_PCT=0.05`, restart. 2. `uv run strike-desk tick-now`. 3. `uv run strike-desk proposals`. 4. `uv run strike-desk declines; echo "exit $?"`. 5. Restore `1.5` and restart. | The row has status `no-contract`, verdict `n/a`, a reason naming the spread constraint, and evidence behind it. The decision is `decline` / `no-viable-contract` / `contract` / **`routine`**, and `declines` **exits 0** — a refusal is not a defect and must never make the day look broken. Confirm the exit code specifically; it is the easiest thing in the slice to get wrong. | AC-4 |
| **MT-15** | A non-directional regime spends nothing | Market open; a range-bound session, or force it | 1. Set `STRIKE_DESK_DIRECTIONAL_REGIMES=high-volatility` — a tradeable-set label the analyst will not return today — restart, and run one tick on a `trending` read. 2. `sqlite3 ... "SELECT count(*) FROM proposals WHERE tick_id='<that tick>';"` 3. Read the decision row. 4. Restore `trending`. | Zero `proposals` rows: the router never entered `propose`, so no Sonnet call and no chain read happened. The decision is `decline` / `no-viable-contract` with the `non_directional` sentence naming the regime. The tick's `token_cost_micros` reflects the regime read alone. Gating before spending is the cost shape the slice depends on. | AC-6 |
| **MT-16** | An unreadable chain declines rather than guesses | Service running | 1. Stop OpenAlgo mid-session. 2. `uv run strike-desk tick-now`. 3. Restart OpenAlgo. 4. Read the newest `proposals` and `decisions` rows. | The regime read fails first, so the tick declines at `data-quality` / `regime` before the strategist is reached — that is correct and expected. To exercise the chain path specifically, instead leave OpenAlgo up and revoke only the broker session: the regime read may still succeed from cached indicators while the chain call fails, giving a `degraded` proposal row and a `data-quality` / `chain` decision. Either way the desk declines and never proposes a contract from a chain it could not see. | E4 |

## Coverage

| AC | Covered by | Block |
| --- | --- | --- |
| AC-1 proposal completeness | MT-09, MT-12 | A + B |
| AC-2 grounding | MT-04, MT-12 | A + B |
| AC-3 playbook verification | MT-03, MT-12 | A + B |
| AC-4 refusal is first-class | MT-14 | B |
| AC-5 read-only whitelist | MT-01, MT-02 | A |
| AC-6 gated consultation | MT-15 | B |
| AC-7 additive taxonomy | MT-05, MT-07 | A |
| AC-8 in-place migration | MT-06 | A |
| AC-9 spans | MT-12 (and `tests/test_options_strategist.py`) | B |
| AC-10 the command | MT-08, MT-09 | A |
| AC-11 no `enter` | MT-11, MT-13 | A + B |
| AC-12 versioned artifacts | MT-07, MT-09 | A |

Nine of twelve acceptance criteria are fully or partly provable **before the broker comes
back**. AC-4, AC-6 and AC-9 are the three that genuinely need a live session; AC-1, AC-2 and
AC-3 have off-hours proofs against the recorded fixture and live confirmations in block B.

## After a full pass

Restore the playbook bands, the tick interval, `STRIKE_DESK_DIRECTIONAL_REGIMES` and any
source you edited. Delete the scratch state with `rm -rf "$STRIKE_DESK_STATE_DIR"` — MT-06
deliberately attempts a write the triggers refuse, and MT-09 seeds rows an append-only
journal cannot clean up any other way.

One judgement call is worth recording rather than assuming: if MT-12 never produces a
`proposed` row across several trending sessions because the playbook's bands are too tight
for the current chain, that is **information about the bands, not a failed test**. Note which
constraint bound most often and raise it deliberately with a version bump — do not loosen a
band mid-test to make a case pass.
