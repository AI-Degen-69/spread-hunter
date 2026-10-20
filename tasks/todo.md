# Checklist — #707 Validate BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID range

Branch: `i707/validate-max-spread-from-mid-range` | Issue: #707

- [x] T1 engine loader: both reads validate 1..99 cents, name the winning variable
- [x] T2 scoring loader: same rule, pinned strict, unpinned tolerant of non-numbers only
- [x] T3 `.env.example` range + error note; #690 ledger O3 -> fixed #707
- [x] T4 tests: boundaries, rejections, alias naming, per-path typo pin; two #690 tests repinned
- [x] Focused suites green; full suite green at build time
- [ ] PR opened, one CodeRabbit review round, CI green, merged
- [ ] Closeout: clean tree on `main`, issue closed
