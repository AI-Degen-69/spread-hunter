# SPEC — One Performance & Analytics page (#749)

## Goal

The dashboard serves two competing analytics pages ("Reports & Analytics" and
"Data Analysis") that render the same statistics twice, in two designs. This
change retires the Reports page and makes the surviving page the single
analytics surface, labelled **Performance & Analytics**.

## In scope

1. `reports` leaves `PAGES` and `PAGE_LAYOUT`; the `analysis` entry is relabelled
   **Performance & Analytics** (rail label and hidden tab-shell label agree).
2. The two legacy cards that are visibly better than their canonical twins move
   into `#analysis-v2`, visuals unchanged: **Average Close & Confidence**
   (`renderPnlCiReadout`) and **Execution Drop-off** (`renderExecutionFunnel`).
3. **STOP-EXIT SLIPPAGE** (`renderStopSlippage`) moves onto the canonical page.
4. The remaining legacy analytics cards and the `.stats-subnav` filter strip are
   deleted. Statistics that already exist on the canonical page keep the one
   rendering that page already has.
5. `#analytics-gates` keeps a home in the document (the Strategy page claims it);
   `#broker-portfolio-overview` stays where it is (Home claims it).

## Out of scope

- A page-wide visual restyle of the canonical cards.
- Any new statistic, KPI payload field, or `KPI_PAYLOAD_VERSION` change.
- Trading loop, registry, venue paths, market selection, sizing, risk.
- Deleting the now-unreachable legacy renderer *functions* in `app.js`
  (follow-up issue; this change deletes the markup and the wiring only).

## Acceptance

- The rail shows exactly one analytics page, labelled Performance & Analytics.
- The canonical page renders the band CI card (90 % / 95 % rows, loss banner in
  its negative state), the four-stage funnel with carry-on/lost arrows, WORST
  DROP and MERGED badges plus the refused/dropped chips, and the STOP-EXIT
  SLIPPAGE card.
- No statistic is rendered twice in the served page; every stat-card title names
  its unit (`test_541` 2a/2b stay green).
- The Strategy page still shows `#analytics-gates`.
