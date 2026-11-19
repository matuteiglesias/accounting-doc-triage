# Evidence Steward — Codex delegation design (proposal)

Status: **DESIGN ONLY / DISPATCH DISABLED**. This directory is safe to store in a public code repository: it contains no private accounting records, document contents, customer names, exact local data roots, credentials, or real transaction identifiers. Do not put those in prompts, issues, logs, worktrees, or pull requests.

## What this agent owns

An **orchestrator**, not an accountant and not a document adjudicator, maintains the improvement frontier for accounting payment/document evidence:

1. Read the existing code contracts, test failures, review-queue *aggregates*, PR state and the persisted backlog.
2. Select **at most one** well-scoped engineering task with a measurable missing capability.
3. Assemble its prompt from `CODEX_TASK_PROMPT.md` and the corresponding entry in `tasks.json`.
4. Delegate to a Codex worker **only through a configured, authorized dispatch adapter**.
5. Check its synthetic tests, provenance/safety behavior and diff. Ask for a small fix or produce one reviewable draft PR.
6. Record what changed, what is still blocked, and the next smallest useful action.
7. Stop if there is no defensible next action. **No task churn to maintain a schedule.**

It may *recommend* private evidence-review campaigns, but may not run them on real data or approve payments without an explicit operating packet and a human-controlled review gate.

## Existing system boundaries (do not rebuild these)

| Repository | Authority | Orchestrator may request |
| --- | --- | --- |
| `accounting-doc-triage` | Custody/identity, document interpretation, proposed matching, review frontier and evidence artifact contract | Synthetic fixture-safe parsers, review queue diagnostics, source adapters behind an allowlist, regression tests |
| `accounting-workflows` | Canonical ledger, `acct.transaction-evidence@1` consumer, reproducible report/snapshot contracts | One separately scoped consumer compatibility test or renderer change, with accounting-owner review |
| `accounting-viewer` | Read-only approved-report discovery/navigation | A separately scoped editorial/navigation task **after** the approved report bundle exists; never compute evidence coverage here |

The historic `scripts/run_evidence_campaign.py` is a bounded operational adapter, not permission to scan private data. The `src/accounting_doc_triage/` contracts and `AGENTS.md` remain authoritative. Reconcile overlapping adapters rather than creating a second matching engine.

**Evidence status is not transaction status:** `approved`, `candidate`, `rejected`, `missing proof` describe documentary support, never whether the financial transaction occurred. A bill is `liability_source`, not automatically `payment_proof`; a statement is usually `statement_context`. Document identity is content hash, not filename.

## Loop: sense → choose → delegate → verify → learn

| Gate | Input | Decision | Output |
| --- | --- | --- | --- |
| SENSE | Repo state, tests, existing issue/PRs, pinned **redacted aggregate** of review frontier | Is there a real bottleneck, regression, stale interface or useful new permitted payer? | Delta-only signal. If none, `NO_OP`. |
| CHOOSE | `tasks.json` and unresolved review | Is one task ready, unblocked, within proper repo, nonduplicative, and cheaper than another? | One task ID, baseline, acceptance conditions; otherwise `HOLD`. |
| DELEGATE | `CODEX_TASK_PROMPT.md` + task entry | Is dispatch enabled and the selected runner credentialed/isolated? | Codex thread/run link and worktree/branch; otherwise a **prepared handoff**, not a claimed dispatch. |
| VERIFY | Diff, tests, artifact schema, privacy audit, baseline metrics | Did it improve a verified need without changing accounting meaning or leaking evidence? | Draft PR or `NEEDS_REVISION` with exact failures. |
| RECONCILE | PR review, review decisions, new actual coverage snapshots | Did the fix reduce ambiguity, rework or human-review time? | Updated task state and next trigger; never fabricated approvals. |
| RELEASE | Human-reviewed code/authorizations and approved pinned evidence snapshot | Does release policy permit this exact document population/links? | Only the designated owner publishes. No automatic release. |

**WIP limit:** one Codex engineering task **and** one PR under review; no next dispatch while an earlier PR remains awaiting review. The orchestrator itself can collect read-only signals but must not spawn speculative implementation agents. Proposed pilot cadence: two sensing passes/week, maximum one delegation/week; reduce if no new evidence. This is a design default, not an enabled schedule.

## Persistent state

`tasks.json` is the **definition and planning queue**, not an execution-history database. It must include `id`, `state`, `repo`, `trigger`, `goal`, `files_to_inspect`, `allowed_changes`, `acceptance`, `blocked_by`, and `risk`.

Allowed task states: `READY_FOR_REVIEW`, `HOLD`, `DISPATCHED`, `PR_OPEN`, `DONE`, `BLOCKED`, `CANCELED`. A state transition to `DISPATCHED` is valid **only if a real Codex run ID exists**. A state transition to `DONE` requires linked evidence from tests and an accepted PR or explicitly closed no-code issue; `DONE` does not imply published evidence.

Operational runtime state (last sensed baseline, Codex run IDs, failures, PR URLs and counts) belongs in a private approved tracker or appropriately permissioned GitHub issues, **not** in this public queue when real documents or identifiable records are involved. Keep the queue versioned and small; completed items can be archived.

## Agent strategy and capability ladder

**Stage 0 — now:** human or scheduled orchestrator reads the persisted prompts and prepares **one Codex-ready coding task**. No SDK installation, agent session, private scan or dispatch is implied by this branch.

**Stage 1 — bounded coding pilot:** human approves a specific task/runner; a Codex worker operates in an isolated worktree/branch on code + **synthetic fixtures only**, emits a draft PR and test report. One task at a time.

**Stage 2 — approval-gated evidence sensing:** a separate explicitly authorized private worker searches only allowlisted data roots, email exports or document sources; logs aggregate coverage and generates *candidates*, never evidence approvals. It does not publish raw findings to public GitHub.

**Stage 3 — ongoing stewardship:** reopen tasks only on a measured regression, a new permitted source/payer, changed evidence contract or a genuine human-review bottleneck; do not pursue improving a score for its own sake.

**Codex runner choice:** use the documented Codex SDK to run local tasks in isolated sandboxes/worktrees when authenticated and installed, or the OpenAI Agents API for managed Codex sessions if its permissions/costs/data residency are appropriate. These are future integration options; **no dispatch adapter is implemented or activated here**. GitHub issue creation by itself is not proof Codex ran.

## Authority / privacy / stop conditions

- Public code repository, private source data. **Never** paste transaction rows, PDFs, receipts, addresses, people, account IDs, local source paths, email bodies, or private coverage extracts into Codex prompts/PRs.
- No `APPLY=1`, no moving/deleting real documents, no live ingestion, no `run_evidence_campaign` on actual files, no document uploads to remote models, no external account search without exact authorization.
- No alteration of paid/canonical ledger, payer/Box semantics, debt or legally owed amounts, evidence approval, historical status or `latest` publication from this queue.
- New payer support requires an explicit **allowlisted scope**, controlled status/denominator definition and separate approval before any actual data execution. Do not rewrite the original PM/MI cohort in place.
- Never promote a candidate solely on amount/date match. Do not infer property occupancy or legal payer from who paid a bill.
- Deduplicate by evidence SHA and relation identity, preserve refusals and review provenance, and avoid adding a new storage engine.
- If inputs are not authorized, a source is ambiguous, findings conflict, QA regresses, private data would leak, or old PRs are unreviewed: **HOLD and report the blocker**.
- All report/Viewer publication is a separate explicit human gate.

## Weekly report contract (6 lines max)

`FRONTIER` changed / unchanged and source cutoff (aggregate only);
`WIP` current task/PR or none;
`DELIVERED` concrete patch/tests or none;
`BLOCKER` human evidence/authority/permission needed;
`NEXT` one action or NO_OP;
`PRIVACY` confirmation no private data copied / scans performed / deployment.

Read `ORCHESTRATOR_PROMPT.md`, `CODEX_TASK_PROMPT.md` and `tasks.json` before operating. Any change to dispatch policies requires a separate reviewed task.
