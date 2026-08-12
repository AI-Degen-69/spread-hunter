"""HTML/CSS/JS templates for server/spread_dash.py.

Split from the route file only because the templates are long, not for any
architectural reason. Design tokens (palette, type, spacing, square corners)
are migrated from `spread-hunter/src/pages/{Landing,Dashboard}.tsx` and
`spread-hunter/src/index.css`; all numbers are filled in at runtime from the
`/api/*` endpoints in `server/spread_dash.py` -- nothing here is the
mockup's placeholder data.
"""
from __future__ import annotations

_HEAD = """
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap">
<script src="https://cdn.tailwindcss.com"></script>
<style>
  :root{color-scheme:dark;}
  body{background:#090D16;color:#F9FAFB;font-family:Inter,"Helvetica Neue",Helvetica,Arial,sans-serif;
       -webkit-font-smoothing:antialiased;letter-spacing:-0.01em;}
  .font-display{font-family:Inter,"Helvetica Neue",Helvetica,Arial,sans-serif;letter-spacing:-0.03em;font-weight:800;}
  .mono{font-family:"JetBrains Mono","SF Mono",ui-monospace,monospace;font-variant-numeric:tabular-nums;}
  ::selection{background:#10B981;color:#F9FAFB;}
  .sh-fade{transition:opacity .2s ease,transform .2s ease;}
  .sh-collapsed{display:none;}
  .sh-chev{transition:transform .2s ease;}
  .sh-open .sh-chev{transform:rotate(180deg);}
  ::-webkit-scrollbar{height:8px;width:8px;}
  ::-webkit-scrollbar-thumb{background:#1F2937;}
</style>
"""


def _wrap(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head>
<title>{title}</title>
{_HEAD}
</head>
<body class="bg-[#090D16] text-[#F9FAFB] min-h-screen">
{body}
</body></html>"""


LANDING_HTML = _wrap("Spread Hunter -- maker fleet", r"""
<div class="h-[2px] w-full bg-[#10B981]"></div>
<nav class="sticky top-0 z-40 bg-[#111827]/95 backdrop-blur border-b border-[#1F2937]">
  <div class="mx-auto max-w-[1440px] px-6 lg:px-10 h-[72px] flex items-center justify-between">
    <div class="flex items-center gap-5 min-w-0">
      <div class="flex items-center gap-3 shrink-0">
        <div class="size-[40px] bg-[#F9FAFB] text-[#090D16] grid place-items-center mono text-[13px] font-bold tracking-widest">SH<span class="text-[#EF4444]">&mdash;</span>01</div>
        <div>
          <div class="font-display text-[15px] leading-none tracking-[-0.02em]">SPREAD HUNTER</div>
          <div class="mono text-[13px] tracking-[0.14em] uppercase text-[#9CA3AF] mt-0.5">maker fleet &middot; live desk</div>
        </div>
      </div>
      <div class="hidden lg:flex items-center gap-2 ml-6 pl-6 border-l border-[#1F2937] h-8">
        <span class="mono text-[13px] tracking-[0.14em] uppercase text-[#9CA3AF]">Markets that resolve to $1.00</span>
        <span class="size-1 bg-[#10B981] mx-1"></span>
        <span class="mono text-[13px] tracking-[0.14em] uppercase text-[#9CA3AF]">Spread inconsistencies</span>
      </div>
    </div>
    <div class="flex items-center gap-3 shrink-0">
      <div id="nav-status" class="hidden md:flex items-center gap-2.5 mono text-[13px] border border-[#1F2937] bg-[#111827] px-3.5 h-9">
        <span class="size-1.5 bg-[#F59E0B] animate-pulse"></span>
        <span class="tracking-[0.12em] uppercase text-[13px]">Loading&hellip;</span>
      </div>
      <a href="/dashboard" class="bg-[#10B981] text-white px-5 h-9 inline-flex items-center justify-center mono text-[13px] font-semibold tracking-[0.07em] uppercase hover:bg-[#059669] transition-colors shadow-[0_1px_12px_rgba(16,185,129,0.25)]">Open Your Desk &nearr;</a>
    </div>
  </div>
</nav>

<section class="mx-auto max-w-[1440px] px-6 lg:px-10">
  <div class="grid grid-cols-12 gap-0 border-x border-[#1F2937] border-b">
    <div class="col-span-12 lg:col-span-7 border-b lg:border-b-0 lg:border-r border-[#1F2937] p-8 lg:p-12 flex flex-col justify-between min-h-[600px] bg-[#111827]">
      <div>
        <div class="mono text-[14px] tracking-[0.16em] uppercase text-[#3B82F6] flex items-center gap-3"><span class="h-px w-9 bg-[#3B82F6]"></span> Your private desk for spread capture</div>
        <h1 class="font-display text-[46px] lg:text-[64px] leading-[0.92] tracking-[-0.035em] mt-6">
          Find the<br><span class="text-[#10B981]">spread</span> others<br>leave <span class="text-[#EF4444]">behind</span>.
        </h1>
        <p class="mt-6 max-w-[540px] text-[17px] leading-7 text-[#9CA3AF]">
          Spread Hunter identifies pricing inconsistencies in markets that settle at $1.00 and measures whether resting orders can systematically capture them &mdash; after adverse selection and hedging costs. This desk reads the live fleet database directly; nothing here is a mockup number.
        </p>
        <p class="mt-4 max-w-[540px] mono text-[14px] leading-6 text-[#9CA3AF] border-l-2 border-[#1F2937] pl-3">
          Profit and loss, and a clear go / no-go signal &mdash; the evidence you need to decide, not a portfolio dashboard.
        </p>
      </div>

      <div class="mt-10 grid grid-cols-2 gap-0 border border-[#1F2937] bg-[#090D16] overflow-hidden">
        <div class="p-5 border-r border-[#1F2937] bg-[#111827] relative">
          <div class="absolute inset-x-0 top-0 h-[2px] bg-[#10B981]"></div>
          <div class="mono text-[13px] tracking-[0.14em] uppercase text-[#9CA3AF] flex items-center gap-2"><span class="size-1.5 bg-[#10B981]"></span> Realized P&amp;L &mdash; Closed Positions</div>
          <div id="hero-realized" class="mono text-[26px] font-bold tracking-tight leading-none mt-3 text-[#10B981]">&hellip;</div>
          <div id="hero-realized-sub" class="mono text-[13px] text-[#9CA3AF] mt-1">&nbsp;</div>
          <div id="hero-realized-rebate" class="mono text-[12px] text-[#9CA3AF] mt-1">&nbsp;</div>
        </div>
        <div class="p-5 bg-[#111827] relative overflow-hidden">
          <div class="absolute inset-x-0 top-0 h-[2px] bg-[#3B82F6]"></div>
          <div class="mono text-[13px] tracking-[0.14em] uppercase text-[#9CA3AF] flex items-center gap-2">Unrealized P&amp;L <span class="ml-auto px-1.5 py-0.5 bg-[#1F2937] border border-[#3B82F6]/30 text-[#3B82F6] text-[12px] tracking-widest uppercase font-semibold">Separate</span></div>
          <div id="hero-unrealized" class="mono text-[26px] font-bold tracking-tight leading-none mt-3 text-[#3B82F6]">&hellip;</div>
          <div id="hero-unrealized-sub" class="mono text-[13px] text-[#9CA3AF] mt-1">&nbsp;</div>
        </div>
        <div class="col-span-2 border-t border-[#1F2937] px-5 py-3 flex items-center justify-between bg-[#090D16]">
          <span class="mono text-[13px] tracking-[0.12em] uppercase text-[#9CA3AF]">Two independent valuations &mdash; kept separate by design</span>
          <span class="hidden sm:inline mono text-[12px] tracking-widest uppercase text-[#9CA3AF] border border-[#1F2937] bg-[#111827] px-2 py-1">Never combined</span>
        </div>
      </div>

      <div class="flex gap-3 mt-6">
        <a href="/dashboard" class="flex-1 bg-[#10B981] text-white h-11 inline-flex items-center justify-center mono text-[13px] font-semibold tracking-[0.07em] uppercase hover:bg-[#059669] transition-colors shadow-[0_4px_16px_rgba(16,185,129,0.2)]">View Your Dashboard &rarr;</a>
        <a href="#verdict" class="px-6 h-11 inline-flex items-center justify-center border border-[#1F2937] bg-[#111827] mono text-[13px] font-semibold tracking-[0.07em] uppercase hover:bg-[#1F2937] transition-colors text-[#9CA3AF]">How the decision is made</a>
      </div>
    </div>

    <div id="verdict" class="col-span-12 lg:col-span-5 bg-[#090D16] flex flex-col">
      <div class="p-7 lg:p-8 border-b border-[#1F2937] bg-[#111827]">
        <div class="mono text-[13px] tracking-[0.18em] uppercase text-[#9CA3AF]">The Five Readings That Determine the Outcome</div>
        <div class="font-display text-[22px] leading-none mt-2 tracking-tight">Live from the fleet database</div>
        <div class="mono text-[14px] text-[#9CA3AF] mt-2 leading-5">The go / no-go decision rests on the sign of the confidence bound. Everything else provides context.</div>
      </div>
      <div id="verdict-list" class="divide-y divide-[#1F2937]"></div>
      <div class="p-4 bg-[#111827] border-t border-[#1F2937] flex gap-3">
        <div class="mono text-[13px] leading-5 text-[#9CA3AF]">
          <span class="font-bold uppercase tracking-widest text-[#F9FAFB]">Interpret with care:</span> yields and projections are context only and are never added to settled profit. Only settled markets determine the outcome.
        </div>
      </div>
    </div>
  </div>

  <div class="grid grid-cols-12 gap-0 border-x border-[#1F2937] border-b bg-[#111827]">
    <div class="col-span-12 p-6 lg:p-8 flex flex-col lg:flex-row lg:items-end justify-between gap-4 border-b border-[#1F2937] bg-[#090D16]">
      <div>
        <div class="mono text-[13px] tracking-[0.16em] uppercase text-[#9CA3AF]">How Your Desk Is Organized</div>
        <div class="font-display text-[26px] leading-none tracking-tight mt-2">Four layers, arranged to support clear interpretation.</div>
      </div>
      <div class="mono text-[13px] tracking-widest uppercase border border-[#1F2937] px-3 py-1.5 bg-[#111827] shrink-0 text-[#9CA3AF]">Personal &middot; Minimal &middot; Evidence-Driven</div>
    </div>
    <div class="col-span-12 md:col-span-6 lg:col-span-3 border-r last:border-r-0 border-b lg:border-b-0 border-[#1F2937] p-6 flex flex-col gap-4">
      <span class="w-fit mono text-[13px] tracking-[0.14em] uppercase px-2 py-1 bg-[#1F2937] border border-[#1F2937]">01 &middot; Overview</span>
      <div class="font-display text-[18px] leading-none tracking-tight">Two valuations, kept apart</div>
      <div class="text-[14px] leading-6 text-[#9CA3AF]">Realized value from closed positions alongside Unrealized P&amp;L for open exposure &mdash; never combined.</div>
    </div>
    <div class="col-span-12 md:col-span-6 lg:col-span-3 border-r last:border-r-0 border-b lg:border-b-0 border-[#1F2937] p-6 flex flex-col gap-4">
      <span class="w-fit mono text-[13px] tracking-[0.14em] uppercase px-2 py-1 bg-[#1F2937] border border-[#1F2937]">02 &middot; Readiness</span>
      <div class="font-display text-[18px] leading-none tracking-tight">Three threshold gauges</div>
      <div class="text-[14px] leading-6 text-[#9CA3AF]">Capital committed against your limit, markout sample maturity, and progress toward the settlement target.</div>
    </div>
    <div class="col-span-12 md:col-span-6 lg:col-span-3 border-r last:border-r-0 border-b lg:border-b-0 border-[#1F2937] p-6 flex flex-col gap-4">
      <span class="w-fit mono text-[13px] tracking-[0.14em] uppercase px-2 py-1 bg-[#1F2937] border border-[#1F2937]">03 &middot; Evidence</span>
      <div class="font-display text-[18px] leading-none tracking-tight">Performance, readiness, risk, capital</div>
      <div class="text-[14px] leading-6 text-[#9CA3AF]">Each figure notes its source, so you can trace every conclusion back to the database.</div>
    </div>
    <div class="col-span-12 md:col-span-6 lg:col-span-3 p-6 flex flex-col gap-4">
      <span class="w-fit mono text-[13px] tracking-[0.14em] uppercase px-2 py-1 bg-[#1F2937] border border-[#1F2937]">04 &middot; Inspection</span>
      <div class="font-display text-[18px] leading-none tracking-tight">Markets, exits, and selection</div>
      <div class="text-[14px] leading-6 text-[#9CA3AF]">Per-market positions, closed-trade log, and the full selection funnel from scan to settlement.</div>
    </div>
  </div>

  <div class="grid grid-cols-12 gap-0 border-x border-[#1F2937] border-b">
    <div class="col-span-12 lg:col-span-8 bg-[#111827] p-8 lg:p-10">
      <div class="mono text-[14px] tracking-[0.16em] uppercase text-[#9CA3AF]">Design Principles</div>
      <div class="grid grid-cols-2 gap-8 mt-6 mono text-[14px] leading-6">
        <div>
          <div class="font-semibold tracking-[0.12em] uppercase text-[13px]">Typography &amp; Space</div>
          <div class="text-[#9CA3AF] mt-1.5">A strict grid, confident sans-serif hierarchy, and tabular numerals throughout.</div>
        </div>
        <div>
          <div class="font-semibold tracking-[0.12em] uppercase text-[13px]">Palette &amp; Form</div>
          <div class="text-[#9CA3AF] mt-1.5 flex flex-col gap-2"><span class="flex items-center gap-2 flex-wrap"><span class="size-3 bg-[#10B981] inline-block"></span> Green &mdash; gains &amp; thresholds <span class="size-3 bg-[#EF4444] inline-block ml-2"></span> Red &mdash; risk &amp; fails <span class="size-3 bg-[#3B82F6] inline-block ml-2"></span> Blue &mdash; open positions</span><span>Square corners, hairline rules, restrained color.</span></div>
        </div>
      </div>
    </div>
    <div class="col-span-12 lg:col-span-4 bg-[#090D16] p-8 lg:p-10 flex flex-col justify-center border-t lg:border-t-0 lg:border-l border-[#1F2937]">
      <div class="font-display text-[32px] leading-[0.95] tracking-tight">Your desk<br>is ready.</div>
      <div class="mono text-[14px] leading-6 text-[#9CA3AF] mt-3">Review Realized and Unrealized P&amp;L, check your gauges, and inspect market-by-market evidence &mdash; all with clear caveats.</div>
      <a href="/dashboard" class="mt-6 bg-[#10B981] text-white h-11 inline-flex items-center justify-center mono text-[13px] font-semibold tracking-[0.07em] uppercase hover:bg-[#059669] transition-colors shadow-[0_4px_16px_rgba(16,185,129,0.25)]">Enter Private Desk &nearr;</a>
    </div>
  </div>

  <div class="px-2 lg:px-0 py-6 flex flex-col md:flex-row items-center justify-between gap-3 mono text-[13px] tracking-[0.12em] uppercase text-[#9CA3AF]">
    <span>maker fleet &middot; Spread Hunter design</span>
    <span class="flex items-center gap-2"><span class="size-1.5 bg-[#10B981]"></span> Swiss Edition &middot; Live Desk</span>
  </div>
</section>

<script>
function fmtUsd(v){ if(v===null||v===undefined) return "--"; const s=v<0?"-":"+"; return s+"$"+Math.abs(v).toFixed(2); }
function fmtPct(v,d){ if(v===null||v===undefined) return "--"; d=d===undefined?1:d; const s=v>0?"+":""; return s+v.toFixed(d)+"%"; }

async function load(){
  const r = await fetch("/api/summary");
  const s = await r.json();

  document.getElementById("hero-realized").textContent = fmtUsd(s.realized_usd);
  document.getElementById("hero-realized-sub").textContent =
    (s.realized_pct===null?"":fmtPct(s.realized_pct)+" ") + "on " + (s.realized_cost||0).toFixed(0) + " committed";
  document.getElementById("hero-realized-rebate").textContent =
    fmtUsd(s.rebate_usd) + " rebates = " + fmtUsd(s.total_liquidation_usd) + " total liquidation P&L";
  document.getElementById("hero-unrealized").textContent = fmtUsd(s.unrealized_usd);
  document.getElementById("hero-unrealized-sub").textContent =
    s.active_positions + " active position" + (s.active_positions===1?"":"s");

  const nav = document.getElementById("nav-status");
  nav.innerHTML = `<span class="size-1.5 ${s.fleet_alive ? 'bg-[#10B981] animate-pulse' : 'bg-[#F59E0B]'}"></span>
    <span class="tracking-[0.12em] uppercase text-[13px]">${s.fleet_alive ? 'Live' : 'Idle'}</span>
    <span class="text-[#1F2937]">&middot;</span>
    <span class="text-[#9CA3AF] text-[13px]">${s.n_settled} of ${s.go_live_min_settled} settled</span>`;

  const rows = [
    {n:"01", label:"Confidence Bound", value: fmtPct(s.ci90_lower_pct,2),
     sub:`Mean ${fmtPct(s.mean_return_pct)} &middot; Stdev ${s.stdev_return_pct===null?'--':s.stdev_return_pct.toFixed(1)+'%'} &middot; n=${s.n_settled}`,
     accent: (s.ci90_lower_pct||0) > 0 ? "neutral":"red",
     note:"90% lower bound on realized return. Below zero means the sample cannot yet rule out no edge."},
    {n:"02", label:"Markout Drift", value: s.markout_mean_per_share===null?"--":(s.markout_mean_per_share*100).toFixed(2)+"&cent;",
     sub:`Effective sample ${s.markout_n_eff.toFixed(1)} &middot; directly measured`, accent:"blue",
     note:"Price movement against filled orders, pooled fleet-wide."},
    {n:"03", label:"Realized vs Unrealized", value: fmtUsd(s.realized_usd) + " / " + fmtUsd(s.unrealized_usd),
     sub:`${s.wins} wins &middot; ${s.losses} losses`, accent:"neutral",
     note:"Two independent ledgers, never summed."},
    {n:"04", label:"Category Concentration", value: s.categories.length ? (s.categories[0].pct.toFixed(1)+"% "+s.categories[0].name) : "--",
     sub: s.categories.map(c=>c.name+" "+c.n).join(" / ") + ` &middot; Cap ${(s.go_live_max_category_share*100).toFixed(0)}%`,
     accent: (s.max_category_share||0) > s.go_live_max_category_share ? "red":"neutral",
     note:"Concentration above the cap alone prevents go-live."},
    {n:"05", label:"Settled Positions", value: `${s.n_settled} of ${s.go_live_min_settled}`,
     sub:`${s.closes} total closes recorded &middot; status ${s.status}`, accent:"neutral",
     note:`Signal floor is ${s.signal_min_settled} settled markets.`},
  ];
  document.getElementById("verdict-list").innerHTML = rows.map(row => `
    <div class="grid grid-cols-12 gap-0 hover:bg-[#111827] transition-colors">
      <div class="col-span-2 border-r border-[#1F2937] p-4 grid place-items-center bg-[#111827]/50">
        <span class="mono text-[13px] font-bold tracking-widest text-[#9CA3AF]">${row.n}</span>
      </div>
      <div class="col-span-10 p-4 bg-[#111827]">
        <div class="flex items-start justify-between gap-3">
          <div class="mono text-[13px] tracking-[0.14em] uppercase font-semibold">${row.label}</div>
          <div class="mono text-[13px] font-bold tracking-tight px-2 py-1 leading-none shrink-0 border ${row.accent==='red'?'bg-[#EF4444] text-white border-[#EF4444]':row.accent==='blue'?'bg-[#3B82F6] text-white border-[#3B82F6]':'bg-[#1F2937] text-[#F9FAFB] border-[#1F2937]'}">${row.value}</div>
        </div>
        <div class="mono text-[13px] text-[#9CA3AF] mt-1.5">${row.sub}</div>
        <div class="mono text-[13px] leading-5 text-[#9CA3AF] mt-2 border-l-2 pl-2.5" style="border-color:${row.accent==='red'?'#EF4444':row.accent==='blue'?'#3B82F6':'#374151'}">${row.note}</div>
      </div>
    </div>`).join("");
}
load();
</script>
""")

DASHBOARD_HTML = _wrap("Fleet Desk -- Spread Hunter design", r"""
<div class="h-[2px] w-full bg-[#EF4444]"></div>
<header class="sticky top-0 z-30 bg-[#111827]/90 backdrop-blur border-b border-[#1F2937]">
  <div class="mx-auto max-w-[1440px] px-6 lg:px-8 h-[60px] flex items-center justify-between gap-4">
    <div class="flex items-center gap-4 min-w-0">
      <div class="size-9 bg-[#111827] text-[#F9FAFB] grid place-items-center mono text-[12px] font-bold tracking-widest shrink-0 border border-[#1F2937]">SH<span class="text-[#EF4444]">&mdash;</span>01</div>
      <div class="hidden md:block min-w-0">
        <div class="font-display text-[15px] leading-none tracking-tight flex items-center gap-2">SPREAD HUNTER <span class="hidden lg:inline mono text-[12px] tracking-[0.14em] uppercase font-normal text-[#9CA3AF]">Fleet Desk</span></div>
        <div class="mono text-[12px] tracking-[0.12em] uppercase text-[#9CA3AF] truncate">Live fleet database &mdash; maker</div>
      </div>
      <div id="hdr-pills" class="hidden lg:flex items-center gap-1.5 ml-2"></div>
    </div>
    <div class="flex items-center gap-2 shrink-0">
      <div id="hdr-live" class="hidden sm:flex items-center gap-2 mono text-[13px] border border-[#1F2937] bg-[#111827] px-3 h-9">
        <span class="size-1.5 bg-[#9CA3AF]"></span>
        <span class="tracking-[0.12em] uppercase text-[12px]">Loading</span>
      </div>
      <a href="/" class="h-9 px-3.5 border border-[#1F2937] bg-[#111827] mono text-[13px] font-semibold tracking-widest uppercase hover:bg-[#1F2937] transition-colors flex items-center gap-1.5">&larr; Home</a>
    </div>
  </div>
  <div class="bg-[#111827] border-y border-[#1F2937]">
    <div class="mx-auto max-w-[1440px] px-6 lg:px-8 min-h-9 py-2 flex flex-wrap items-center justify-between gap-3">
      <div id="hdr-hinge" class="flex flex-wrap items-center gap-3 mono text-[13px] text-[#9CA3AF]">Loading decision hinge&hellip;</div>
    </div>
  </div>
</header>

<main class="mx-auto max-w-[1440px] px-3 sm:px-6 lg:px-8 py-6 space-y-5">

  <section class="border border-[#1F2937] bg-[#111827] overflow-hidden">
    <button data-toggle="sec-positions" class="sh-open w-full flex items-center justify-between gap-4 px-4 h-10 hover:bg-[#1F2937] transition-colors text-left">
      <span class="flex items-center gap-3 min-w-0">
        <span class="hidden sm:inline-flex size-6 bg-[#090D16] grid place-items-center mono text-[12px] font-bold shrink-0 border border-[#1F2937]">01</span>
        <span class="mono text-[13px] tracking-[0.14em] uppercase flex items-center gap-2">Positions <span class="sh-chev size-4 border border-[#1F2937] grid place-items-center">&#9660;</span></span>
      </span>
      <span class="hidden md:inline mono text-[12px] tracking-widest uppercase text-[#9CA3AF]">Realized and Unrealized P&amp;L &mdash; kept separate</span>
    </button>
    <div id="sec-positions" class="sh-fade grid grid-cols-12 gap-0"></div>
  </section>

  <section class="border border-[#1F2937] bg-[#111827] overflow-hidden">
    <button data-toggle="sec-verdict" class="sh-open w-full flex items-center justify-between px-4 h-10 border-b border-[#1F2937] hover:bg-[#1F2937] transition-colors text-left">
      <span class="flex items-center gap-3">
        <span class="mono text-[13px] tracking-[0.14em] uppercase font-semibold">Verdict</span>
        <span class="hidden md:inline mono text-[12px] tracking-widest uppercase text-[#9CA3AF]">Five decisive readings</span>
      </span>
      <span class="sh-chev size-6 border border-[#1F2937] grid place-items-center">&#9660;</span>
    </button>
    <div id="sec-verdict" class="sh-fade grid grid-cols-12 gap-0"></div>
  </section>

  <section class="space-y-3">
    <button data-toggle="sec-gauges" class="sh-open flex items-center gap-2.5 px-3 py-1.5 bg-[#111827] hover:bg-[#1F2937] transition-colors border border-[#1F2937]">
      <span class="mono text-[13px] tracking-[0.16em] uppercase">Readiness</span>
      <span class="sh-chev">&#9660;</span>
    </button>
    <div id="sec-gauges" class="sh-fade grid grid-cols-1 lg:grid-cols-3 gap-4"></div>
  </section>

  <section class="space-y-3">
    <button data-toggle="sec-evidence" class="sh-open flex items-center gap-2.5 px-3 py-1.5 bg-[#111827] hover:bg-[#1F2937] transition-colors border border-[#1F2937]">
      <span class="mono text-[13px] tracking-[0.16em] uppercase">Evidence</span>
      <span class="sh-chev">&#9660;</span>
    </button>
    <div id="sec-evidence" class="sh-fade grid grid-cols-1 lg:grid-cols-2 gap-4"></div>
  </section>

  <section class="border border-[#1F2937] bg-[#111827] overflow-hidden">
    <div class="bg-[#111827] px-4 h-11 flex items-center justify-between gap-4 border-b border-[#1F2937]">
      <span class="mono text-[13px] tracking-[0.14em] uppercase">Active Markets</span>
      <div class="flex items-center gap-1 shrink-0">
        <button data-tab="markets" class="tab-btn h-7 px-3 mono text-[13px] font-semibold tracking-widest uppercase border border-[#1F2937] bg-[#10B981] text-white">Active Markets</button>
        <button data-tab="settled" class="tab-btn h-7 px-3 mono text-[13px] font-semibold tracking-widest uppercase border border-[#1F2937] hover:bg-[#1F2937]">Closed History</button>
        <button data-tab="funnel" class="tab-btn h-7 px-3 mono text-[13px] font-semibold tracking-widest uppercase border border-[#1F2937] hover:bg-[#1F2937]">Selection</button>
      </div>
    </div>
    <div id="tab-markets" class="tab-panel"></div>
    <div id="tab-settled" class="tab-panel sh-collapsed"></div>
    <div id="tab-funnel" class="tab-panel sh-collapsed"></div>
  </section>

  <section class="border border-[#1F2937] bg-[#111827] p-5 flex flex-col lg:flex-row gap-6">
    <div class="flex-1">
      <div class="mono text-[13px] font-semibold tracking-[0.12em] uppercase">Scope</div>
      <div class="mono text-[12px] leading-6 text-[#9CA3AF] mt-2">
        Profit and go / no-go for this desk. No portfolio optimization, no automation. Yields are context only &mdash; never added to settled. Expand any panel to trace every figure back to the fleet database.
      </div>
    </div>
    <div class="hidden lg:block w-px bg-[#1F2937]"></div>
    <div id="scope-tiles" class="flex-1 grid grid-cols-3 gap-2 mono text-[12px] tracking-widest uppercase"></div>
  </section>

  <div class="flex flex-col sm:flex-row items-center justify-between gap-3 mono text-[12px] tracking-[0.12em] uppercase text-[#9CA3AF] pb-2">
    <span>maker fleet &middot; Spread Hunter design &middot; color guides decisions</span>
    <span class="flex items-center gap-2"><span class="size-1.5 bg-[#10B981]"></span> Green = gain <span class="size-1.5 bg-[#EF4444] ml-2"></span> Red = loss / risk</span>
  </div>
</main>

<script>
// ---------- formatting ----------
function fmtUsd(v){ if(v===null||v===undefined) return "--"; const s=v<0?"-":"+"; return s+"$"+Math.abs(v).toFixed(2); }
function fmtPct(v,d){ if(v===null||v===undefined) return "--"; d=d===undefined?1:d; const s=v>0?"+":""; return s+v.toFixed(d)+"%"; }
function esc(s){ return (s===null||s===undefined?"":String(s)).replace(/[&<>]/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }

// ---------- collapsible sections ----------
document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-toggle]");
  if (btn) {
    const id = btn.getAttribute("data-toggle");
    const el = document.getElementById(id);
    const open = !el.classList.contains("sh-collapsed");
    el.classList.toggle("sh-collapsed", open);
    btn.classList.toggle("sh-open", !open);
  }
  const tab = e.target.closest("[data-tab]");
  if (tab) {
    document.querySelectorAll(".tab-btn").forEach(b => {
      const active = b === tab;
      b.classList.toggle("bg-[#10B981]", active);
      b.classList.toggle("text-white", active);
    });
    document.querySelectorAll(".tab-panel").forEach(p => p.classList.toggle("sh-collapsed", p.id !== "tab-" + tab.dataset.tab));
  }
});

// ---------- SVG chart builders ----------
function bellCurveSvg(opts){
  const {min,max,mean,stdev,zero,ciLow,ciHigh,color,w,h} = opts;
  const W = w||320, H = h||84, pad=14;
  const x = v => pad + ((v-min)/(max-min))*(W-pad*2);
  const sd = stdev && stdev>0 ? stdev : (max-min)/6;
  const bell = [];
  for(let i=0;i<80;i++){
    const v = min + (i/79)*(max-min);
    const z = (v-mean)/sd;
    bell.push([v, Math.exp(-0.5*z*z)]);
  }
  const maxY = Math.max(...bell.map(p=>p[1]));
  const yS = y => (H-22) - (y/maxY)*(H-40);
  const path = bell.map((p,i) => `${i===0?"M":"L"} ${x(p[0]).toFixed(1)} ${yS(p[1]).toFixed(1)}`).join(" ");
  const area = `${path} L ${x(bell[bell.length-1][0]).toFixed(1)} ${H-22} L ${x(bell[0][0]).toFixed(1)} ${H-22} Z`;
  let ciRect = "";
  if(ciLow!==undefined && ciHigh!==undefined){
    ciRect = `<rect x="${x(ciLow).toFixed(1)}" y="${H-16}" width="${Math.max(2,x(ciHigh)-x(ciLow)).toFixed(1)}" height="8" fill="#10B981" opacity="0.9"/>`;
  }
  let zeroLine = "";
  if(zero!==undefined && zero>=min && zero<=max){
    zeroLine = `<line x1="${x(zero).toFixed(1)}" x2="${x(zero).toFixed(1)}" y1="8" y2="${H-10}" stroke="#EF4444" stroke-width="1.25" stroke-dasharray="4 3"/>`;
  }
  return `<svg viewBox="0 0 ${W} ${H}" class="w-full" style="height:${H}px">
    ${[0,1,2,3].map(i=>`<line x1="${pad}" x2="${W-pad}" y1="${12+i*14}" y2="${12+i*14}" stroke="#1F2937" stroke-width="1"/>`).join("")}
    <path d="${area}" fill="${color}" fill-opacity="0.12" stroke="none"/>
    <path d="${path}" fill="none" stroke="${color}" stroke-width="1.4"/>
    ${ciRect}
    ${zeroLine}
    <circle cx="${x(mean).toFixed(1)}" cy="${yS(Math.exp(0)).toFixed(1)}" r="4" fill="#111827" stroke="#F9FAFB" stroke-width="1.5"/>
  </svg>`;
}

function gaugeSvg(value, max, color, threshold){
  const pct = Math.min(100, Math.max(0, (value/max)*100));
  const r = 54, circ = 2*Math.PI*r, dash = (pct/100)*(circ*0.75);
  const tRot = threshold!==undefined ? (threshold/max)*270 : null;
  return `<svg width="124" height="124" viewBox="0 0 132 132" style="transform:rotate(-135deg)">
    <circle cx="66" cy="66" r="${r}" fill="none" stroke="#1F2937" stroke-width="9" stroke-dasharray="${(circ*0.75).toFixed(1)} ${(circ*0.25).toFixed(1)}"/>
    <circle cx="66" cy="66" r="${r}" fill="none" stroke="${color}" stroke-width="9" stroke-dasharray="${dash.toFixed(1)} ${(circ-dash).toFixed(1)}"/>
    ${tRot!==null?`<circle cx="66" cy="66" r="${r}" fill="none" stroke="#9CA3AF" stroke-width="1.5" stroke-dasharray="3 4" opacity="0.3" style="transform:rotate(${tRot}deg);transform-origin:66px 66px"/>`:""}
  </svg>`;
}

function donutSvg(categories){
  const total = categories.reduce((a,c)=>a+c.n,0) || 1;
  const R=56, C=2*Math.PI*R;
  let acc=0;
  const segs = categories.map(c => {
    const len = (c.n/total)*C, gap=2, dashLen=Math.max(0,len-gap), dashGap=C-dashLen, rot=(acc/C)*360;
    acc += len;
    return `<circle cx="74" cy="74" r="${R}" fill="none" stroke="${c.color}" stroke-width="18" stroke-dasharray="${dashLen.toFixed(1)} ${dashGap.toFixed(1)}" style="transform:rotate(${rot.toFixed(1)}deg);transform-origin:74px 74px"/>`;
  }).join("");
  return `<svg width="148" height="148" viewBox="0 0 148 148" style="transform:rotate(-90deg)">
    <circle cx="74" cy="74" r="${R}" fill="none" stroke="#1F2937" stroke-width="18"/>${segs}</svg>`;
}

// Order-depth / mid visualization, ported from server/fleet_dash.py's
// `ladder()` + `capBar()` (same source fields: mid_up/up_bid/up_ask are the
// live book, our_up/our_dn_as_up are this fleet's own resting quotes on the
// UP axis). Kept as one horizontal strip: a price band from bid to ask with
// mid/our-quote markers, then a YES/NO resting-notional split bar below it.
function orderDepthHtml(m){
  const mid = m.mid_up, bid = m.up_bid, ask = m.up_ask;
  let band = '<span class="mono text-[11px] text-[#9CA3AF]">No two-sided book</span>';
  if(mid!==null && bid!==null && ask!==null && mid!==undefined && bid!==undefined && ask!==undefined){
    const v = m.max_spread || 0.045;
    const half = Math.max(v*1.35, (ask-bid)*0.75, 0.01);
    const lo = mid-half, hi = mid+half, W = hi-lo;
    const x = p => Math.max(0, Math.min(100, 100*(p-lo)/W));
    const wl = x(mid-v), wr = x(mid+v);
    const mark = (p,color,w) => (p===null||p===undefined) ? "" :
      `<span style="position:absolute;left:${x(p)}%;top:2px;bottom:2px;width:${w}px;background:${color};transform:translateX(-50%)"></span>`;
    band = `<div style="position:relative;height:26px;width:100%;max-width:220px">
      <div style="position:absolute;left:${wl}%;width:${Math.max(0,wr-wl)}%;top:9px;height:8px;background:#10B98122;border-left:1px solid #10B98155;border-right:1px solid #10B98155"></div>
      <div style="position:absolute;left:0;right:0;top:13px;height:1px;background:#1F2937"></div>
      ${mark(mid,'#F59E0B',3)}
      ${mark(bid,'#9CA3AF',1.5)}${mark(ask,'#9CA3AF',1.5)}
      ${mark(m.our_up,'#3B82F6',2)}
      ${mark(m.our_dn_as_up,'#EF4444',2)}
      <span style="position:absolute;left:${x(mid)}%;top:16px;transform:translateX(-50%);white-space:nowrap" class="mono text-[10px] font-bold text-[#F9FAFB]">MID ${mid.toFixed(3)}</span>
    </div>`;
  }
  let cap = '<div class="mono text-[10px] text-[#9CA3AF] mt-1">No capital resting</div>';
  const quotes = m.quotes || [];
  if(quotes.length){
    let up=0, dn=0;
    for(const o of quotes){
      const remaining = o.remaining===null||o.remaining===undefined ? Math.max(0,(o.size||0)-(o.filled||0)) : o.remaining;
      const notional = o.notional===null||o.notional===undefined ? (o.price||0)*remaining : o.notional;
      if(o.side==='UP') up+=notional; else dn+=notional;
    }
    const total = up+dn;
    if(total>0){
      const upPct=100*up/total, dnPct=100*dn/total;
      cap = `<div style="display:flex;height:12px;width:100%;max-width:220px;background:#090D16;overflow:hidden;margin-top:4px" class="mono text-[9px] font-bold">
        <div style="width:${dnPct}%;background:#EF444433;color:#EF4444;display:flex;align-items:center;padding-left:4px;white-space:nowrap;overflow:hidden">$${dn.toFixed(0)} NO</div>
        <div style="width:${upPct}%;background:#10B98133;color:#10B981;display:flex;align-items:center;justify-content:flex-end;padding-right:4px;white-space:nowrap;overflow:hidden">$${up.toFixed(0)} YES</div>
      </div>`;
    }
  }
  return `${band}${cap}`;
}

// ---------- section renderers ----------
function renderPositions(s){
  document.getElementById("sec-positions").innerHTML = `
    <div class="col-span-12 lg:col-span-6 p-6 lg:p-7 border-b lg:border-b-0 lg:border-r border-[#1F2937] relative">
      <div class="absolute top-0 left-0 w-full h-[2px] bg-[#10B981]"></div>
      <div class="mono text-[12px] tracking-[0.16em] uppercase text-[#10B981] font-semibold flex items-center gap-2"><span class="size-1.5 bg-[#10B981]"></span> Realized P&amp;L &mdash; Settled Positions</div>
      <div class="mono text-[13px] tracking-[0.08em] uppercase text-[#9CA3AF] mt-1">Booked closes plus resolutions on markets held to settlement</div>
      <div class="mt-6 flex items-baseline gap-3 flex-wrap">
        <div class="mono text-[44px] font-bold leading-none tracking-[-0.03em] ${s.realized_usd>=0?'text-[#10B981]':'text-[#EF4444]'}">${fmtUsd(s.realized_usd)}</div>
        <div class="mono text-[14px] font-semibold px-2 py-1 bg-[#10B981]/10 border border-[#10B981]/20 text-[#10B981]">${fmtPct(s.realized_pct)}</div>
        <div class="mono text-[13px] text-[#9CA3AF]">on ${s.realized_cost.toFixed(0)} committed</div>
      </div>
      <div class="mt-3 mono text-[12px] leading-5 text-[#9CA3AF] bg-[#090D16] border border-[#1F2937] px-3 py-2">
        ${fmtUsd(s.realized_usd)} Realized &nbsp;|&nbsp; ${fmtUsd(s.rebate_usd)} Earned Rebates &nbsp;|&nbsp; ${fmtUsd(s.unrealized_usd)} Unrealized &nbsp;=&nbsp; <span class="font-bold text-[#F9FAFB]">${fmtUsd(s.total_liquidation_usd)} Total Liquidation P&amp;L</span>
      </div>
      <div class="mt-3 grid grid-cols-2 sm:grid-cols-4 gap-2">
        <div class="p-3.5 bg-[#10B981]/10 border border-[#10B981]/20"><div class="mono text-[12px] tracking-[0.12em] uppercase text-[#10B981]">Settled</div><div class="mono text-[17px] font-bold mt-1 text-[#10B981]">${s.n_settled}</div><div class="mono text-[12px] text-[#10B981]/70 mt-0.5">${s.wins}W / ${s.losses}L</div></div>
        <div class="p-3.5 bg-[#111827] border border-[#1F2937]"><div class="mono text-[12px] tracking-[0.12em] uppercase text-[#9CA3AF]">Total closes</div><div class="mono text-[17px] font-bold mt-1">${s.closes}</div><div class="mono text-[12px] text-[#9CA3AF] mt-0.5">${fmtUsd(s.closed_pnl)} booked</div></div>
        <div class="p-3.5 bg-[#10B981]/10 border border-[#10B981]/20"><div class="mono text-[12px] tracking-[0.12em] uppercase text-[#10B981]">Rebates earned</div><div class="mono text-[17px] font-bold mt-1 text-[#10B981]">${fmtUsd(s.rebate_usd)}</div><div class="mono text-[12px] text-[#10B981]/70 mt-0.5">${s.rebate_fills} fills${s.rebate_cps===null?'':' &middot; '+s.rebate_cps.toFixed(2)+'c/sh'}</div></div>
        <div class="p-3.5 bg-[#111827] border border-[#1F2937]"><div class="mono text-[12px] tracking-[0.12em] uppercase text-[#9CA3AF]">Status</div><div class="mono text-[15px] font-bold mt-1">${esc(s.status)}</div><div class="mono text-[12px] text-[#9CA3AF] mt-0.5">${s.n_settled} / ${s.go_live_min_settled} to go-live</div></div>
      </div>
    </div>
    <div class="col-span-12 lg:col-span-6 p-6 lg:p-7 relative border-l border-[#1F2937]">
      <div class="absolute top-0 left-0 w-full h-[2px] bg-[#3B82F6]"></div>
      <div class="mono text-[12px] tracking-[0.16em] uppercase text-[#3B82F6] font-semibold flex items-center gap-2"><span class="size-1.5 bg-[#3B82F6]"></span> Unrealized P&amp;L &mdash; Open Positions</div>
      <div class="mono text-[13px] tracking-[0.08em] uppercase text-[#9CA3AF] mt-1">Midpoint estimate &middot; separate from Realized P&amp;L</div>
      <div class="mt-6 flex items-baseline gap-3">
        <div class="mono text-[44px] font-bold leading-none tracking-[-0.03em]">${fmtUsd(s.unrealized_usd)}</div>
        <div class="mono text-[12px] tracking-widest uppercase px-2 py-1 bg-[#3B82F6] border border-[#1F2937]">Unrealized</div>
      </div>
      <div class="mono text-[13px] tracking-widest uppercase text-[#9CA3AF] mt-1">Floating midpoint on ${s.committed_open_usd.toFixed(0)} &middot; ${s.active_positions} active positions</div>
      <div class="mt-3 bg-[#111827] border border-[#1F2937] px-3 py-2 mono text-[12px] leading-5 text-[#9CA3AF]">Realized ${fmtUsd(s.realized_usd)} and Unrealized ${fmtUsd(s.unrealized_usd)} are separate ledgers. Never summed.</div>
    </div>`;
}

function renderVerdict(s){
  const weightGap = (s.mean_return_pct!==null && s.realized_pct!==null) ? (s.mean_return_pct - s.realized_pct) : null;
  const tiles = [
    {label:"90% Lower Bound", value: fmtPct(s.ci90_lower_pct,2), accent: (s.ci90_lower_pct||0)>0?"#10B981":"#EF4444",
     chart: bellCurveSvg({min:-100,max:100,mean:s.mean_return_pct||0,stdev:s.stdev_return_pct,zero:0,
       ciLow:s.ci90_lower_pct,ciHigh:(s.mean_return_pct!==null&&s.ci90_lower_pct!==null)?(2*s.mean_return_pct-s.ci90_lower_pct):undefined,
       color:"#3B82F6",w:140,h:64}),
     sub:`Mean ${fmtPct(s.mean_return_pct)} &middot; &sigma; ${s.stdev_return_pct===null?'--':s.stdev_return_pct.toFixed(1)+'%'} &middot; n=${s.n_settled}`},
    {label:"Markout Drift", value: s.markout_mean_per_share===null?"--":(s.markout_mean_per_share*100).toFixed(2)+"&cent;", accent:"#EF4444",
     chart: bellCurveSvg({min:-6,max:6,mean:(s.markout_mean_per_share||0)*100,stdev:2,zero:0,color:"#EF4444",w:140,h:64}),
     sub:`n_eff ${s.markout_n_eff.toFixed(1)} &middot; measured`},
    {label:"Weighting Gap", value: weightGap===null?"--":weightGap.toFixed(1)+" pp", accent:"#F59E0B",
     chart: `<div class="space-y-1.5 pt-1"><div class="flex items-center gap-2"><span class="mono text-[12px] text-[#9CA3AF] w-[46px]">Equal</span><div class="flex-1 h-[7px] bg-[#090D16] border border-[#1F2937] overflow-hidden"><div class="h-full bg-[#9CA3AF]" style="width:${Math.min(100,Math.abs(s.mean_return_pct||0))}%"></div></div></div><div class="flex items-center gap-2"><span class="mono text-[12px] text-[#9CA3AF] w-[46px]">Cash</span><div class="flex-1 h-[7px] bg-[#090D16] border border-[#1F2937] overflow-hidden"><div class="h-full bg-[#3B82F6]" style="width:${Math.min(100,Math.abs(s.realized_pct||0))}%"></div></div></div></div>`,
     sub:`${fmtPct(s.mean_return_pct)} eq vs ${fmtPct(s.realized_pct)} $`},
    {label:"Concentration", value: s.categories.length?(s.categories[0].pct.toFixed(1)+"% "+s.categories[0].name):"--",
     accent: (s.max_category_share||0)>s.go_live_max_category_share ? "#EF4444":"#10B981",
     chart: `<div class="relative h-[22px] bg-[#090D16] border border-[#1F2937] overflow-hidden flex items-end">${s.categories.map(c=>`<div class="flex-1 flex flex-col items-center justify-end h-full pt-1"><span class="mono text-[11px] font-bold" style="color:${c.color}">${c.pct.toFixed(0)}%</span><div class="w-full" style="height:${(c.pct/100)*14}px;background:${c.color}"></div></div>`).join("")}</div>`,
     sub:`${s.categories.map(c=>c.name+" "+c.n).join(" / ")} &middot; Cap ${(s.go_live_max_category_share*100).toFixed(0)}%`},
    {label:"Sample", value: `${s.n_settled} / ${s.go_live_min_settled}`, accent:"#9CA3AF",
     chart: `<div class="h-[36px] flex items-center"><div class="w-full h-[7px] bg-[#090D16] border border-[#1F2937] overflow-hidden relative"><div class="absolute left-0 top-0 bottom-0 bg-[#3B82F6]" style="width:${Math.min(100,100*s.n_settled/s.go_live_min_settled)}%"></div><div class="absolute top-0 bottom-0 w-px bg-[#F59E0B]" style="left:${100*s.signal_min_settled/s.go_live_min_settled}%"></div></div></div>`,
     sub:`${s.wins}W &middot; ${s.losses}L &middot; ${s.closes} closes`},
  ];
  const left = `<div class="col-span-12 lg:col-span-3 p-6 flex flex-col justify-center border-b lg:border-b-0 lg:border-r border-[#1F2937]">
    <div class="mono text-[12px] tracking-[0.18em] uppercase text-[#9CA3AF]">Go or no-go</div>
    <div class="font-display text-[26px] leading-none mt-2">Determined by<br><span class="text-[#10B981]">the lower bound</span></div>
    <div class="mono text-[13px] leading-5 text-[#9CA3AF] mt-3">Not the mean. Not win rate. Bound, drift, gap, concentration, and sample.</div>
    <div class="mt-5 flex items-center gap-2">
      <span class="px-3 py-1.5 border border-[#1F2937] mono text-[13px] tracking-widest uppercase">${esc(s.status)}</span>
      <span class="px-3 py-1.5 border border-[#1F2937] mono text-[13px] tracking-widest uppercase">${s.n_settled} / ${s.go_live_min_settled}</span>
    </div>
  </div>`;
  const right = `<div class="col-span-12 lg:col-span-9 grid grid-cols-1 md:grid-cols-3 lg:grid-cols-5 divide-y md:divide-y-0 md:divide-x divide-[#1F2937]">
    ${tiles.map((t,i)=>`<div class="p-3 flex flex-col gap-2 hover:bg-[#090D16] transition-colors">
      <div class="mono text-[12px] tracking-[0.14em] uppercase text-[#9CA3AF]">0${i+1} &mdash; ${t.label}</div>
      <div class="mono text-[19px] font-bold leading-none px-2 py-1.5 w-fit border" style="color:${t.accent};background:${t.accent}1A;border-color:${t.accent}33">${t.value}</div>
      ${t.chart}
      <div class="mono text-[12px] leading-4 text-[#9CA3AF]">${t.sub}</div>
    </div>`).join("")}
  </div>`;
  document.getElementById("sec-verdict").innerHTML = left + right;
}

function renderGauges(s){
  const items = [
    {label:"Capital Committed", sub:`${s.committed_open_usd.toFixed(0)} of ${s.max_committed_usd.toFixed(0)}`, value:s.committed_open_usd, max:s.max_committed_usd, color:"#3B82F6"},
    {label:"Markout Coverage", sub:`${s.markout_n_eff.toFixed(1)} fills &middot; threshold ${s.markout_min_sample}`, value:s.markout_n_eff, max:Math.max(s.markout_min_sample*2, s.markout_n_eff), color:"#10B981", threshold:s.markout_min_sample},
    {label:"Settlement Progress", sub:`${s.n_settled} of ${s.go_live_min_settled} settled`, value:s.n_settled, max:s.go_live_min_settled, color:"#EF4444", threshold:s.signal_min_settled},
  ];
  document.getElementById("sec-gauges").innerHTML = items.map(g => {
    const pct = Math.min(100, Math.max(0,(g.value/g.max)*100));
    return `<div class="bg-[#111827] border border-[#1F2937] p-5 flex flex-col">
      <div class="flex items-start justify-between gap-3">
        <div><div class="mono text-[12px] tracking-[0.14em] uppercase text-[#9CA3AF]">${g.label}</div><div class="font-display text-[13px] tracking-tight mt-0.5">${g.sub}</div></div>
        <span class="mono text-[12px] tracking-widest px-2 py-1 border font-semibold shrink-0" style="color:${g.color};background:${g.color}1A;border-color:${g.color}33">${pct.toFixed(1)}%</span>
      </div>
      <div class="mt-5 flex items-center gap-5">
        <div class="relative size-[124px] shrink-0">
          ${gaugeSvg(g.value, g.max, g.color, g.threshold)}
          <div class="absolute inset-0 grid place-items-center"><div class="text-center translate-y-1">
            <div class="mono text-[21px] font-bold leading-none tracking-tight">${Number.isInteger(g.value)?g.value:g.value.toFixed(1)}<span class="text-[13px] font-normal text-[#9CA3AF]"> / ${g.max.toFixed(0)}</span></div>
          </div></div>
        </div>
        <div class="flex-1 space-y-3">
          <div class="h-1.5 w-full bg-[#090D16] overflow-hidden"><div class="h-full" style="width:${pct}%;background:${g.color}"></div></div>
          <div class="mono text-[13px] leading-5 text-[#9CA3AF] border-l-2 pl-3" style="border-color:${g.color}">${g.label==='Capital Committed'?'Cash committed to resting offers and held inventory.':g.label==='Markout Coverage'?'Effective sample vs the gate threshold this fleet actually uses.':'Live needs '+s.go_live_min_settled+' settled; signal floor is '+s.signal_min_settled+'.'}</div>
        </div>
      </div>
    </div>`;
  }).join("");
}

function renderEvidence(s){
  document.getElementById("sec-evidence").innerHTML = `
    <div class="border border-[#1F2937] bg-[#111827] overflow-hidden">
      <div class="px-3.5 h-9 flex items-center gap-2 mono text-[13px] tracking-[0.14em] uppercase font-semibold border-b border-[#1F2937]">Performance</div>
      <div class="p-3 space-y-3">
        <div class="bg-[#111827] border border-[#1F2937] p-3">
          <div class="flex items-center justify-between mono text-[12px] tracking-[0.14em] uppercase text-[#9CA3AF]"><span>90% Confidence vs zero</span><span class="px-2 py-1 border" style="color:${(s.ci90_lower_pct||0)>0?'#10B981':'#EF4444'};border-color:${(s.ci90_lower_pct||0)>0?'#10B98133':'#EF444433'}">Lower ${fmtPct(s.ci90_lower_pct,2)}</span></div>
          ${bellCurveSvg({min:-100,max:100,mean:s.mean_return_pct||0,stdev:s.stdev_return_pct,zero:0,color:"#3B82F6"})}
        </div>
        <div class="bg-[#111827] border border-[#1F2937] p-3">
          <div class="flex items-center justify-between mono text-[12px] tracking-[0.14em] uppercase text-[#9CA3AF]"><span>Markout drift</span><span class="px-2 py-1 border text-[#EF4444] border-[#EF444433]">Mean ${s.markout_mean_per_share===null?'--':(s.markout_mean_per_share*100).toFixed(2)+'c'}</span></div>
          ${bellCurveSvg({min:-6,max:6,mean:(s.markout_mean_per_share||0)*100,stdev:2,zero:0,color:"#EF4444"})}
        </div>
        <div class="grid grid-cols-3 gap-2">
          <div class="bg-[#10B981]/10 border border-[#10B981]/20 p-3"><div class="mono text-[12px] tracking-[0.14em] uppercase text-[#10B981]">Realized</div><div class="mono text-[18px] font-bold text-[#10B981] mt-1">${fmtUsd(s.realized_usd)}</div></div>
          <div class="bg-[#111827] border border-[#1F2937] p-3"><div class="mono text-[12px] tracking-[0.14em] uppercase text-[#9CA3AF]">Mean / Stdev</div><div class="mono text-[15px] font-bold mt-1">${fmtPct(s.mean_return_pct)} / ${s.stdev_return_pct===null?'--':s.stdev_return_pct.toFixed(1)+'%'}</div></div>
          <div class="bg-[#3B82F6]/10 border border-[#3B82F6]/20 p-3"><div class="mono text-[12px] tracking-[0.14em] uppercase text-[#3B82F6]">Unrealized</div><div class="mono text-[18px] font-bold mt-1 text-[#3B82F6]">${fmtUsd(s.unrealized_usd)}</div></div>
        </div>
      </div>
    </div>
    <div class="border border-[#1F2937] bg-[#111827] overflow-hidden">
      <div class="px-3.5 h-9 flex items-center gap-2 mono text-[13px] tracking-[0.14em] uppercase font-semibold border-b border-[#1F2937] bg-[#3B82F6] text-white">Readiness</div>
      <div class="p-3 space-y-3">
        <div class="grid grid-cols-2 gap-3">
          <div class="bg-[#111827] border border-[#1F2937] p-3"><div class="mono text-[12px] tracking-[0.14em] uppercase text-[#9CA3AF]">Category mix</div><div class="mt-3 flex justify-center">${donutSvg(s.categories)}</div></div>
          <div class="space-y-3">
            <div class="bg-[#111827] border border-[#1F2937] p-3">
              <div class="mono text-[12px] tracking-[0.14em] uppercase text-[#EF4444] font-semibold">Concentration vs cap</div>
              <div class="mt-3 space-y-1.5">${s.categories.map(c=>`<div class="flex items-center justify-between mono text-[12px]"><span class="flex items-center gap-1.5"><span class="size-2" style="background:${c.color}"></span>${c.name}</span><span class="${c.pct>s.go_live_max_category_share*100?'text-[#EF4444] font-bold':'text-[#9CA3AF]'}">${c.pct.toFixed(1)}%</span></div>`).join("")}</div>
            </div>
            <div class="grid grid-cols-2 gap-2">
              <div class="bg-[#111827] border border-[#1F2937] p-2.5 text-center"><div class="mono text-[12px] tracking-widest uppercase text-[#9CA3AF]">Settled</div><div class="mono text-[17px] font-bold">${s.n_settled} / ${s.go_live_min_settled}</div></div>
              <div class="bg-[#111827] border border-[#1F2937] p-2.5 text-center"><div class="mono text-[12px] tracking-widest uppercase text-[#9CA3AF]">Calendar</div><div class="mono text-[17px] font-bold ${(s.calendar_days||0)<s.go_live_min_calendar_days?'text-[#EF4444]':'text-[#10B981]'}">${s.calendar_days===null?'--':s.calendar_days.toFixed(1)} / ${s.go_live_min_calendar_days} d</div></div>
            </div>
          </div>
        </div>
      </div>
    </div>
    <div class="border border-[#1F2937] bg-[#111827] overflow-hidden">
      <div class="px-3.5 h-9 flex items-center gap-2 mono text-[13px] tracking-[0.14em] uppercase font-semibold border-b border-[#1F2937]">Risk</div>
      <div class="p-3 grid grid-cols-3 gap-2">
        <div class="bg-[#111827] border border-[#1F2937] p-3 text-center"><div class="mono text-[12px] tracking-widest uppercase text-[#9CA3AF]">Wins</div><div class="mono text-[17px] font-bold text-[#10B981]">${s.wins}</div></div>
        <div class="bg-[#111827] border border-[#1F2937] p-3 text-center"><div class="mono text-[12px] tracking-widest uppercase text-[#9CA3AF]">Losses</div><div class="mono text-[17px] font-bold text-[#EF4444]">${s.losses}</div></div>
        <div class="bg-[#111827] border border-[#1F2937] p-3 text-center"><div class="mono text-[12px] tracking-widest uppercase text-[#9CA3AF]">Win rate</div><div class="mono text-[17px] font-bold">${(s.wins+s.losses)?(100*s.wins/(s.wins+s.losses)).toFixed(1):'--'}%</div></div>
      </div>
    </div>
    <div class="border border-[#1F2937] bg-[#111827] overflow-hidden">
      <div class="px-3.5 h-9 flex items-center gap-2 mono text-[13px] tracking-[0.14em] uppercase font-semibold border-b border-[#1F2937]">Capital &amp; Operations</div>
      <div class="p-3 grid grid-cols-3 gap-2">
        <div class="bg-[#111827] border border-[#1F2937] p-3 text-center"><div class="mono text-[12px] tracking-widest uppercase text-[#9CA3AF]">Committed</div><div class="mono text-[17px] font-bold">${s.committed_open_usd.toFixed(0)}</div></div>
        <div class="bg-[#111827] border border-[#1F2937] p-3 text-center"><div class="mono text-[12px] tracking-widest uppercase text-[#9CA3AF]">Limit</div><div class="mono text-[17px] font-bold">${s.max_committed_usd.toFixed(0)}</div></div>
        <div class="bg-[#111827] border border-[#1F2937] p-3 text-center"><div class="mono text-[12px] tracking-widest uppercase text-[#9CA3AF]">Bankroll</div><div class="mono text-[17px] font-bold">${s.bankroll_usd.toFixed(0)}</div></div>
      </div>
    </div>`;
}

function renderMarkets(rows){
  const table = `<div class="overflow-x-auto"><table class="w-full text-left border-collapse">
    <thead><tr class="bg-[#090D16] mono text-[12px] tracking-[0.14em] uppercase border-b border-[#1F2937]">
      <th class="px-3 py-2.5">Market</th><th class="px-2 py-2.5">Cat</th><th class="px-2 py-2.5 text-right">Commit</th>
      <th class="px-2 py-2.5">Order Depth / Mid</th><th class="px-2 py-2.5 text-right">Unrealized</th><th class="px-2 py-2.5 text-right">Realized</th><th class="px-2 py-2.5 text-center">Fills</th><th class="px-2 py-2.5">Status</th>
    </tr></thead>
    <tbody class="mono text-[14px] divide-y divide-[#1F2937]">
      ${rows.length ? rows.map(r => `<tr class="hover:bg-[#090D16] transition-colors">
        <td class="px-2.5 py-2 font-medium max-w-[200px] truncate text-[13px]">${esc(r.market)}</td>
        <td class="px-3 py-2.5"><span class="px-1 py-0.5 text-[12px] font-bold tracking-widest uppercase bg-[#1F2937]">${esc(r.category)}</span></td>
        <td class="px-2 py-2 text-right font-semibold text-[13px]">$${r.committed.toFixed(0)}</td>
        <td class="px-2 py-2">${orderDepthHtml(r)}</td>
        <td class="px-2 py-2 text-right"><span class="inline-flex items-center gap-1 px-1.5 py-0.5 text-[13px] font-bold border ${r.unrealized>=0?'bg-[#10B981]/10 border-[#10B981]/20 text-[#10B981]':'bg-[#EF4444]/10 border-[#EF4444]/20 text-[#EF4444]'}">${fmtUsd(r.unrealized)}</span></td>
        <td class="px-2 py-2 text-right">${r.closes ? `<span class="inline-flex items-center gap-1 px-1.5 py-0.5 text-[13px] font-bold border ${r.realized>=0?'bg-[#10B981]/10 border-[#10B981]/20 text-[#10B981]':'bg-[#EF4444]/10 border-[#EF4444]/20 text-[#EF4444]'}">${fmtUsd(r.realized)}</span>` : `<span class="text-[#9CA3AF]">--</span>`}</td>
        <td class="px-2 py-2 text-center"><span class="px-2 py-1 text-[13px] font-bold border border-[#1F2937]">${r.fills}</span></td>
        <td class="px-2 py-2 text-[12px] text-[#9CA3AF] max-w-[180px] truncate">${esc(r.status)}</td>
      </tr>`).join("") : `<tr><td colspan="8" class="px-4 py-6 text-center mono text-[13px] text-[#9CA3AF]">No active markets right now.</td></tr>`}
    </tbody></table></div>
    <div class="px-4 py-2.5 border-t border-[#1F2937] mono text-[12px] tracking-widest uppercase text-[#9CA3AF]">${rows.length} active markets &middot; Realized = already-booked P&amp;L from partial closes on that market &middot; Unrealized values are estimates only</div>`;
  document.getElementById("tab-markets").innerHTML = table;
}

function renderSettled(rows, totalCloses){
  const shown = rows.slice(0, 60);
  const table = `<div class="px-4 py-3 border-b border-[#1F2937] flex items-center justify-between">
      <span class="mono text-[13px] tracking-[0.14em] uppercase font-semibold">History &mdash; the sample that matters</span>
      <span class="mono text-[12px] tracking-widest uppercase px-2 py-1 bg-[#1F2937]">${totalCloses} closes</span>
    </div>
    <div class="overflow-x-auto"><table class="w-full text-left border-collapse">
    <thead><tr class="bg-[#090D16] mono text-[12px] tracking-[0.14em] uppercase border-b border-[#1F2937]">
      <th class="px-3 py-2.5">Market</th><th class="px-2 py-2 text-right">Result</th><th class="px-2 py-2 text-right">Return</th><th class="px-3 py-2.5">Method</th><th class="px-2 py-2 text-center">Outcome</th>
    </tr></thead>
    <tbody class="mono text-[14px] divide-y divide-[#1F2937]">
      ${shown.length ? shown.map(r => `<tr class="hover:bg-[#1F2937] transition-colors">
        <td class="px-3 py-2.5 font-medium max-w-[220px] truncate">${esc(r.market)}</td>
        <td class="px-3 py-2.5 text-right font-bold ${r.win?'text-[#10B981]':'text-[#EF4444]'}">${fmtUsd(r.pnl)}</td>
        <td class="px-3 py-2.5 text-right font-semibold ${(r.pnl_pct||0)<0?'text-[#EF4444]':''}">${fmtPct(r.pnl_pct)}</td>
        <td class="px-3 py-2.5"><span class="px-2 py-0.5 text-[12px] tracking-widest uppercase border border-[#1F2937]">${esc(r.method)}</span></td>
        <td class="px-2 py-2 text-center">${r.win?'<span class="inline-flex items-center gap-1 px-2 py-1 bg-[#10B981]/10 border border-[#10B981]/20 text-[#10B981] text-[12px] font-bold">Gain</span>':'<span class="inline-flex items-center gap-1 px-2 py-1 bg-[#EF4444]/10 border border-[#EF4444]/20 text-[#EF4444] text-[12px] font-bold">Loss</span>'}</td>
      </tr>`).join("") : `<tr><td colspan="5" class="px-4 py-6 text-center mono text-[13px] text-[#9CA3AF]">No closed positions yet.</td></tr>`}
    </tbody></table></div>`;
  document.getElementById("tab-settled").innerHTML = table;
}

function renderFunnel(f){
  const steps = [
    {label:"Scanned", value:f.scanned, color:"#9CA3AF"},
    {label:"Scored", value:f.scored, color:"#6B7280"},
    {label:"Eligible / Picked", value:f.picked, color:"#3B82F6"},
    {label:"Settled", value:f.settled, color:"#10B981"},
  ];
  const maxV = Math.max(1, ...steps.map(s=>s.value||0));
  const funnelHtml = `<div class="p-4 lg:p-6"><div class="grid grid-cols-12 gap-0 border border-[#1F2937]">
    ${steps.map((s,i)=>`<div class="col-span-12 md:col-span-3 border-b md:border-b-0 md:border-r last:border-r-0 border-[#1F2937] p-4 flex flex-col">
      <div class="mono text-[12px] tracking-[0.14em] uppercase text-[#9CA3AF]">0${i+1} &mdash; ${s.label}</div>
      <div class="mono text-[36px] font-bold leading-none tracking-tight mt-3" style="color:${s.color}">${s.value===undefined||s.value===null?'--':s.value.toLocaleString()}</div>
      <div class="mt-3 h-1.5 w-full bg-[#090D16] overflow-hidden"><div class="h-full" style="width:${100*(s.value||0)/maxV}%;background:${s.color}"></div></div>
    </div>`).join("")}
  </div>
  <div class="mt-4 border border-[#1F2937]">
    <div class="px-4 h-9 flex items-center justify-between"><span class="mono text-[13px] tracking-[0.14em] uppercase font-semibold">Rejections &mdash; why markets did not advance</span></div>
    <div class="grid grid-cols-1 md:grid-cols-3 divide-y md:divide-y-0 md:divide-x divide-[#1F2937]">
      ${f.rejections.length ? f.rejections.map(r=>`<div class="p-4">
        <div class="mono text-[13px] tracking-widest uppercase">${esc(r.cause)}</div>
        <div class="mono text-[20px] font-bold mt-1">${r.n}</div>
        <div class="mono text-[12px] text-[#9CA3AF] mt-1">${r.would_fund} would fund if loosened</div>
      </div>`).join("") : `<div class="p-4 mono text-[13px] text-[#9CA3AF]">No rejection telemetry recorded yet.</div>`}
    </div>
  </div></div>`;
  document.getElementById("tab-funnel").innerHTML = funnelHtml;
}

// ---------- boot ----------
async function boot(){
  const [s, mk, st, fn] = await Promise.all([
    fetch("/api/summary").then(r=>r.json()),
    fetch("/api/markets").then(r=>r.json()),
    fetch("/api/settled").then(r=>r.json()),
    fetch("/api/funnel").then(r=>r.json()),
  ]);

  document.getElementById("hdr-pills").innerHTML = `
    <span class="mono text-[12px] tracking-[0.12em] uppercase px-2.5 py-1 bg-[#090D16] font-semibold border border-[#1F2937]">${esc(s.status)}</span>
    <span class="mono text-[12px] tracking-[0.12em] uppercase px-2.5 py-1 border border-[#1F2937]">${s.n_settled} of ${s.go_live_min_settled} settled</span>`;
  document.getElementById("hdr-live").innerHTML = `
    <span class="size-1.5 ${s.fleet_alive?'bg-[#10B981] animate-pulse':'bg-[#F59E0B]'}"></span>
    <span class="tracking-[0.12em] uppercase text-[12px]">${s.fleet_alive?'Live':'Idle'}</span>`;
  document.getElementById("hdr-hinge").innerHTML = `
    <span class="mono text-[12px] tracking-[0.16em] uppercase text-[#9CA3AF]">Decision hinge</span>
    <span class="mono text-[14px] font-semibold tracking-tight flex items-center gap-2">90% lower bound
      <span class="px-1.5 py-0.5 ml-1 font-bold border" style="background:${(s.ci90_lower_pct||0)>0?'#10B981':'#EF4444'};border-color:${(s.ci90_lower_pct||0)>0?'#10B981':'#EF4444'}">${fmtPct(s.ci90_lower_pct,2)}</span></span>
    <span class="hidden md:inline mono text-[13px] text-[#9CA3AF]">Mean ${fmtPct(s.mean_return_pct)} &middot; n=${s.n_settled}</span>`;

  renderPositions(s);
  renderVerdict(s);
  renderGauges(s);
  renderEvidence(s);
  renderMarkets(mk.markets);
  renderSettled(st.settled, st.total_closes);
  renderFunnel(fn);

  document.getElementById("scope-tiles").innerHTML = `
    <div class="border border-[#1F2937] p-3 text-center"><div class="text-[#9CA3AF]">Data as of</div><div class="font-semibold mt-1">Now</div></div>
    <div class="border border-[#1F2937] p-3 text-center"><div class="text-[#9CA3AF]">Sample</div><div class="font-semibold mt-1">${esc(s.status)}</div><div class="text-[#9CA3AF] mt-0.5">n = ${s.n_settled}</div></div>
    <div class="border border-[#1F2937] p-3 text-center"><div class="text-[#9CA3AF]">Lower bound</div><div class="font-bold mt-1">${fmtPct(s.ci90_lower_pct,2)}</div></div>`;
}
boot();
</script>
""")
