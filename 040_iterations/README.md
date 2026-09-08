# 040_iterations — the build, one use case at a time

This directory holds the incremental build of the Strike Desk agent system. Each
sub-directory is **one iteration**, and each iteration delivers exactly **one use
case** from the catalog in [`../030_design/01_use_cases.md`](../030_design/01_use_cases.md),
in the MVP priority order that catalog defines.

Iterations are cumulative: iteration `N` assumes everything iterations `1..N-1`
built is in place, and its use-case document opens with a *Why this slice* note
explaining what the previous iteration left open and how this one closes it.

## Iterations

| # | Use case | What it adds |
| --- | --- | --- |
| [01](iteration-01/01_use_case.md) | UC-01 — Run a decision tick | The Supervisor loop: a cadence, market-hours gating, fail-closed behaviour, and a journal entry per tick. Everything else plugs into this. |
| [02](iteration-02/01_use_case.md) | UC-02 — Read the regime | The Regime Analyst fills the `regime` role in the specialist registry, so a tick can form a market read instead of declining for want of a specialist. |
| [03](iteration-03/01_use_case.md) | UC-03 — Decline the marginal trade | Refusals become first-class: typed, counted and journalled, rather than free-text sentences assembled inline. |
| [04](iteration-04/01_use_case.md) | UC-04 — Propose a directional long | The Options Strategist fills the `strategist` role and turns a tradeable regime read into a concrete, verified contract. |
| [05](iteration-05/01_use_case.md) | UC-05 — Adjudicate against hard risk limits | The Risk Officer clears or blocks a proposed contract against hard limits before it can become an intent. |
| [06](iteration-06/01_use_case.md) | UC-06 — Place a live order behind human approval | A cleared `enter` intent reaches the broker — but only after a human approves it. |

## What is inside an iteration

Every iteration folder carries the same five documents plus its diagrams:

| File | Purpose |
| --- | --- |
| `01_use_case.md` | The slice itself — actor, preconditions, dependencies, main and alternate flows, acceptance criteria. |
| `02_implementation_guide.md` | How to build it: modules, data shapes, code-level decisions. |
| `03_manual_test_cases.md` | Step-by-step checks a person runs by hand. |
| `04_test_automation.md` | The automated suite for the slice. |
| `05_deployment_guide.md` | How to deploy and run what this iteration adds. |
| `images/` | Infographics and diagrams embedded by the documents above. |

Iteration 01 additionally has `05_deployment_guide_gcp.md` for the GCP path.

## Related directories

- [`../020_proposal/`](../020_proposal/) — the engagement proposal.
- [`../030_design/`](../030_design/) — use-case catalog, PRD, architecture and tech stack. Read the catalog first; it is the source of the UC numbers used here.
- [`../000_client_data/`](../000_client_data/) — client-supplied inputs.
