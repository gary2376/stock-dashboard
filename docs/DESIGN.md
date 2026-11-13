# Design decisions

Why the system is built the way it is. Each entry: the problem, the decision, the alternative rejected, and what it costs.

## 1. Code decides what it can; the model only does what code cannot
LLMs are unreliable at comparisons and arithmetic (this project saw it repeatedly). Technical and market scores, score caps,
pinned-rule verdicts and every number shown as evidence are computed by code. The model is used for the one thing code cannot
do: choosing which of the user's note rules apply to the stock now and phrasing the comparison.
*Rejected:* letting the model produce scores and explain them — explanations drifted from the numbers.
*Cost:* more code, rules that a human has to maintain.

## 2. Verify what can be verified, and never claim more
A strategy-check row is kept only if its quote exists verbatim and **uniquely** across all notes (NFKC, punctuation and negation
preserved, minimum information length), its fact references exist in a registry, and its evidence text is regenerated from that
registry. Pass/fail itself stays the model's judgement and is labelled so. Quotes that merely exist are not "proof the rule is met".
*Rejected:* stripping punctuation before matching (glues neighbouring cells into sentences that never existed).

## 3. Point-in-time facts for past actions
Judging a past buy with today's data is hindsight. Each buy gets `BUY<n>.*` facts computed from bars strictly **before** the buy
date; rows scoped "entry" may cite only those; code removes foreign references and downgrades verdicts without evidence.
Fundamentals, news and market state at that time are not reconstructable, so they are marked unavailable instead of invented.

## 4. Pinned rules: compile once, confirm, then evaluate deterministically
Users type free text. A local template (strict about < vs ≤) or a one-off LLM compile turns it into a tiny expression language
(no `eval`, comparisons and and/or only, unit-checked, three-valued logic, limits on size/depth/facts). The restatement shown for
confirmation is **generated from the AST**, never from the model, so a manipulated compile cannot show a reassuring but wrong
explanation. Stale detection covers text edits, registry changes and compiler version. IDs never change, so batch columns stay stable.
*Rejected:* running the LLM on every pin every time (cost, drift, unverifiable) and a plugin/DSL system for users.

## 5. Safe defaults for a tool that sees holdings
Localhost by default, no auth but an origin check, atomic writes, per-ticker locks, transactional import with automatic backup,
size/rate limits, a consent dialog before data leaves the machine, an LLM CLI invoked without tools/plugins/MCP.
Honest limits are documented in SECURITY.md rather than implied away.

## 6. Cost awareness
`claude -p` loads the user's global config by default (about 66k input tokens for "hi"); the app disables it (about 2.7k). Every call is
logged with tokens and cost. Measured: ≈ $0.15 per analysis, ≈ $0.13 per strategy check (API-equivalent).

## 7. Evidence discipline
Scores have **no backtest evidence** of predictive power. The technical axis was backtested point-in-time on cached history
(`scripts/backtest_technical.py`; leave-one-out same-market excess return); no significant effect. The UI and README say so.
Analysis snapshots are stored immutably so the LLM-judged axes can be evaluated forward over time.

## How it was reviewed
Backend and frontend changes were red-teamed by a second model in several rounds (race conditions, injection, lookahead,
unit mixing, stale state), with findings fixed and regression-tested; UI behaviour was verified in a real browser (Playwright).
The test suite (400+ tests) runs offline.
