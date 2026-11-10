# Manual smoke checklist

- [ ] Old analysis JSON without new fields renders.
- [ ] First load shows the consent modal; initial focus is on confirm, Tab stays in the dialog, Escape does not dismiss it, confirm closes it, and focus is restored.
- [ ] Confirming stores consent; subsequent loads do not show the modal. With localStorage blocked, the page still works and the modal appears on each load.
- [ ] Disclaimer footer and TradingView attribution link appear on index.html, dashboard.html, and detail.html.
- [ ] At mobile width, the modal and footer fit without horizontal scrolling.
- [ ] Strategy panel escaping: n/a yet.
