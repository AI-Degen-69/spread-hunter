# Todo — #558

- [x] T1: build the leaf module `core_brain/close_methods.py` (contract in tasks/plan.md)
- [x] T2: write the guard `tests/test_close_method_taxonomy.py` (scanner + self-checks + pins)
- [x] T3: red proof — capture the scan's failure list, pins green, list recorded for #559/#560
- [x] IV. Review, Verify & PR: multi-axis review clean (OCR/python/security/silent-failure/tdd/docs/spec — zero findings) → pushed, PR #578 open, @coderabbitai review triggered
- [ ] V. Babysit PR & Merge: countdown → triage comments → targeted checks → merge (guard RED by design; owner override at merge)

# Todo — #559 (consumers import the taxonomy)

- [x] T1: leaf widened — `TAKER_RESOLVED_METHODS` added to `close_methods.py` (#558, e783205)
- [x] T2: core_brain consumers import the leaf — kpi, statistical_analytics, ladder, registry_state, market_resolution, order_registry, paired_shadow, shadow_exec, single_buy_saver, run_scorer (guard 36 → 11)
- [x] IV. Review, Verify & PR: guard 13 passed / 1 red-by-design; 730 regression green; PR #581 open (stacked on #578), @coderabbitai review triggered
- [ ] V. Babysit PR & Merge: guard red until #560; stack merges after #560 lands
