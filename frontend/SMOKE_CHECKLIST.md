# Manual smoke checklist

- [ ] Old analysis JSON without new fields renders.
- [ ] First load shows the consent modal; initial focus is on confirm, Tab stays in the dialog, Escape does not dismiss it, confirm closes it, and focus is restored.
- [ ] Confirming stores consent; subsequent loads do not show the modal. With localStorage blocked, the page still works and the modal appears on each load.
- [ ] Disclaimer footer and TradingView attribution link appear on index.html, dashboard.html, detail.html, and strategy.html.
- [ ] At mobile width, the modal and footer fit without horizontal scrolling.
- [ ] `strategy.html` shows current byte/file usage and limits, and its empty state is clear when no notes exist.
- [ ] Upload accepts UTF-8 `.md`/`.txt`, rejects a file over the server-reported single-file/total limits before sending, rejects non-UTF-8 bytes without a PUT, and surfaces backend 400/413 details.
- [ ] Paste-save preflights UTF-8 byte size against the server-reported single-file and total limits; view, confirmed delete, and refresh work; filenames and note contents containing `<script>`, quotes, and `&` render only as text.
- [ ] Overlap two strategy file-list refreshes with delayed responses; only the newest response updates usage and the list. After deleting a note, keyboard focus moves to the saved-notes heading.
- [ ] Detail strategy panel renders a saved result on load with timestamp, engine, holding-lot state, collapsed warnings, all four badge states, verbatim quote/context, evidence, and only non-empty scenario advice.
- [ ] Strategy panel escaping: rule/source/quote/context/evidence/advice/warnings/API errors containing HTML render as text and never execute markup.
- [ ] Running a check polls about every 3 seconds, stops on done/error/navigation, and restores the button; the fixed disclaimer stays above every result. A result whose ticker differs from the page ticker is treated as no result.
- [ ] Delay the initial status GET until after a newer POST/poll GET completes; the delayed response must not overwrite the newer state.
- [ ] A POST 409 shows the server detail for the other globally running check, keeps the button usable, and does not load or label this ticker's old result as complete; 400 (including no-notes detail) and 404 messages are visible and do not clear the last saved result.
- [ ] During polling, transient GET failures show a connection/retry message and retry after about 3s, 6s, then 12s without extending the original 10-minute deadline.
- [ ] Navigate away/back through BFCache while a check is running; the page becomes active again and resumes/re-fetches status instead of remaining stopped.
- [ ] Strategy page and detail panel fit at mobile width with no horizontal scrolling; all controls are keyboard reachable and focus-visible.
