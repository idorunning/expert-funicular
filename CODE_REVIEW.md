# Code Review (2026-04-24)

This review focused on runtime correctness, crash risks, and behavior mismatches between UI and service logic.

## High-severity functional issues

1. **"Disregard" is effectively blocked by category validation.**
   - `CategoryGridPicker` emits `"Transfer/Excluded"` from the inline picker.
   - `TransactionsModel.setData()` rejects any category not in `user_visible_categories()`.
   - `user_visible_categories()` explicitly excludes internal categories such as `"Transfer/Excluded"`.
   - Result: picker-level disregard cannot persist, and the UI appears to accept an action that model logic rejects.

2. **Bulk disregard is also blocked, so the "Disregard selected" flow can no-op.**
   - `disregard_rows()` passes `"Transfer/Excluded"` into `bulk_apply_category()`.
   - `bulk_apply_category()` uses the same `user_visible_categories()` gate.
   - Result: rows selected for disregard can be silently skipped (`updated == 0`), so broker intent is not applied.

## Medium-severity correctness issue

3. **Undo disregard contains dead/contradictory logic for empty prior categories.**
   - `undo_last_disregard()` computes `prev_category` and comments that an empty previous category should keep current.
   - But DB write uses `prev_state["category"]` directly (not `prev_category`), so the computed fallback is ignored.
   - Result: behavior does not match the method comment; maintenance risk and possible unexpected category clearing.

## Medium-severity crash/integration risk

4. **Missing `QPoint` import in category grid picker type annotation.**
   - `show_at(self, preferred_top_left: "QPoint")` references `QPoint` but it is not imported.
   - While postponed annotations prevent immediate import-time failure, tools that evaluate annotations (`typing.get_type_hints`, docs/introspection tooling, some IDE/runtime plugins) can raise `NameError`.

## Engineering quality risks (could hide future bugs)

5. **Large number of static-analysis findings in core paths.**
   - `ruff check .` reports 91 issues, including undefined names, multiple style/safety warnings, and several import/order issues.
   - Not all are runtime bugs, but the current signal-to-noise level will make it harder to catch real regressions.

## Commands run

- `pytest -q` *(failed initially because local package import path not configured in this environment)*
- `PYTHONPATH=. pytest -q` *(132 passed, 1 skipped)*
- `PYTHONPATH=. ruff check .` *(91 findings)*

## Pipeline assessment against requested flow

Requested flow described:
1) OCR statements
2) Check known merchant/category memory (fuzzy)
3) LLM on remaining
4) Web-search unresolved merchants (merchant-only)
5) Total categories

Current implementation status:
- **Partially matches, with important differences:**
  - OCR is **conditional**, not universal: PDFs are parsed as text first and only routed to OCR when text density is low.
  - Merchant memory is checked first (exact then fuzzy), and LLM runs when rules are insufficient.
  - Web lookup is **opt-in** and only used as a conditional LLM retry when confidence is low/generic; it does not replace LLM with a pure web-only classifier.
  - Privacy intent is implemented: only a sanitised merchant string is sent for web lookup.
  - Category totals are computed from stored transactions; excluded/uncategorised rows are kept out of committed/discretionary totals.

Amendments recommended:
- Fix the disregard-path validation mismatch (`Transfer/Excluded` currently blocked by `user_visible_categories()` checks), because this can distort totals and operator workflows.
- Optionally add explicit telemetry counters to make the real path per row transparent (rule hit, llm, web-assisted, OCR used).
