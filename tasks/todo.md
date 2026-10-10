# Todo - Issue 475 (Branch: i475/rework-control-center-menu-navigation-and-retire-depth-trial)

- [x] 1.1 [S] [Menu/Tests] Retire the depth-bar trial launcher — delete Start-ShadowTrial, t row/branch, aliases, allow-list t; add launcher-is-gone tests; update resume tuple + architecture.md
- [ ] 1.2 [M] [Menu/Tests] One key set everywhere — $script:MenuKeys (1-9,r,a,p,q); grid rows for r/a/p/q; a/p branches + aliases; header; test_menu_navigation.py text + pwsh dispatch
- [ ] 1.3 [M] [Menu/Tests] Interactive menu loop — Invoke-InteractiveMenu; Read-MenuChoice Enter/EOF; per-pass input reset; error-safe loop; pwsh loop tests

Checkpoints: after 1.1 (trial gone, resume intact) / after 1.2 (one key set, a/p work) /
after 1.3 (loop + CLI single-shot green). All done → /iii-build-plan.

