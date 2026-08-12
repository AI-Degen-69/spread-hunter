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
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Big+Shoulders+Display:wght@600;700;800&family=Geist+Mono:wght@400;500;600;700&display=swap">
<script src="https://cdn.tailwindcss.com"></script>
<style>
  /* Design tokens -- the operator-approved desk palette, codified as one
     system instead of scattered arbitrary values. Big Shoulders Display
     (a condensed industrial grotesque, the visual language of odds boards)
     carries the identity; IBM Plex Mono carries every number and label. */
  :root{
    color-scheme:dark;
    --ink:#080C14;      /* canvas */
    --panel:rgba(15,23,42,.65); /* frosted glass panel */
    --line:rgba(255,255,255,.07); /* translucent hairline */
    --ink-soft:#F9FAFB; /* primary text */
    --muted:#94A3B8;    /* secondary text, lifted contrast */
    --signal:#10B981;   /* gains, thresholds, live */
    --loss:#EF4444;     /* losses, fails */
    --open:#3B82F6;     /* open exposure */
    --warn:#F59E0B;     /* warnings, idle, stale */
    --gold:#FBBF24;     /* reserved: mid badge, go-live call */
  }
  /* Dark mesh gradient canvas. */
  body{
    background:linear-gradient(180deg,#080C14 0%,#0B111E 100%) fixed;
    color:var(--ink-soft);
    font-family:"Geist Mono",ui-monospace,"SF Mono",Menlo,monospace;
    -webkit-font-smoothing:antialiased;}
  .font-display{font-family:"Big Shoulders Display",Impact,"Arial Narrow",sans-serif;
                letter-spacing:0.015em;font-weight:700;}
  .mono{font-family:"Geist Mono",ui-monospace,"SF Mono",Menlo,monospace;
        font-variant-numeric:tabular-nums;font-feature-settings:"tnum";}
  /* Phase-2 terminal upgrade: every panel surface becomes frosted glass
     over the mesh, hairlines become faint white, and secondary labels
     gain contrast. These post-Tailwind overrides (with !important) let
     both the static markup and every JS-rendered card inherit the look
     without touching a single renderer. */
  /* background-color only -- the shorthand would wipe the canvas gradient. */
  .bg-\[\#111827\]{background-color:rgba(15,23,42,.65)!important;backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);}
  .bg-\[\#090D16\]{background-color:rgba(8,12,20,.55)!important;}
  .border-\[\#1F2937\]{border-color:rgba(255,255,255,.07)!important;}
  .bg-\[\#1F2937\]{background-color:rgba(148,163,184,.12)!important;}
  .text-\[\#9CA3AF\]{color:#94A3B8!important;}
  .hero-shadow{filter:drop-shadow(0 4px 12px rgba(0,0,0,.4));}
  ::selection{background:var(--signal);color:var(--ink-soft);}
  :focus-visible{outline:2px solid var(--signal);outline-offset:2px;}
  .sh-fade{transition:opacity .2s ease,transform .2s ease;}
  .sh-collapsed{display:none !important;}
  .sh-chev{transition:transform .2s ease;}
  .sh-open .sh-chev{transform:rotate(180deg);}
  /* Boot choreography: panels rise in one after another; the fleet pulse
     beats only while its data is fresh. Both stand down for reduced motion. */
  @keyframes sh-rise{from{opacity:0;transform:translateY(7px)}to{opacity:1;transform:none}}
  .sh-rise{animation:sh-rise .4s cubic-bezier(.2,.7,.2,1) both;}
  @keyframes sh-beat{0%,100%{box-shadow:0 0 0 0 rgba(16,185,129,0)}45%{box-shadow:0 0 0 6px rgba(16,185,129,.16)}}
  .pulse-live{animation:sh-beat 2.4s ease-out infinite;}
  /* During a live refresh the panels dim briefly instead of flickering. */
  main.sh-refreshing section{opacity:.55;transition:opacity .18s ease;}
  /* Data-change cues: financial figures flash green/red when their value
     changes (300ms), status cells fade+scale in, and the data-health dot
     pulses continuously. All stand down under reduced motion. */
  @keyframes kpi-flash-up{0%{background-color:rgba(34,197,94,.2)}100%{background-color:transparent}}
  @keyframes kpi-flash-down{0%{background-color:rgba(239,68,68,.2)}100%{background-color:transparent}}
  .flash-up{animation:kpi-flash-up .3s ease-out;}
  .flash-down{animation:kpi-flash-down .3s ease-out;}
  @keyframes status-in{from{opacity:0;transform:scale(.98)}to{opacity:1;transform:scale(1)}}
  .status-in{animation:status-in .18s ease-out both;}
  @keyframes health-pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.4;transform:scale(.95)}}
  .health-pulse{animation:health-pulse 2s ease-in-out infinite;}
  /* Naked-USD capacity bar at >=80% utilization: the fill pulses red to
     demand attention, alongside the HIGH EXPOSURE badge. */
  @keyframes warn-bar{0%,100%{opacity:1}50%{opacity:.45}}
  .warn-bar{animation:warn-bar 1s ease-in-out infinite;}
  /* Metric tooltips: a tiny info icon beside statistical headers opens a
     styled card carrying the formula, the live value, and the gate meaning.
     Pure CSS -- hover or keyboard focus (button inside the wrap) reveals it.
     No tooltip dependency needed; matches the terminal's square language. */
  .tip-wrap{position:relative;display:inline-flex;vertical-align:middle;margin-left:6px;}
  .tip-ico{width:14px;height:14px;border:1px solid rgba(148,163,184,.5);color:#94A3B8;background:transparent;border-radius:9999px;font-size:9px;font-weight:700;line-height:1;display:inline-flex;align-items:center;justify-content:center;cursor:help;padding:0;}
  .tip-ico:hover,.tip-wrap:focus-within .tip-ico{border-color:var(--gold);color:var(--gold);}
  .tip-pop{position:absolute;bottom:calc(100% + 8px);left:50%;transform:translateX(-50%) translateY(4px);width:252px;padding:10px 12px;background:rgba(8,12,20,.97);border:1px solid rgba(255,255,255,.16);box-shadow:0 8px 24px rgba(0,0,0,.55);opacity:0;visibility:hidden;pointer-events:none;transition:opacity .15s ease,transform .15s ease,visibility .15s;z-index:70;text-align:left;}
  .tip-pop .tip-k{display:block;font-size:10px;letter-spacing:.16em;text-transform:uppercase;color:var(--gold);font-weight:700;margin-bottom:6px;}
  .tip-pop .tip-t{display:block;font-size:11px;line-height:1.65;color:#94A3B8;font-weight:400;letter-spacing:0;text-transform:none;}
  .tip-wrap:hover .tip-pop,.tip-wrap:focus-within .tip-pop{opacity:1;visibility:visible;transform:translateX(-50%) translateY(0);}
  @media (prefers-reduced-motion: reduce){
    .sh-rise,.pulse-live,.flash-up,.flash-down,.status-in,.health-pulse,.warn-bar{animation:none;}
    .sh-fade,.sh-chev,main.sh-refreshing section,#drawer,#drawer-backdrop,.tip-pop{transition:none;}
  }
  ::-webkit-scrollbar{height:8px;width:8px;}
  ::-webkit-scrollbar-thumb{background:var(--line);}
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


LANDING_HTML = _wrap("Spread Hunter -- Hunter fleet", r"""
<div class="h-[2px] w-full bg-[#10B981]"></div>
<nav class="sticky top-0 z-40 bg-[#111827]/95 backdrop-blur border-b border-[#1F2937]">
  <div class="mx-auto max-w-[1440px] px-6 lg:px-10 h-[72px] flex items-center justify-between">
    <div class="flex items-center gap-5 min-w-0">
      <div class="flex items-center gap-3 shrink-0">
        <div class="size-[40px] bg-[#F9FAFB] text-[#090D16] grid place-items-center mono text-[13px] font-bold tracking-widest">SH<span class="text-[#EF4444]">&mdash;</span>01</div>
        <div>
          <div class="font-display text-[16px] leading-none">SPREAD HUNTER</div>
          <div class="mono text-[13px] tracking-[0.14em] uppercase text-[#9CA3AF] mt-0.5">Hunter fleet &middot; live desk</div>
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
        <h1 class="font-display text-[46px] lg:text-[64px] leading-[0.9] tracking-[-0.01em] mt-6">
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
      <span class="w-fit mono text-[13px] tracking-[0.14em] uppercase px-2 py-1 bg-[#1F2937] border border-[#1F2937]">01 &middot; BOOK</span>
      <div class="font-display text-[18px] leading-none tracking-tight">Two valuations, kept apart</div>
      <div class="text-[14px] leading-6 text-[#9CA3AF]">Realized value from closed positions alongside Unrealized P&amp;L for open exposure &mdash; never combined.</div>
    </div>
    <div class="col-span-12 md:col-span-6 lg:col-span-3 border-r last:border-r-0 border-b lg:border-b-0 border-[#1F2937] p-6 flex flex-col gap-4">
      <span class="w-fit mono text-[13px] tracking-[0.14em] uppercase px-2 py-1 bg-[#1F2937] border border-[#1F2937]">02 &middot; GATES</span>
      <div class="font-display text-[18px] leading-none tracking-tight">Two threshold gauges</div>
      <div class="text-[14px] leading-6 text-[#9CA3AF]">Capital committed against your limit, and markout sample maturity. Settlement progress lives with the Verdict.</div>
    </div>
    <div class="col-span-12 md:col-span-6 lg:col-span-3 border-r last:border-r-0 border-b lg:border-b-0 border-[#1F2937] p-6 flex flex-col gap-4">
      <span class="w-fit mono text-[13px] tracking-[0.14em] uppercase px-2 py-1 bg-[#1F2937] border border-[#1F2937]">03 &middot; PROOF</span>
      <div class="font-display text-[18px] leading-none tracking-tight">Performance, readiness, risk, capital</div>
      <div class="text-[14px] leading-6 text-[#9CA3AF]">Each figure notes its source, so you can trace every conclusion back to the database.</div>
    </div>
    <div class="col-span-12 md:col-span-6 lg:col-span-3 p-6 flex flex-col gap-4">
      <span class="w-fit mono text-[13px] tracking-[0.14em] uppercase px-2 py-1 bg-[#1F2937] border border-[#1F2937]">04 &middot; DETAIL</span>
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
          <div class="text-[#9CA3AF] mt-1.5 flex flex-col gap-2"><span class="flex items-center gap-x-4 gap-y-1.5 flex-wrap"><span class="inline-flex items-center gap-1.5 whitespace-nowrap"><span class="size-3 bg-[#10B981] inline-block shrink-0"></span> Green &mdash; gains &amp; thresholds</span><span class="inline-flex items-center gap-1.5 whitespace-nowrap"><span class="size-3 bg-[#EF4444] inline-block shrink-0"></span> Red &mdash; risk &amp; fails</span><span class="inline-flex items-center gap-1.5 whitespace-nowrap"><span class="size-3 bg-[#3B82F6] inline-block shrink-0"></span> Blue &mdash; open positions</span></span><span>Square corners, hairline rules, restrained color.</span></div>
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
    <span>Hunter fleet &middot; Spread Hunter design</span>
    <span class="flex items-center gap-2"><span class="size-1.5 bg-[#10B981]"></span> Swiss Edition &middot; Live Desk</span>
  </div>
</section>

<script>
function fmtUsd(v){ if(v===null||v===undefined) return "--"; const s=v<0?"-":"+"; return s+"$"+Math.abs(v).toFixed(2); }
function fmtPct(v,d){ if(v===null||v===undefined) return "--"; d=d===undefined?1:d; const s=v>0?"+":""; return s+v.toFixed(d)+"%"; }

async function load(){
  let s;
  try {
    const r = await fetch("/api/summary");
    if (!r.ok) throw new Error("HTTP " + r.status);
    s = await r.json();
  } catch (e) {
    const nav = document.getElementById("nav-status");
    nav.innerHTML = `<span class="size-1.5 bg-[#EF4444]"></span><span class="tracking-[0.12em] uppercase text-[13px]">Offline</span>`;
    document.getElementById("hero-realized").textContent = "--";
    document.getElementById("hero-unrealized").textContent = "--";
    const list = document.getElementById("verdict-list");
    list.innerHTML = `<div class="p-4 mono text-[13px] text-[#EF4444]">Could not load the live summary.</div>`;
    const detail = document.createElement("div");
    detail.className = "mono text-[12px] text-[#9CA3AF] mt-1";
    detail.textContent = e && e.message ? e.message : String(e);
    list.appendChild(detail);
    return;
  }

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
      <div class="size-10 px-1 bg-[#111827] text-[#F9FAFB] grid place-items-center mono text-[12px] font-bold whitespace-nowrap shrink-0 border border-[#1F2937]">SH<span class="text-[#EF4444]">&mdash;</span>01</div>
      <div class="hidden md:block min-w-0">
        <div class="font-display text-[16px] leading-none flex items-center gap-2">SPREAD HUNTER <span class="hidden lg:inline mono text-[12px] tracking-[0.14em] uppercase font-normal text-[#9CA3AF]">Fleet Desk</span></div>
        <div class="mono text-[12px] tracking-[0.12em] uppercase text-[#9CA3AF] truncate">Live fleet database &mdash; maker</div>
      </div>
      <div id="hdr-pills" class="hidden lg:flex items-center gap-1.5 ml-2"></div>
    </div>
    <div class="flex items-center gap-2 shrink-0">
      <div id="hdr-live" class="hidden sm:flex items-center gap-2 mono text-[13px] border border-[#1F2937] bg-[#111827] px-3 h-9">
        <span class="size-1.5 bg-[#9CA3AF]"></span>
        <span class="tracking-[0.12em] uppercase text-[12px]">Loading</span>
      </div>
      <a href="http://127.0.0.1:8801/?view=scan" target="_blank" rel="noopener" title="Legacy market-scan view (not yet redesigned)" class="hidden md:flex h-9 px-3.5 border border-[#1F2937] bg-[#111827] mono text-[13px] font-semibold tracking-widest uppercase hover:bg-[#1F2937] transition-colors items-center gap-1.5">Market Scan &nearr;</a>
      <a href="/" class="h-9 px-3.5 border border-[#1F2937] bg-[#111827] mono text-[13px] font-semibold tracking-widest uppercase hover:bg-[#1F2937] transition-colors flex items-center gap-1.5">&larr; Home</a>
    </div>
  </div>
  <div class="bg-[#111827] border-y border-[#1F2937]">
    <div class="mx-auto max-w-[1440px] px-6 lg:px-8 min-h-9 py-2.5 flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
      <!-- The decision hinge: one line answering the desk's only question --
           go or no-go -- rendered by renderHinge() from the live summary. -->
      <div id="hdr-hinge" class="w-full flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
        <span class="mono text-[13px] tracking-[0.2em] uppercase text-[#9CA3AF]">Loading the call&hellip;</span>
      </div>
    </div>
  </div>
</header>

<main class="mx-auto max-w-[1440px] px-3 sm:px-6 lg:px-8 py-6 space-y-5">

  <section class="sh-rise hero-shadow border border-[#1F2937] bg-[#111827] overflow-hidden" style="animation-delay:.05s">
    <button data-toggle="sec-positions" aria-expanded="true" class="sh-open w-full flex items-center justify-between gap-4 px-4 h-11 border-b border-[#1F2937] hover:bg-[#1F2937] transition-colors text-left">
      <span class="flex items-center gap-3 min-w-0">
        <span class="hidden sm:inline-flex h-6 items-center px-1.5 bg-[#090D16] mono text-[11px] font-bold tracking-[0.18em] shrink-0 border border-[#1F2937]">BOOK</span>
        <span class="mono text-[13px] tracking-[0.14em] uppercase font-semibold flex items-center gap-2">Positions <span class="sh-chev size-4 border border-[#1F2937] grid place-items-center">&#9660;</span></span>
      </span>
      <span class="hidden md:inline mono text-[12px] tracking-widest uppercase text-[#9CA3AF]">Realized and Unrealized P&amp;L &mdash; kept separate</span>
    </button>
    <div id="sec-positions" class="sh-fade grid grid-cols-12 gap-0"></div>
  </section>

  <section class="sh-rise border border-[#1F2937] bg-[#111827] overflow-hidden" style="animation-delay:.1s">
    <button data-toggle="sec-verdict" aria-expanded="true" class="sh-open w-full flex items-center justify-between gap-4 px-4 h-11 border-b border-[#1F2937] hover:bg-[#1F2937] transition-colors text-left">
      <span class="flex items-center gap-3 min-w-0">
        <span class="hidden sm:inline-flex h-6 items-center px-1.5 bg-[#090D16] mono text-[11px] font-bold tracking-[0.18em] shrink-0 border border-[#1F2937]">CALL</span>
        <span class="mono text-[13px] tracking-[0.14em] uppercase font-semibold flex items-center gap-2">Verdict <span class="sh-chev size-4 border border-[#1F2937] grid place-items-center">&#9660;</span></span>
      </span>
      <span class="hidden md:inline mono text-[12px] tracking-widest uppercase text-[#9CA3AF]">Five decisive readings</span>
    </button>
    <div id="sec-verdict" class="sh-fade grid grid-cols-12 gap-0"></div>
  </section>

  <section class="sh-rise border border-[#1F2937] bg-[#111827] overflow-hidden" style="animation-delay:.15s">
    <button data-toggle="sec-gauges" aria-expanded="true" class="sh-open w-full flex items-center justify-between gap-4 px-4 h-11 border-b border-[#1F2937] hover:bg-[#1F2937] transition-colors text-left">
      <span class="flex items-center gap-3 min-w-0">
        <span class="hidden sm:inline-flex h-6 items-center px-1.5 bg-[#090D16] mono text-[11px] font-bold tracking-[0.18em] shrink-0 border border-[#1F2937]">GATES</span>
        <span class="mono text-[13px] tracking-[0.14em] uppercase font-semibold flex items-center gap-2">Readiness <span class="sh-chev size-4 border border-[#1F2937] grid place-items-center">&#9660;</span></span>
      </span>
      <span class="hidden md:inline mono text-[12px] tracking-widest uppercase text-[#9CA3AF]">Two threshold gauges</span>
    </button>
    <div id="sec-gauges" class="sh-fade grid grid-cols-1 lg:grid-cols-2 gap-4 p-4 bg-[#090D16]"></div>
  </section>

  <section class="sh-rise border border-[#1F2937] bg-[#111827] overflow-hidden" style="animation-delay:.2s">
    <button data-toggle="sec-evidence" aria-expanded="true" class="sh-open w-full flex items-center justify-between gap-4 px-4 h-11 border-b border-[#1F2937] hover:bg-[#1F2937] transition-colors text-left">
      <span class="flex items-center gap-3 min-w-0">
        <span class="hidden sm:inline-flex h-6 items-center px-1.5 bg-[#090D16] mono text-[11px] font-bold tracking-[0.18em] shrink-0 border border-[#1F2937]">PROOF</span>
        <span class="mono text-[13px] tracking-[0.14em] uppercase font-semibold flex items-center gap-2">Evidence <span class="sh-chev size-4 border border-[#1F2937] grid place-items-center">&#9660;</span></span>
      </span>
      <span class="hidden md:inline mono text-[12px] tracking-widest uppercase text-[#9CA3AF]">Performance, readiness, risk, capital</span>
    </button>
    <div id="sec-evidence" class="sh-fade grid grid-cols-1 lg:grid-cols-2 gap-4 p-4 bg-[#090D16]"></div>
  </section>

  <section class="sh-rise border border-[#1F2937] bg-[#111827] overflow-hidden" style="animation-delay:.25s">
    <div class="w-full flex items-center justify-between gap-4 px-4 h-11 border-b border-[#1F2937]">
      <button data-toggle="sec-inspection" aria-expanded="true" class="sh-open flex items-center gap-3 min-w-0 h-full flex-1 text-left hover:bg-[#1F2937] transition-colors">
        <span class="hidden sm:inline-flex h-6 items-center px-1.5 bg-[#090D16] mono text-[11px] font-bold tracking-[0.18em] shrink-0 border border-[#1F2937]">DETAIL</span>
        <span class="mono text-[13px] tracking-[0.14em] uppercase font-semibold flex items-center gap-2">Inspection <span class="sh-chev size-4 border border-[#1F2937] grid place-items-center">&#9660;</span></span>
      </button>
      <div class="flex items-center gap-1 shrink-0" role="tablist" aria-label="Inspection views">
        <button data-tab="markets" id="tab-btn-markets" role="tab" aria-selected="true" aria-controls="tab-markets" class="tab-btn h-7 px-3 mono text-[12px] font-bold tracking-widest uppercase border border-[#1F2937] bg-[#10B981] text-white">Active Markets</button>
        <button data-tab="settled" id="tab-btn-settled" role="tab" aria-selected="false" aria-controls="tab-settled" class="tab-btn h-7 px-3 mono text-[12px] font-bold tracking-widest uppercase border border-[#1F2937] hover:bg-[#1F2937] text-[#9CA3AF]">Closed History</button>
        <button data-tab="funnel" id="tab-btn-funnel" role="tab" aria-selected="false" aria-controls="tab-funnel" class="tab-btn h-7 px-3 mono text-[12px] font-bold tracking-widest uppercase border border-[#1F2937] hover:bg-[#1F2937] text-[#9CA3AF]">Selection</button>
      </div>
    </div>
    <div id="sec-inspection">
      <div id="tab-markets" role="tabpanel" aria-labelledby="tab-btn-markets" class="tab-panel sh-fade"></div>
      <div id="tab-settled" role="tabpanel" aria-labelledby="tab-btn-settled" class="tab-panel sh-collapsed sh-fade"></div>
      <div id="tab-funnel" role="tabpanel" aria-labelledby="tab-btn-funnel" class="tab-panel sh-collapsed sh-fade"></div>
    </div>
  </section>

  <section class="sh-rise border border-[#1F2937] bg-[#111827] p-5 flex flex-col lg:flex-row gap-6" style="animation-delay:.3s">
    <div class="flex-1">
      <div class="mono text-[13px] font-semibold tracking-[0.12em] uppercase">Scope</div>
      <div class="mono text-[12px] leading-6 text-[#9CA3AF] mt-2">
        Profit and go / no-go for this desk. No portfolio optimization, no automation. Yields are context only &mdash; never added to settled. Expand any panel to trace every figure back to the fleet database.
      </div>
    </div>
    <div class="hidden lg:block w-px bg-[#1F2937]"></div>
    <div id="scope-tiles" class="flex-1 grid grid-cols-1 gap-2 mono text-[12px] tracking-widest uppercase"></div>
  </section>

  <div class="flex flex-col sm:flex-row items-center justify-between gap-3 mono text-[12px] tracking-[0.12em] uppercase text-[#9CA3AF] pb-2">
    <span>Hunter fleet &middot; Spread Hunter design &middot; color guides decisions</span>
    <span class="flex items-center gap-2"><span class="size-1.5 bg-[#10B981]"></span> Green = gain <span class="size-1.5 bg-[#EF4444] ml-2"></span> Red = loss / risk</span>
  </div>
</main>

<div id="dist-modal" role="dialog" aria-modal="true" aria-label="Expanded distribution chart" class="fixed inset-0 z-50 hidden items-center justify-center bg-black/70 p-4" onclick="if(event.target===this)closeDistModal()">
  <div class="bg-[#111827] border border-[#1F2937] max-w-[720px] w-full max-h-[90vh] overflow-y-auto">
    <div class="flex items-center justify-between px-4 h-11 border-b border-[#1F2937]">
      <span id="dist-modal-title" class="mono text-[13px] tracking-[0.14em] uppercase font-semibold"></span>
      <button onclick="closeDistModal()" class="size-7 border border-[#1F2937] grid place-items-center hover:bg-[#1F2937] transition-colors mono text-[13px]">&times;</button>
    </div>
    <div id="dist-modal-body" class="p-5"></div>
  </div>
</div>

<!-- Market-detail drawer: slides in from the right edge (x: 100% -> 0) when
     an active-market row is clicked. Populated from the market row object
     and the settled exits already in memory -- no extra fetch. -->
<div id="drawer-backdrop" class="fixed inset-0 z-40 bg-black/60 opacity-0 pointer-events-none transition-opacity duration-300" onclick="closeDrawer()" aria-hidden="true"></div>
<div id="drawer" role="dialog" aria-modal="true" aria-label="Market details" class="fixed inset-y-0 right-0 z-50 w-full max-w-[520px] bg-[#111827] border-l border-[#1F2937] flex flex-col shadow-[-16px_0_40px_rgba(0,0,0,0.45)] transition-transform duration-300 ease-out translate-x-full">
  <div id="drawer-body" class="flex-1 overflow-y-auto flex flex-col"></div>
</div>

<script>
// ---------- formatting ----------
function fmtUsd(v){ if(v===null||v===undefined) return "--"; const s=v<0?"-":"+"; return s+"$"+Math.abs(v).toFixed(2); }
function fmtPct(v,d){ if(v===null||v===undefined) return "--"; d=d===undefined?1:d; const s=v>0?"+":""; return s+v.toFixed(d)+"%"; }
function esc(s){ return (s===null||s===undefined?"":String(s)).replace(/[&<>]/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }
function escAttr(s){ return (s===null||s===undefined?"":String(s)).replace(/[&<>"']/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
function fmtPnlHTML(v){
  if(v===null||v===undefined) return "--";
  if(Math.abs(v) < 0.005) return `<span class="text-[#9CA3AF] opacity-60">+$0.00</span>`;
  const s=v<0?"-":"+"; const c=v>0?"text-[#10B981]":"text-[#EF4444]";
  return `<span class="font-bold ${c}">${s}$${Math.abs(v).toFixed(2)}</span>`;
}
function fmtPctHTML(v){
  if(v===null||v===undefined) return "--";
  if(Math.abs(v) < 0.005) return `<span class="text-[#9CA3AF] opacity-60">+0.00%</span>`;
  const s=v<0?"-":"+"; const c=v>0?"text-[#10B981]":"text-[#EF4444]";
  return `<span class="font-bold ${c}">${s}${Math.abs(v).toFixed(2)}%</span>`;
}
function badgePnlHTML(v) {
  if(v===null||v===undefined) return `<span class="text-[#9CA3AF]">--</span>`;
  if(Math.abs(v) < 0.005) return `<span class="inline-flex items-center gap-1 px-1.5 py-0.5 text-[13px] font-bold border border-[#1F2937]/50 text-[#9CA3AF] opacity-60">+$0.00</span>`;
  const s=v<0?"-":"+";
  const bg = v>0 ? 'bg-[#10B981]/10 border-[#10B981]/20 text-[#10B981]' : 'bg-[#EF4444]/10 border-[#EF4444]/20 text-[#EF4444]';
  return `<span class="inline-flex items-center gap-1 px-1.5 py-0.5 text-[13px] font-bold border ${bg}">${s}$${Math.abs(v).toFixed(2)}</span>`;
}
function badgePnlPctHTML(usd, pct) {
  if (usd === null || usd === undefined) return `<span class="text-[#9CA3AF]">--</span>`;
  const pctStr = (pct !== null && pct !== undefined) ? ` <span class="font-normal opacity-75 ml-0.5">(${pct>0?'+':''}${pct.toFixed(2)}%)</span>` : '';
  if (Math.abs(usd) < 0.005) {
    const zeroPctStr = (pct !== null && pct !== undefined) ? ` <span class="font-normal opacity-75 ml-0.5">(0.00%)</span>` : '';
    return `<span class="inline-flex items-center gap-1 px-1.5 py-0.5 text-[13px] font-bold border border-[#1F2937]/50 text-[#9CA3AF] opacity-60">+$0.00${zeroPctStr}</span>`;
  }
  const s = usd < 0 ? "-" : "+";
  const bg = usd > 0 ? 'bg-[#10B981]/10 border-[#10B981]/20 text-[#10B981]' : 'bg-[#EF4444]/10 border-[#EF4444]/20 text-[#EF4444]';
  return `<span class="inline-flex items-center gap-1 px-1.5 py-0.5 text-[13px] font-bold border ${bg}">${s}$${Math.abs(usd).toFixed(2)}${pctStr}</span>`;
}
function plainPnlPctHTML(usd, pct) {
  if (usd === null || usd === undefined) return `<span class="text-[#9CA3AF]">--</span>`;
  const pctStr = (pct !== null && pct !== undefined) ? ` <span class="font-normal opacity-75 text-[12px] ml-1">(${pct>0?'+':''}${pct.toFixed(2)}%)</span>` : '';
  if (Math.abs(usd) < 0.005) {
    const zeroPctStr = (pct !== null && pct !== undefined) ? ` <span class="font-normal opacity-75 text-[12px] ml-1">(0.00%)</span>` : '';
    return `<span class="text-[#9CA3AF] opacity-60 font-bold">+$0.00${zeroPctStr}</span>`;
  }
  const s = usd < 0 ? "-" : "+";
  const c = usd > 0 ? "text-[#10B981]" : "text-[#EF4444]";
  return `<span class="font-bold ${c}">${s}$${Math.abs(usd).toFixed(2)}${pctStr}</span>`;
}

// ---------- collapsible sections ----------
document.addEventListener("click", (e) => {
  const dr = e.target.closest("[data-drawer]");
  if (dr){ openDrawer(dr.getAttribute("data-drawer")); return; }
  // Quick-filter chips re-render the market table in place -- no reload.
  const fcat = e.target.closest("[data-fcat]");
  if (fcat){ FILTERS.cat = fcat.getAttribute("data-fcat"); renderMarkets(LAST_MARKETS); tickAgeBadges(); return; }
  const fst = e.target.closest("[data-fst]");
  if (fst){ FILTERS.state = fst.getAttribute("data-fst"); renderMarkets(LAST_MARKETS); tickAgeBadges(); return; }
  const fclear = e.target.closest("[data-fclear]");
  if (fclear){ FILTERS.cat = "all"; FILTERS.state = "all"; renderMarkets(LAST_MARKETS); tickAgeBadges(); return; }
  const btn = e.target.closest("[data-toggle]");
  if (btn) {
    const id = btn.getAttribute("data-toggle");
    const el = document.getElementById(id);
    const open = !el.classList.contains("sh-collapsed");
    el.classList.toggle("sh-collapsed", open);
    btn.classList.toggle("sh-open", !open);
    btn.setAttribute("aria-expanded", open ? "false" : "true");
  }
  const tab = e.target.closest("[data-tab]");
  if (tab) {
    document.querySelectorAll(".tab-btn").forEach(b => {
      const active = b === tab;
      b.classList.toggle("bg-[#10B981]", active);
      b.classList.toggle("text-white", active);
      b.setAttribute("aria-selected", active ? "true" : "false");
    });
    document.querySelectorAll(".tab-panel").forEach(p => p.classList.toggle("sh-collapsed", p.id !== "tab-" + tab.dataset.tab));
  }
  const row = e.target.closest("[data-market]");
  if (row) {
    toggleMarketExpand(row.getAttribute("data-market"));
  }
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && DRAWER_SLUG) closeDrawer();
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

// Larger, axis-labeled version of the same fitted-normal curve for the
// click-to-expand modal. Same math as bellCurveSvg -- this is a normal
// approximation from the real mean/stdev, not a histogram of actual
// per-market returns, so the caption says so rather than implying more
// precision than the sample supports.
function expandedBellCurveSvg(opts){
  const {mean,stdev,zero,ciLow,ciHigh,color,fmt} = opts;
  const W = 640, H = 300, padL = 46, padR = 20, padT = 16, padB = 34;
  const sd = stdev && stdev>0 ? stdev : Math.abs(mean||1)/4 || 1;
  // Domain is CENTERED ON THE MEAN (+/-3.5 sigma) so the peak always sits
  // in the horizontal middle of the chart, regardless of where the mean
  // happens to fall -- a fixed arbitrary range only looked centered when
  // the mean was near zero. Expanded symmetrically (never asymmetrically)
  // if that would otherwise clip the zero line or the CI band.
  let half = 3.5*sd;
  if(zero!==undefined) half = Math.max(half, Math.abs(zero-mean));
  if(ciLow!==undefined) half = Math.max(half, Math.abs(ciLow-mean));
  if(ciHigh!==undefined) half = Math.max(half, Math.abs(ciHigh-mean));
  half *= 1.08; // a little breathing room past the outermost marker
  const min = mean-half, max = mean+half;
  const x = v => padL + ((v-min)/(max-min))*(W-padL-padR);
  const bell = [];
  for(let i=0;i<160;i++){
    const v = min + (i/159)*(max-min);
    const z = (v-mean)/sd;
    bell.push([v, Math.exp(-0.5*z*z)]);
  }
  const maxY = Math.max(...bell.map(p=>p[1]));
  const baseY = H - padB;
  const yS = y => baseY - (y/maxY)*(baseY-padT-20);
  const path = bell.map((p,i) => `${i===0?"M":"L"} ${x(p[0]).toFixed(1)} ${yS(p[1]).toFixed(1)}`).join(" ");
  const area = `${path} L ${x(bell[bell.length-1][0]).toFixed(1)} ${baseY} L ${x(bell[0][0]).toFixed(1)} ${baseY} Z`;
  const fmtV = v => fmt ? fmt(v) : v.toFixed(1);
  // Grid ticks at whole-sigma steps from the mean, not arbitrary round
  // numbers -- so the grid actually reflects the distribution's own shape.
  const ticks = [-3,-2,-1,0,1,2,3].map(k => mean + k*sd);
  const sdBand = `<rect x="${x(mean-sd).toFixed(1)}" y="${padT}" width="${(x(mean+sd)-x(mean-sd)).toFixed(1)}" height="${baseY-padT}" fill="${color}" opacity="0.06"/>`;
  let ciRect = "", ciLabel = "";
  if(ciLow!==undefined && ciHigh!==undefined){
    ciRect = `<rect x="${x(ciLow).toFixed(1)}" y="${baseY-10}" width="${Math.max(2,x(ciHigh)-x(ciLow)).toFixed(1)}" height="10" fill="#10B981" opacity="0.9"/>`;
    const lowColor = ciLow>=0 ? "#10B981" : "#EF4444";
    const highColor = ciHigh>=0 ? "#10B981" : "#EF4444";
    ciLabel = `<div class="mono text-[13px] text-center mt-2">&mu; &isin; [<span style="color:${lowColor}">${fmtV(ciLow)}</span>, <span style="color:${highColor}">${fmtV(ciHigh)}</span>] <span class="text-[#9CA3AF]">(90% confidence)</span></div>`;
  }
  let zeroLine = "";
  if(zero!==undefined && zero>=min && zero<=max){
    zeroLine = `<line x1="${x(zero).toFixed(1)}" x2="${x(zero).toFixed(1)}" y1="${padT}" y2="${baseY}" stroke="#EF4444" stroke-width="1.25" stroke-dasharray="4 3"/>
      <text x="${x(zero).toFixed(1)}" y="${padT-4}" text-anchor="middle" font-size="10" fill="#EF4444" font-family="JetBrains Mono" font-weight="700">ZERO</text>`;
  }
  const svg = `<svg viewBox="0 0 ${W} ${H}" class="w-full" style="height:${H}px">
    ${ticks.map(t=>`<line x1="${x(t).toFixed(1)}" x2="${x(t).toFixed(1)}" y1="${padT}" y2="${baseY}" stroke="#1F2937" stroke-width="1"/>`).join("")}
    <line x1="${padL}" x2="${W-padR}" y1="${baseY}" y2="${baseY}" stroke="#374151" stroke-width="1.5"/>
    ${sdBand}
    <path d="${area}" fill="${color}" fill-opacity="0.12" stroke="none"/>
    <path d="${path}" fill="none" stroke="${color}" stroke-width="1.8"/>
    ${ciRect}
    ${zeroLine}
    <line x1="${x(mean).toFixed(1)}" x2="${x(mean).toFixed(1)}" y1="${yS(Math.exp(0)).toFixed(1)}" y2="${baseY}" stroke="#F9FAFB" stroke-width="1" stroke-dasharray="2 3" opacity="0.6"/>
    <circle cx="${x(mean).toFixed(1)}" cy="${yS(Math.exp(0)).toFixed(1)}" r="5" fill="#111827" stroke="#F9FAFB" stroke-width="2"/>
    <text x="${x(mean).toFixed(1)}" y="${(yS(Math.exp(0))-10).toFixed(1)}" text-anchor="middle" font-size="11" fill="#F9FAFB" font-family="JetBrains Mono" font-weight="700">MEAN ${fmtV(mean)}</text>
    ${ticks.map(t=>`<text x="${x(t).toFixed(1)}" y="${baseY+16}" text-anchor="middle" font-size="10" fill="#9CA3AF" font-family="JetBrains Mono">${fmtV(t)}</text>`).join("")}
    <text x="${padL-8}" y="${padT+4}" text-anchor="end" font-size="10" fill="#9CA3AF" font-family="JetBrains Mono">&#8593; likelihood</text>
  </svg>`;
  return svg + ciLabel;
}

let LAST_STATS = null;

function closeDistModal(){
  document.getElementById("dist-modal").classList.add("hidden");
  document.getElementById("dist-modal").classList.remove("flex");
}

function openDistModal(kind){
  if(!LAST_STATS) return;
  const s = LAST_STATS;
  document.addEventListener("keydown", function esc(e){ if(e.key==="Escape"){ closeDistModal(); document.removeEventListener("keydown", esc); } });
  const modal = document.getElementById("dist-modal");
  modal.classList.remove("hidden");
  modal.classList.add("flex");
  const title = document.getElementById("dist-modal-title");
  const body = document.getElementById("dist-modal-body");
  if(kind === "ci"){
    title.textContent = "90% Confidence Bound — expanded";
    const ciHigh = (s.mean_return_pct!==null && s.ci90_lower_pct!==null) ? (2*s.mean_return_pct - s.ci90_lower_pct) : undefined;
    body.innerHTML = `
      ${expandedBellCurveSvg({mean:s.mean_return_pct||0,stdev:s.stdev_return_pct,zero:0,ciLow:s.ci90_lower_pct,ciHigh,color:"#3B82F6",fmt:v=>v.toFixed(0)+"%"})}
      <div class="grid grid-cols-4 gap-2 mt-4 mono text-[12px]">
        <div class="border border-[#1F2937] p-2.5 text-center"><div class="text-[#9CA3AF] tracking-widest uppercase">Mean</div><div class="font-bold mt-1">${fmtPct(s.mean_return_pct)}</div></div>
        <div class="border border-[#1F2937] p-2.5 text-center"><div class="text-[#9CA3AF] tracking-widest uppercase">Stdev (<span class="normal-case">&sigma;</span>)</div><div class="font-bold mt-1">${s.stdev_return_pct===null?'--':s.stdev_return_pct.toFixed(1)+'%'}</div></div>
        <div class="border border-[#1F2937] p-2.5 text-center"><div class="text-[#9CA3AF] tracking-widest uppercase">n settled</div><div class="font-bold mt-1">${s.n_settled}</div></div>
        <div class="border border-[#1F2937] p-2.5 text-center" style="color:${(s.ci90_lower_pct||0)>0?'#10B981':'#EF4444'}"><div class="tracking-widest uppercase opacity-80">90% lower bound</div><div class="font-bold mt-1">${fmtPct(s.ci90_lower_pct,2)}</div></div>
      </div>
      <div class="mt-3 mono text-[12px] leading-5 text-[#9CA3AF] bg-[#090D16] border border-[#1F2937] px-3 py-2">Normal approximation fitted from the real mean and standard deviation of settled-market returns &mdash; illustrative of spread, not a histogram of individual outcomes. The shaded band is &plusmn;1&sigma;.</div>`;
  } else {
    title.textContent = "Markout Drift — expanded";
    const meanC = (s.markout_mean_per_share||0)*100;
    body.innerHTML = `
      ${expandedBellCurveSvg({mean:meanC,stdev:2,zero:0,color:"#EF4444",fmt:v=>v.toFixed(1)+"&cent;"})}
      <div class="grid grid-cols-3 gap-2 mt-4 mono text-[12px]">
        <div class="border border-[#1F2937] p-2.5 text-center"><div class="text-[#9CA3AF] tracking-widest uppercase">Mean drift</div><div class="font-bold mt-1 text-[#EF4444]">${meanC.toFixed(2)}&cent;</div></div>
        <div class="border border-[#1F2937] p-2.5 text-center"><div class="text-[#9CA3AF] tracking-widest uppercase">Effective n</div><div class="font-bold mt-1">${s.markout_n_eff.toFixed(1)}</div></div>
        <div class="border border-[#1F2937] p-2.5 text-center"><div class="text-[#9CA3AF] tracking-widest uppercase">Spread captured</div><div class="font-bold mt-1">${fmtUsd(s.markout_spread_usd)}</div></div>
      </div>
      <div class="mt-3 mono text-[12px] leading-5 text-[#9CA3AF] bg-[#090D16] border border-[#1F2937] px-3 py-2">Normal approximation centered on the pooled size-weighted mean drift per filled share &mdash; the &sigma;=2&cent; spread shown is illustrative (drift dispersion isn't separately tracked yet), the mean and n_eff are the real measured figures. The shaded band is &plusmn;1&sigma;.</div>`;
  }
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
  // Resting notional per side -- the only real size data this fleet has.
  // The venue book exposes prices only (mid/bid/ask), so the depth chart is
  // scaled by OUR resting quotes, not fabricated venue volume.
  const quotes = m.quotes || [];
  let up = 0, dn = 0;
  for(const o of quotes){
    const remaining = o.remaining===null||o.remaining===undefined ? Math.max(0,(o.size||0)-(o.filled||0)) : o.remaining;
    const notional = o.notional===null||o.notional===undefined ? (o.price||0)*remaining : o.notional;
    if(o.side==='UP') up+=notional; else dn+=notional;
  }
  const total = up+dn;
  const upShare = total>0 ? up/total : 0;
  const dnShare = total>0 ? dn/total : 0;
  let band = '<span class="mono text-[11px] text-[#9CA3AF]">No two-sided book</span>';
  if(mid!==null && bid!==null && ask!==null && mid!==undefined && bid!==undefined && ask!==undefined){
    const v = m.max_spread || 0.045;
    const half = Math.max(v*1.35, (ask-bid)*0.75, 0.01);
    const lo = mid-half, hi = mid+half, W = hi-lo;
    const x = p => Math.max(0, Math.min(100, 100*(p-lo)/W));
    const wl = x(mid-v), wr = x(mid+v);
    const mark = (p,color,w,glow) => (p===null||p===undefined) ? "" :
      `<span style="position:absolute;left:${x(p)}%;top:2px;bottom:2px;width:${w}px;background:${color};transform:translateX(-50%)${glow?';box-shadow:0 0 0 1px #090D16,0 0 5px rgba(251,191,36,.45)':''}"></span>`;
    // Micro depth behind the price levels: the YES half (left of mid) gets a
    // soft green wash and the NO half a soft red-brown one, each scaled by
    // that side's share of resting notional. The gold mid marker overlays
    // dead center with a dark outline; the bright MID badge sits beneath.
    const depth = total>0 ? `
      <div style="position:absolute;left:0;top:0;bottom:0;width:${(50*upShare).toFixed(1)}%;background:rgba(16,185,129,.13)"></div>
      <div style="position:absolute;right:0;top:0;bottom:0;width:${(50*dnShare).toFixed(1)}%;background:rgba(239,68,68,.12)"></div>` : "";
    band = `<div style="position:relative;height:30px;width:100%;max-width:220px">
      ${depth}
      <div style="position:absolute;left:${wl}%;width:${Math.max(0,wr-wl)}%;top:11px;height:8px;background:#10B98122;border-left:1px solid #10B98155;border-right:1px solid #10B98155"></div>
      <div style="position:absolute;left:0;right:0;top:15px;height:1px;background:#1F2937"></div>
      ${mark(mid,'#FBBF24',3,true)}
      ${mark(bid,'#9CA3AF',1.5)}${mark(ask,'#9CA3AF',1.5)}
      ${mark(m.our_up,'#3B82F6',2)}
      ${mark(m.our_dn_as_up,'#EF4444',2)}
      <span style="position:absolute;left:${x(mid)}%;top:20px;transform:translateX(-50%);white-space:nowrap;background:#090D16;border:1px solid rgba(255,255,255,.2);padding:0 4px" class="mono text-[10px] font-bold text-[#FBBF24]">MID ${mid.toFixed(3)}</span>
    </div>`;
  }
  // Dollar resting figures -- once per market. The wash above is the depth
  // chart; this line is the money behind it. (Full quote detail lives in the
  // market drawer.)
  let cap = '<div class="mono text-[10px] text-[#9CA3AF] mt-1">No capital resting</div>';
  if(total>0){
    cap = `<div class="mono text-[10px] mt-1 flex items-center justify-between max-w-[220px]"><span class="text-[#10B981]">$${up.toFixed(0)} YES</span><span class="text-[#9CA3AF]/60">resting</span><span class="text-[#EF4444]">$${dn.toFixed(0)} NO</span></div>`;
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
        <div class="mono text-[44px] font-bold leading-none tracking-[-0.03em]" data-kpi="hero_realized" data-v="${s.realized_usd}" data-rollup data-fmt="usd">${fmtPnlHTML(s.realized_usd)}</div>
        <div class="mono text-[14px] font-semibold px-2 py-1 bg-[#10B981]/10 border border-[#10B981]/20 text-[#10B981]">${fmtPct(s.realized_pct)}</div>
        <div class="mono text-[13px] text-[#9CA3AF]">on ${s.realized_cost.toFixed(0)} committed</div>
      </div>
      <div class="mt-3 mono text-[12px] leading-5 text-[#9CA3AF] bg-[#090D16] border border-[#1F2937] px-3 py-2">
        ${fmtUsd(s.realized_usd)} Realized &nbsp;|&nbsp; ${fmtUsd(s.rebate_usd)} Earned Rebates &nbsp;|&nbsp; ${fmtUsd(s.unrealized_usd)} Unrealized &nbsp;=&nbsp; <span class="font-bold text-[#F9FAFB]">${fmtUsd(s.total_liquidation_usd)} Total Liquidation P&amp;L</span>
      </div>
      <div class="mt-3 grid grid-cols-2 gap-2">
        <div class="p-3.5 bg-[#111827] border border-[#1F2937]"><div class="mono text-[12px] tracking-[0.12em] uppercase text-[#9CA3AF]">Total closes</div><div class="mono text-[17px] font-bold mt-1">${s.closes}</div><div class="mono text-[12px] text-[#9CA3AF] mt-0.5">${fmtUsd(s.closed_pnl)} booked</div></div>
        <div class="p-3.5 bg-[#10B981]/10 border border-[#10B981]/20"><div class="mono text-[12px] tracking-[0.12em] uppercase text-[#10B981]">Rebates earned</div><div class="mono text-[17px] font-bold mt-1 text-[#10B981]">${fmtUsd(s.rebate_usd)}</div><div class="mono text-[12px] text-[#10B981]/70 mt-0.5">${s.rebate_fills} fills${s.rebate_cps===null?'':' &middot; '+s.rebate_cps.toFixed(2)+'c/sh'}</div></div>
      </div>
    </div>
    <div class="col-span-12 lg:col-span-6 p-6 lg:p-7 relative border-l border-[#1F2937]">
      <div class="absolute top-0 left-0 w-full h-[2px] bg-[#3B82F6]"></div>
      <div class="mono text-[12px] tracking-[0.16em] uppercase text-[#3B82F6] font-semibold flex items-center gap-2"><span class="size-1.5 bg-[#3B82F6]"></span> Unrealized P&amp;L &mdash; Open Positions</div>
      <div class="mt-6 flex items-baseline gap-3">
        <div class="mono text-[44px] font-bold leading-none tracking-[-0.03em]" data-kpi="hero_unrealized" data-v="${s.unrealized_usd}" data-rollup data-fmt="usd">${fmtPnlHTML(s.unrealized_usd)}</div>
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
     tip: `Formula: mean &minus; 1.645&middot;&sigma;/&radic;n_eff &mdash; the 90% one-sided lower bound on the pooled size-weighted mean drift. Current: ${fmtPct(s.ci90_lower_pct,2)}. Gate: must clear 0% for a GO call.`,
     chart: `<div class="cursor-pointer" title="Click to expand" onclick="openDistModal('ci')">${bellCurveSvg({min:-100,max:100,mean:s.mean_return_pct||0,stdev:s.stdev_return_pct,zero:0,
       ciLow:s.ci90_lower_pct,ciHigh:(s.mean_return_pct!==null&&s.ci90_lower_pct!==null)?(2*s.mean_return_pct-s.ci90_lower_pct):undefined,
       color:"#3B82F6",w:140,h:64})}</div>`,
     sub:`Mean ${fmtPct(s.mean_return_pct)} &middot; &sigma; ${s.stdev_return_pct===null?'--':s.stdev_return_pct.toFixed(1)+'%'}`},
    {label:"Markout Drift", value: s.markout_mean_per_share===null?"--":(s.markout_mean_per_share*100).toFixed(2)+"&cent;", accent:"#EF4444",
     tip: `Formula: size-weighted mean of (reference mid &minus; fill mid) per filled share, in cents. Current: ${s.markout_mean_per_share===null?"--":(s.markout_mean_per_share*100).toFixed(2)+"&cent;"}. Negative = adverse selection: fills systematically arrive against us.`,
     chart: `<div class="cursor-pointer" title="Click to expand" onclick="openDistModal('markout')">${bellCurveSvg({min:-6,max:6,mean:(s.markout_mean_per_share||0)*100,stdev:2,zero:0,color:"#EF4444",w:140,h:64})}</div>`,
     sub:`n_eff ${s.markout_n_eff.toFixed(1)} &middot; measured`},
    {label:"Weighting Gap", value: weightGap===null?"--":weightGap.toFixed(1)+" pp", accent:"#F59E0B",
     tip: `Formula: equal-weighted mean return &minus; cash-weighted realized return, in percentage points. Current: ${weightGap===null?"--":weightGap.toFixed(1)+" pp"}. A wide gap means per-settlement weighting changes the picture.`,
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
  </div>`;
  const right = `<div class="col-span-12 lg:col-span-9 grid grid-cols-1 md:grid-cols-3 lg:grid-cols-5 divide-y md:divide-y-0 md:divide-x divide-[#1F2937]">
    ${tiles.map((t,i)=>`<div class="p-3 flex flex-col gap-2 hover:bg-[#090D16] transition-colors">
      <div class="mono text-[12px] tracking-[0.14em] uppercase text-[#9CA3AF] flex items-center">0${i+1} &mdash; ${t.label}${t.tip?tip(t.label,t.tip):""}</div>
      <div class="mono text-[19px] font-bold leading-none px-2 py-1.5 w-fit border" style="color:${t.accent};background:${t.accent}1A;border-color:${t.accent}33">${t.value}</div>
      ${t.chart}
      <div class="mono text-[12px] leading-4 text-[#9CA3AF]">${t.sub}</div>
    </div>`).join("")}
  </div>`;
  document.getElementById("sec-verdict").innerHTML = left + right;
}

function renderGauges(s){
  // Readiness instruments. Settlement progress is not repeated here:
  // the settled sample's home is the Verdict panel (Sample tile + note).
  const items = [
    {label:"Capital Committed", sub:`${s.committed_open_usd.toFixed(0)} of ${s.max_committed_usd.toFixed(0)}`, value:s.committed_open_usd, max:s.max_committed_usd, color:"#3B82F6"},
    {label:"Markout Coverage", sub:`${s.markout_n_eff.toFixed(1)} fills &middot; threshold ${s.markout_min_sample}`, value:s.markout_n_eff, max:Math.max(s.markout_min_sample*2, s.markout_n_eff), color:"#10B981", threshold:s.markout_min_sample,
     tip: `Kish's effective sample size &mdash; (&Sigma;w)&sup2;/&Sigma;w&sup2;. Current: ${s.markout_n_eff.toFixed(1)}. A size-weighted sample behaves like this many equal rows; the gate requires n_eff &ge; ${s.markout_min_sample}.`},
  ];
  // Naked-USD capacity bar: the fleet-wide sum of per-market naked_cost
  // against the $120 hard cap (strategy/config.py max_naked_usd). Utilization
  // bands: <50% soft green, 50-80% amber, >=80% pulsing red + HIGH EXPOSURE.
  const nakedUsd = s.naked_usd || 0;
  const nakedCap = s.max_naked_usd || 0;
  const nakedPct = nakedCap > 0 ? Math.min(100, 100*nakedUsd/nakedCap) : 0;
  const barColor = nakedPct >= 80 ? "#EF4444" : nakedPct >= 50 ? "#F59E0B" : "#10B981";
  const naked = `
    <div class="bg-[#111827] border border-[#1F2937] p-5 lg:col-span-2">
      <div class="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <div class="mono text-[12px] tracking-[0.14em] uppercase text-[#9CA3AF] flex items-center">Naked USD Exposure${tip("Naked USD Exposure", `Formula: &Sigma; per-market naked_cost &mdash; USD in the unhedged leg, valued at average cost &mdash; against the ${nakedCap.toFixed(0)} hard cap (max_naked_usd). Current: $${nakedUsd.toFixed(2)} (${nakedPct.toFixed(1)}% of cap). Utilization bands: &lt;50% soft green &middot; 50&ndash;80% amber &middot; &ge;80% pulsing red HIGH EXPOSURE.`)}</div>
          <div class="mt-2 flex items-baseline gap-2.5 flex-wrap">
            <span class="mono text-[26px] font-bold leading-none tracking-tight" data-kpi="naked_usd" data-v="${nakedUsd.toFixed(2)}" data-fmt="usd">$${nakedUsd.toFixed(2)}</span>
            <span class="mono text-[13px] text-[#9CA3AF]">of $${nakedCap.toFixed(0)} hard cap</span>
            ${nakedPct >= 80 ? `<span class="mono text-[11px] font-bold tracking-widest px-2 py-1 bg-[#EF4444] text-white border border-[#EF4444]">HIGH EXPOSURE</span>` : ""}
          </div>
        </div>
        <span class="mono text-[12px] tracking-widest px-2 py-1 border font-semibold shrink-0" style="color:${barColor};background:${barColor}1A;border-color:${barColor}33">${nakedPct.toFixed(1)}%</span>
      </div>
      <div class="mt-4 h-1.5 w-full bg-[#090D16] overflow-hidden"><div class="h-full ${nakedPct>=80?'warn-bar':''}" style="width:${nakedPct}%;background:${barColor}"></div></div>
      <div class="mt-3 mono text-[13px] leading-5 text-[#9CA3AF] border-l-2 pl-3" style="border-color:${barColor}">Sum of naked cost across open markets &mdash; in DOLLARS of unhedged exposure. The $120 cap is per market; this is the fleet-wide view of the same number.</div>
    </div>`;
  document.getElementById("sec-gauges").innerHTML = items.map(g => {
    const pct = Math.min(100, Math.max(0,(g.value/g.max)*100));
    return `<div class="bg-[#111827] border border-[#1F2937] p-5 flex flex-col">
      <div class="flex items-start justify-between gap-3">
        <div><div class="mono text-[12px] tracking-[0.14em] uppercase text-[#9CA3AF] flex items-center">${g.label}${g.tip?tip(g.label,g.tip):""}</div><div class="mono text-[13px] tracking-tight mt-0.5">${g.sub}</div></div>
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
          <div class="mono text-[13px] leading-5 text-[#9CA3AF] border-l-2 pl-3" style="border-color:${g.color}">${g.label==='Capital Committed'?'Cash committed to resting offers and held inventory.':'Effective sample vs the gate threshold this fleet actually uses.'}</div>
        </div>
      </div>
    </div>`;
  }).join("") + naked;
}

function renderEvidence(s){
  document.getElementById("sec-evidence").innerHTML = `
    <div class="border border-[#1F2937] bg-[#111827] overflow-hidden">
      <div class="px-3.5 h-9 flex items-center gap-2 mono text-[13px] tracking-[0.14em] uppercase font-semibold border-b border-[#1F2937]">Performance</div>
      <div class="p-3 space-y-3">
        <div class="bg-[#111827] border border-[#1F2937] p-3 cursor-pointer hover:border-[#3B82F6]/40 transition-colors" title="Click to expand" onclick="openDistModal('ci')">
          <div class="flex items-center justify-between mono text-[12px] tracking-[0.14em] uppercase text-[#9CA3AF]"><span>90% Confidence vs zero</span><span class="text-[#9CA3AF]/60">Expand &nearr;</span></div>
          ${bellCurveSvg({min:-100,max:100,mean:s.mean_return_pct||0,stdev:s.stdev_return_pct,zero:0,color:"#3B82F6"})}
        </div>
        <div class="bg-[#111827] border border-[#1F2937] p-3 cursor-pointer hover:border-[#EF4444]/40 transition-colors" title="Click to expand" onclick="openDistModal('markout')">
          <div class="flex items-center justify-between mono text-[12px] tracking-[0.14em] uppercase text-[#9CA3AF]"><span>Markout drift</span><span class="px-2 py-1 border text-[#EF4444] border-[#EF444433]">Mean ${s.markout_mean_per_share===null?'--':(s.markout_mean_per_share*100).toFixed(2)+'c'}</span></div>
          ${bellCurveSvg({min:-6,max:6,mean:(s.markout_mean_per_share||0)*100,stdev:2,zero:0,color:"#EF4444"})}
        </div>
        <div class="grid grid-cols-3 gap-2">
          <div class="bg-[#10B981]/10 border border-[#10B981]/20 p-3"><div class="mono text-[12px] tracking-[0.14em] uppercase text-[#10B981]">Realized</div><div class="mono text-[18px] font-bold text-[#10B981] mt-1">${fmtPnlHTML(s.realized_usd)}</div></div>
          <div class="bg-[#111827] border border-[#1F2937] p-3"><div class="mono text-[12px] tracking-[0.14em] uppercase text-[#9CA3AF]">Mean / Stdev</div><div class="mono text-[15px] font-bold mt-1">${fmtPctHTML(s.mean_return_pct)} / ${s.stdev_return_pct===null?'--':s.stdev_return_pct.toFixed(1)+'%'}</div></div>
          <div class="bg-[#3B82F6]/10 border border-[#3B82F6]/20 p-3"><div class="mono text-[12px] tracking-[0.14em] uppercase text-[#3B82F6]">Unrealized</div><div class="mono text-[18px] font-bold mt-1 text-[#3B82F6]">${fmtPnlHTML(s.unrealized_usd)}</div></div>
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
            <div class="bg-[#111827] border border-[#1F2937] p-2.5 text-center"><div class="mono text-[12px] tracking-widest uppercase text-[#9CA3AF]">Calendar</div><div class="mono text-[17px] font-bold ${(s.calendar_days||0)<s.go_live_min_calendar_days?'text-[#EF4444]':'text-[#10B981]'}">${s.calendar_days===null?'--':s.calendar_days.toFixed(1)} / ${s.go_live_min_calendar_days} d</div></div>
          </div>
        </div>
      </div>
    </div>
    <div class="border border-[#1F2937] bg-[#111827] overflow-hidden">
      <div class="px-3.5 h-9 flex items-center gap-2 mono text-[13px] tracking-[0.14em] uppercase font-semibold border-b border-[#1F2937]">Risk</div>
      <div class="p-3 grid grid-cols-3 gap-2">
        <div class="bg-[#111827] border border-[#1F2937] p-3 text-center"><div class="mono text-[12px] tracking-widest uppercase text-[#9CA3AF]">Wins</div><div class="mono text-[17px] font-bold text-[#10B981]">${s.wins}</div></div>
        <div class="bg-[#111827] border border-[#1F2937] p-3 text-center"><div class="mono text-[12px] tracking-widest uppercase text-[#9CA3AF]">Losses</div><div class="mono text-[17px] font-bold text-[#EF4444]">${s.losses}</div></div>
        <div class="bg-[#111827] border border-[#1F2937] p-3 text-center"><div class="mono text-[12px] tracking-widest uppercase text-[#9CA3AF]">Win rate</div><div class="mono text-[17px] font-bold" data-kpi="win_rate" data-v="${(s.wins+s.losses)?(100*s.wins/(s.wins+s.losses)).toFixed(1):'--'}" data-rollup data-fmt="pct">${(s.wins+s.losses)?(100*s.wins/(s.wins+s.losses)).toFixed(1):'--'}%</div></div>
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
  LAST_MARKETS = rows;
  // Phase-4 table: quick-filter bar + classified action pills. Rows stay
  // clickable (market drawer); P&L and status cells keep data-kpi/data-state
  // so animateChanges() still flashes and transitions on poll changes.
  const withCls = rows.map(r => ({ r, cls: classifyStatus(r) }));
  const shown = withCls.filter(({r, cls}) => marketMatches(r, cls));
  // Category chips derive from the categories actually present right now --
  // the taxonomy is slug-derived, so a hardcoded sport enum would silently
  // strand a new category with no way to reach it.
  const cats = ["all", ...new Set(rows.map(r => r.category).filter(Boolean))];
  const chips = `<div class="flex flex-wrap items-center gap-1.5 px-3 py-2.5 border-b border-[#1F2937] bg-[#090D16]/60">
    <span class="mono text-[11px] tracking-[0.18em] uppercase text-[#9CA3AF] mr-1">Filter</span>
    ${cats.map(c => filterChip(FILTERS.cat === c, `data-fcat="${escAttr(c)}"`, c === "all" ? "All Categories" : c)).join("")}
    <span class="w-px h-4 bg-[#1F2937] mx-1.5 shrink-0"></span>
    ${STATE_FILTERS.map(s => filterChip(FILTERS.state === s.id, `data-fst="${s.id}"`, s.label)).join("")}
    ${(FILTERS.cat !== "all" || FILTERS.state !== "all") ? `<button type="button" data-fclear class="ml-auto h-6 px-2 mono text-[11px] tracking-[0.14em] uppercase text-[#F59E0B] border border-[#92400E] hover:bg-[#451A03]/40 transition-colors">Clear</button>` : ""}
  </div>`;
  const bodyRows = shown.map(({r, cls}) => `<tr class="hover:bg-[#1F2937] transition-colors cursor-pointer" data-drawer="${escAttr(r.market)}">
    <td class="px-3 py-2.5">
      <div class="font-medium max-w-[280px] truncate text-[13px]"><a href="https://polymarket.com/event/${esc(r.market)}" target="_blank" class="hover:underline text-blue-400" onclick="event.stopPropagation()">${formatMarketTitle(r.market)}</a></div>
      <div class="mt-1">${getCategoryTag(r.category)}</div>
    </td>
    <td class="px-3 py-2.5">${orderDepthHtml(r)}</td>
    <td class="px-3 py-2.5 text-right font-semibold text-[13px]">$${r.committed.toFixed(0)}</td>
    <td class="px-3 py-2.5 text-right" data-kpi="u_${escAttr(r.market)}" data-v="${r.unrealized}">${badgePnlPctHTML(r.unrealized, r.unrealized_pct)}</td>
    <td class="px-3 py-2.5 text-right" data-kpi="r_${escAttr(r.market)}" data-v="${r.closes ? r.realized : 'n/a'}">${r.closes ? badgePnlPctHTML(r.realized, r.realized_pct) : `<span class="text-[#9CA3AF]">--</span>`}</td>
    <td class="px-3 py-2.5 text-center"><span class="px-2 py-1 text-[13px] font-bold border border-[#1F2937]">${r.fills}</span></td>
    <td class="px-3 py-2.5" data-state="${escAttr(r.market)}" data-v="${escAttr(cls.bucket)}">
      <div class="flex flex-col gap-0.5 items-start min-w-[120px]">
        ${getStatePill(cls)}
        ${cls.bucket === "BLOCKED" ? `<span class="mono text-[9px] font-bold tracking-[0.18em] text-[#FBBF24]/80">${esc(r.code && r.code !== "OTHER" ? r.code : "RISK_GATE")}</span>` : stateDots(r.events)}
        ${ageBadgeHtml(r.ts)}
      </div>
    </td>
  </tr>`).join("");
  const empty = !rows.length
    ? `<tr><td colspan="8" class="px-4 py-6 text-center mono text-[13px] text-[#9CA3AF]">No active markets right now.</td></tr>`
    : !shown.length
      ? `<tr><td colspan="8" class="px-4 py-6 text-center mono text-[13px] text-[#9CA3AF]">No markets match the current filters.</td></tr>`
      : bodyRows;
  const table = `<div class="overflow-x-auto"><table class="w-full text-left border-collapse">
    <thead><tr class="bg-[#090D16] mono text-[12px] tracking-[0.14em] uppercase border-b border-[#1F2937]">
      <th class="px-3 py-2.5">Market</th><th class="px-3 py-2.5">Order Depth / Mid</th><th class="px-3 py-2.5 text-right">Commit</th>
      <th class="px-3 py-2.5 text-right">Unrealized P&amp;L</th><th class="px-3 py-2.5 text-right">Realized P&amp;L</th><th class="px-3 py-2.5 text-center">Fills</th><th class="px-3 py-2.5">Status</th>
    </tr></thead>
    <tbody class="mono text-[14px] divide-y divide-[#1F2937]">${empty}</tbody></table></div>
    <div class="px-4 py-2.5 border-t border-[#1F2937] mono text-[12px] tracking-widest uppercase text-[#9CA3AF]">${shown.length} of ${rows.length} active markets &middot; click a row for the market detail drawer &middot; Realized = already-booked P&amp;L from partial closes &middot; Unrealized values are estimates only</div>`;
  document.getElementById("tab-markets").innerHTML = chips + table;
}

let settledState = {
  rows: [],
  totalCloses: 0,
  grouped: [],
  page: 0,
  perPage: 10,
  expanded: {}
};

function renderSettled(rows, totalCloses) {
  settledState.rows = rows;
  settledState.totalCloses = totalCloses;
  
  const groups = {};
  for (const r of rows) {
    if (!groups[r.market]) {
      groups[r.market] = { market: r.market, category: r.category, exits: [], total_pnl: 0, methods: new Set(), total_shares: 0, total_cost_basis: 0 };
    }
    groups[r.market].exits.push(r);
    groups[r.market].total_pnl += r.pnl;
    groups[r.market].methods.add(r.method);
    groups[r.market].total_shares += (r.shares || 0);
    groups[r.market].total_cost_basis += (r.cost_basis || 0);
  }
  
  const orderedGroups = [];
  const seen = new Set();
  for (const r of rows) {
    if (!seen.has(r.market)) {
      seen.add(r.market);
      const g = groups[r.market];
      // Grouped return derived from the aggregate P&L and grouped cost basis,
      // not a mean of per-exit percentages (which is only equal when every
      // exit shares the same cost basis).
      g.avg_pnl_pct = g.total_cost_basis > 0
        ? 100 * g.total_pnl / g.total_cost_basis
        : null;
      g.avg_cost = g.total_shares > 0 ? (g.total_cost_basis / g.total_shares) : null;
      g.win = g.total_pnl > 0;
      g.method = g.methods.size === 1 ? [...g.methods][0] : "MIXED";
      orderedGroups.push(g);
    }
  }
  settledState.grouped = orderedGroups;
  
  renderSettledTable();
}

window.toggleMarketExpand = function(market) {
  settledState.expanded[market] = !settledState.expanded[market];
  renderSettledTable();
};

window.setSettledPage = function(p) {
  settledState.page = p;
  renderSettledTable();
};

window.setSettledPerPage = function(pp) {
  settledState.perPage = pp;
  settledState.page = 0;
  renderSettledTable();
};

const TAG_BASE_CLASS = "inline-flex items-center px-2 py-0.5 text-[11px] font-bold tracking-widest uppercase border whitespace-nowrap rounded-sm";
const TAG_DEFAULT_COLOR = "bg-[#090D16] text-[#D1D5DB] border-[#1F2937]/75";

function renderTag(icon, text, colors = TAG_DEFAULT_COLOR) {
  return `<span class="${TAG_BASE_CLASS} ${colors}">${icon} ${esc(text)}</span>`;
}

function getMethodTag(method) {
  let icon = `<svg class="w-3.5 h-3.5 mr-1.5 inline-block text-[#9CA3AF]" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>`;
  if (method === "MERGE") icon = `<svg class="w-3.5 h-3.5 mr-1.5 inline-block text-[#3B82F6]" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7v8a2 2 0 002 2h6M8 7l-3 3m3-3l3 3m4-4h.01M16 11h.01M16 15h.01" /></svg>`;
  else if (method === "RESOLVE") icon = `<svg class="w-3.5 h-3.5 mr-1.5 inline-block text-[#10B981]" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" /></svg>`;
  return renderTag(icon, method);
}

function getCategoryTag(cat) {
  let icon = `<svg class="w-3.5 h-3.5 mr-1.5 inline-block text-[#9CA3AF]" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A1.994 1.994 0 013 12V7a4 4 0 014-4z" /></svg>`;
  
  if (cat.startsWith("Sports")) icon = `<svg class="w-3.5 h-3.5 mr-1.5 inline-block text-[#3B82F6]" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 3v4M3 5h4M21 5h-4M19 3v4M7 3h10v4a5 5 0 01-10 0V3z M12 17v4 M8 21h8" /></svg>`;
  else if (cat.startsWith("E-Sports")) icon = `<svg class="w-3.5 h-3.5 mr-1.5 inline-block text-[#8B5CF6]" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" /></svg>`;
  else if (cat.startsWith("Politics")) icon = `<svg class="w-3.5 h-3.5 mr-1.5 inline-block text-[#EF4444]" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 14v3m4-3v3m4-3v3M3 21h18M3 10h18M3 7l9-4 9 4M4 10h16v11H4V10z" /></svg>`;
  else if (cat.startsWith("Crypto")) icon = `<svg class="w-3.5 h-3.5 mr-1.5 inline-block text-[#F59E0B]" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>`;
  else if (cat.startsWith("Pop Culture")) icon = `<svg class="w-3.5 h-3.5 mr-1.5 inline-block text-[#EC4899]" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11.049 2.927c.3-.921 1.603-.921 1.902 0l1.519 4.674a1 1 0 00.95.69h4.915c.969 0 1.371 1.24.588 1.81l-3.976 2.888a1 1 0 00-.363 1.118l1.518 4.674c.3.922-.755 1.688-1.538 1.118l-3.976-2.888a1 1 0 00-1.176 0l-3.976 2.888c-.783.57-1.838-.197-1.538-1.118l1.518-4.674a1 1 0 00-.363-1.118l-3.976-2.888c-.784-.57-.38-1.81.588-1.81h4.914a1 1 0 00.951-.69l1.519-4.674z" /></svg>`;
  else if (cat.startsWith("Business")) icon = `<svg class="w-3.5 h-3.5 mr-1.5 inline-block text-[#10B981]" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" /></svg>`;
  
  return renderTag(icon, cat);
}

function formatAge(sec) {
  if (sec === null || sec === undefined) return "";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

// ---------- phase-4: market-table states, lifecycle, filters ----------
// The table classifies each market's CURRENT posture into the operator's
// four action buckets (the same vocabulary the market_events telemetry
// uses), with the exact color coding from the brief: QUOTING soft blue,
// FILLED neon green, BLOCKED muted amber with the gate refusal code below
// in micro-text, MERGED electric purple. The classification reads the real
// fleet row (err/why, paired/naked_sh, quotes) -- never the display string.
const STATE_PILL = {
  QUOTING:  { cls: "bg-[#172554] text-[#60A5FA] border-[#1E40AF]", label: "Quoting" },
  FILLED:   { cls: "bg-[#022C22] text-[#34D399] border-[#065F46]", label: "Filled" },
  BLOCKED:  { cls: "bg-[#451A03] text-[#FBBF24] border-[#92400E]", label: "Blocked" },
  MERGED:   { cls: "bg-[#3B0764] text-[#C084FC] border-[#6B21A8]", label: "Merged" },
  CLOSED:   { cls: "bg-[#0B111E] text-[#94A3B8] border-[#1F2937]", label: "Closed" },
  INACTIVE: { cls: "bg-[#0B111E] text-[#94A3B8] border-[#1F2937]", label: "Inactive" },
};

function classifyStatus(r){
  if (r.err || r.why) return { bucket: "BLOCKED", label: "Blocked" };
  if ((r.paired||0) > 0 && (r.naked_sh||0) > 0) return { bucket: "FILLED", label: "Filled" };
  if ((r.paired||0) > 0) return { bucket: "MERGED", label: "Merged" };
  if ((r.naked_sh||0) > 0) return { bucket: "FILLED", label: "Filled" };
  if ((r.quotes||[]).length) return { bucket: "QUOTING", label: "Quoting" };
  if (r.merge_why) return { bucket: "MERGED", label: "Merged" };
  if (r.close_why) return { bucket: "CLOSED", label: "Closed" };
  return { bucket: "INACTIVE", label: "Inactive" };
}

function getStatePill(cls){
  const s = STATE_PILL[cls.bucket] || STATE_PILL.INACTIVE;
  return `<span class="mono text-[11px] font-bold tracking-[0.14em] uppercase px-2 py-0.5 border ${s.cls}">${s.label}</span>`;
}

// Lifecycle dots: the market's up-to-3 most recent PERSISTED events from the
// market_events telemetry (kind + reason_code + ts), not anything fabricated
// client-side. One colored dot per event, with the detail in the title.
const EVENT_COLOR = { QUOTING:"#60A5FA", FILLED:"#34D399", HEDGED:"#C084FC",
  MERGED:"#C084FC", EXITED:"#94A3B8", BLOCKED:"#FBBF24", WAITING:"#94A3B8", ERROR:"#EF4444" };

function stateDots(events){
  if (!events || !events.length) return "";
  const dots = events.slice(0, 3);
  const tip = dots.map(e => `${e.kind}${e.reason_code && e.reason_code !== "OTHER" ? " · " + e.reason_code : ""} · ${fmtClock(e.ts)}`).join(" | ");
  return `<span class="flex items-center gap-1 mt-1" title="${esc(tip)}">` +
    dots.map(e => `<span class="size-1.5 rounded-full" style="background:${EVENT_COLOR[e.kind] || "#94A3B8"}"></span>`).join("") + `</span>`;
}

// Quick filter bar state. `state === "HOLD"` means any market holding
// inventory (Filled or Merged) -- the operator's "Has Active Inventory".
let FILTERS = { cat: "all", state: "all" };
const STATE_FILTERS = [
  { id: "all",     label: "All States" },
  { id: "QUOTING", label: "Actively Quoting" },
  { id: "BLOCKED", label: "Blocked by Risk" },
  { id: "HOLD",    label: "Has Active Inventory" },
];

function marketMatches(r, cls){
  if (FILTERS.cat !== "all" && r.category !== FILTERS.cat) return false;
  if (FILTERS.state === "all") return true;
  if (FILTERS.state === "HOLD") return cls.bucket === "FILLED" || cls.bucket === "MERGED";
  return cls.bucket === FILTERS.state;
}

function filterChip(active, attrs, label){
  return `<button type="button" ${attrs} class="h-6 px-2.5 mono text-[11px] font-bold tracking-[0.14em] uppercase border transition-colors ${active?"bg-[#10B981] text-white border-[#10B981]":"bg-[#090D16] text-[#94A3B8] border-[#1F2937] hover:text-[#F9FAFB] hover:border-[#10B981]/50"}">${esc(label)}</button>`;
}

// Age badge: seconds since this market's last telemetry update, ticked live
// by the pulse ticker. Past 60s the row dims and the badge reads STALE.
function ageBadgeHtml(ts){
  if (ts === null || ts === undefined) return `<span class="mono text-[10px] text-[#9CA3AF]">age --</span>`;
  return `<span data-age="${ts}" class="mono text-[10px] text-[#9CA3AF]">&hellip;</span>`;
}

function tickAgeBadges(){
  const now = Date.now()/1000;
  document.querySelectorAll("[data-age]").forEach(el => {
    const ts = parseFloat(el.getAttribute("data-age"));
    if (!isFinite(ts)) return;
    const secs = Math.max(0, Math.round(now - ts));
    const stale = secs >= 60;
    el.textContent = stale ? `STALE ${Math.floor(secs/60)}m` : `${secs}s ago`;
    el.classList.toggle("text-[#F59E0B]", stale);
    el.classList.toggle("text-[#9CA3AF]", !stale);
    const tr = el.closest("tr");
    if (tr) tr.style.opacity = stale ? "0.6" : "";
  });
}

function getStatusTag(status) {
  if (!status) return "";
  let icon = `<svg class="w-3.5 h-3.5 mr-1.5 inline-block text-[#9CA3AF]" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>`;
  let bg = `bg-[#090D16] text-[#9CA3AF] border-[#1F2937]/50`;
  
  if (status.includes("Orders resting") || status.includes("Quoting")) {
    icon = `<svg class="w-3.5 h-3.5 mr-1.5 inline-block" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>`;
    bg = `bg-[#10B981]/10 text-[#10B981] border-[#10B981]/20`; 
  } else if (status.includes("side filled")) {
    icon = `<svg class="w-3.5 h-3.5 mr-1.5 inline-block" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>`;
    bg = `bg-[#F59E0B]/10 text-[#F59E0B] border-[#F59E0B]/20`;
  } else if (status.includes("Paired")) {
    icon = `<svg class="w-3.5 h-3.5 mr-1.5 inline-block" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>`;
    bg = `bg-[#10B981]/10 text-[#10B981] border-[#10B981]/20`;
  } else if (status.includes("ERROR") || status.includes("blocked")) {
    icon = `<svg class="w-3.5 h-3.5 mr-1.5 inline-block" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg>`;
    bg = `bg-[#EF4444]/10 text-[#EF4444] border-[#EF4444]/20`;
  }
  return renderTag(icon, status, bg);
}

function formatMarketTitle(slug) {
  if (!slug) return "";
  
  let parts = slug.split('-');
  if (parts.length === 1) return slug;
  
  if (parts.length >= 3) {
    const last3 = parts.slice(-3).join('-');
    if (/^\d{4}-\d{2}-\d{2}$/.test(last3)) {
      parts = parts.slice(0, -3);
    }
  }
  
  const cat = parts[0];
  const knownCats = ["mlb", "atp", "wta", "nfl", "nba", "cs2", "lol", "pol", "nhl", "soccer", "fifa", "ufc", "boxing", "f1", "tennis", "golf", "csgo", "dota", "dota2", "val", "valorant", "esports", "politics", "election", "pres", "senate", "gop", "dem", "crypto", "btc", "eth", "sol", "defi", "nft", "pop", "culture", "oscars", "grammys", "movie", "boxoffice", "biz", "econ", "finance", "fed"];
  if (knownCats.includes(cat)) {
    parts.shift();
    if (cat === "lol" && parts.length > 0 && ["lck", "lpl", "lcs", "lec"].includes(parts[0])) {
      parts.shift();
    }
  }
  
  if (parts.length === 2) {
    return parts[0].toUpperCase() + " vs " + parts[1].toUpperCase();
  }
  
  return parts.map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
}  function renderSettledTable() {
  const { grouped, perPage, expanded, totalCloses } = settledState;
  // The grouped list can shrink between refreshes; clamp the page so a
  // poll never strands the table on an empty page past the last one.
  let { page } = settledState;
  const totalPages = Math.ceil(grouped.length / perPage);
  if (page >= totalPages) {
    page = Math.max(0, totalPages - 1);
    settledState.page = page;
  }
  const start = page * perPage;
  const shown = grouped.slice(start, start + perPage);
  
  const table = `<div class="overflow-x-auto"><table class="w-full text-left border-collapse">
    <thead><tr class="bg-[#090D16] mono text-[12px] tracking-[0.14em] uppercase border-b border-[#1F2937]">
      <th class="px-3 py-2.5">Method</th><th class="px-3 py-2.5">Market</th><th class="px-3 py-2.5 text-right">Avg Price</th><th class="px-3 py-2.5 text-right">Gain/Loss ($ / %)</th>
    </tr></thead>
    <tbody class="mono text-[14px] divide-y divide-[#1F2937]">
      ${shown.length ? shown.map(g => {
        const isExpanded = expanded[g.market];
        
        let html = `<tr class="cursor-pointer hover:bg-[#1F2937] transition-colors" data-market="${escAttr(g.market)}">
          <td class="px-3 py-2.5 whitespace-nowrap">${getMethodTag(g.method)}</td>
          <td class="px-3 py-2.5">
            <div class="font-medium max-w-[280px] truncate text-[13px]"><a href="https://polymarket.com/event/${encodeURIComponent(g.market)}" target="_blank" class="hover:underline text-blue-400" onclick="event.stopPropagation()">${esc(formatMarketTitle(g.market))}</a></div>
            <div class="mt-1">${getCategoryTag(g.category)}</div>
          </td>
          <td class="px-3 py-2.5 text-right text-[13px] font-mono opacity-80">${g.avg_cost !== null ? '$' + g.avg_cost.toFixed(4) : '--'}</td>
          <td class="px-3 py-2.5 text-right">${plainPnlPctHTML(g.total_pnl, g.avg_pnl_pct)}</td>
        </tr>`;
        
        if (isExpanded) {
           for (const x of g.exits) {
             html += `<tr class="bg-[#090D16]/50">
               <td class="px-3 py-2 pl-8 whitespace-nowrap opacity-75">${getMethodTag(x.method)}</td>
               <td class="px-3 py-2 max-w-[280px] truncate opacity-75 text-[13px]">${esc(formatMarketTitle(x.market))}</td>
               <td class="px-3 py-2 text-right opacity-60 text-[12px] font-mono">${x.avg_cost !== null ? '$' + x.avg_cost.toFixed(4) : '--'}</td>
               <td class="px-3 py-2 text-right opacity-75">${plainPnlPctHTML(x.pnl, x.pnl_pct)}</td>
             </tr>`;
           }
        }
        return html;
      }).join("") : `<tr><td colspan="4" class="px-4 py-6 text-center mono text-[13px] text-[#9CA3AF]">No closed positions yet.</td></tr>`}
    </tbody></table></div>
    <div class="px-4 py-2.5 border-t border-[#1F2937] flex items-center justify-between text-[12px] mono uppercase tracking-widest">
      <div class="flex items-center gap-2">
        <span class="text-[#9CA3AF]">Rows per page:</span>
        <select class="bg-[#1F2937] text-white outline-none px-2 py-1" onchange="setSettledPerPage(parseInt(this.value))">
          <option value="10" ${perPage===10?'selected':''}>10</option>
          <option value="20" ${perPage===20?'selected':''}>20</option>
          <option value="50" ${perPage===50?'selected':''}>50</option>
        </select>
      </div>
      <div class="flex items-center gap-3">
        ${page > 0 ? `<button class="hover:text-white text-[#9CA3AF]" onclick="setSettledPage(${page - 1})">&lt; Prev</button>` : `<span class="opacity-50 text-[#9CA3AF]">&lt; Prev</span>`}
        <span class="text-[#9CA3AF]">Page ${page + 1} of ${totalPages || 1}</span>
        ${page < totalPages - 1 ? `<button class="hover:text-white text-[#9CA3AF]" onclick="setSettledPage(${page + 1})">Next &gt;</button>` : `<span class="opacity-50 text-[#9CA3AF]">Next &gt;</span>`}
      </div>
    </div>`;
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
async function fetchJSON(url){
  const r = await fetch(url);
  if (!r.ok) throw new Error("HTTP " + r.status);
  return r.json();
}

function showSectionError(elId, err){
  const el = document.getElementById(elId);
  if (!el) return;
  const msg = err && err.message ? err.message : String(err);
  el.innerHTML = `<div class="p-5 border border-[#EF4444]/40 bg-[#EF4444]/5 mono text-[13px] text-[#EF4444]">Could not load this section: ${esc(msg)}</div>`;
}

function fmtClock(ts){
  if (!ts) return "--:--:--";
  return new Date(ts * 1000).toLocaleTimeString();
}

// Metric tooltip: an info icon beside a statistical header opens a card
// with the formula, the live value, and the gate meaning. Pure CSS hover /
// keyboard-focus (the button inside the wrap); no tooltip library needed.
function tip(key, body){
  return `<span class="tip-wrap"><button type="button" class="tip-ico" aria-label="About ${esc(key)}" tabindex="0">i</button>` +
    `<span class="tip-pop" role="tooltip"><span class="tip-k">${esc(key)}</span><span class="tip-t">${body}</span></span></span>`;
}

// ---------- the call (decision hinge) ----------
// The desk exists to answer one question: go or no-go. The hinge renders
// that answer in one line -- the verdict in display type, the deciding
// figure, the sample behind it, and the fleet pulse (fresh data beats).
// The verdict words are the real go-live statuses from stats.py.
const PULSE = { base: null, alive: false };

function renderHinge(s){
  PULSE.base = s.now || Math.floor(Date.now()/1000);
  PULSE.alive = !!s.fleet_alive;
  const st = s.status || "NO_DATA";
  const call = {
    READY_FOR_SMALL_LIVE_PILOT: { word:"GO",      color:"#FBBF24" },
    DIRECTIONAL_SIGNAL:         { word:"SIGNAL",  color:"#F59E0B" },
    COLLECTING:                 { word:"COLLECT", color:"#F59E0B" },
    NO_DATA:                    { word:"NO DATA", color:"#9CA3AF" },
  }[st] || { word: st, color: "#9CA3AF" };
  const el = document.getElementById("hdr-hinge");
  // The hinge answers the desk's only question in one line -- the call and
  // the freshness. The deciding figure (lower bound) and the sample live in
  // the Verdict panel; the raw status lives in the header pill.
  el.innerHTML = `
    <div class="flex items-center gap-x-3 gap-y-1 flex-wrap min-w-0">
      <span class="mono text-[11px] tracking-[0.22em] uppercase text-[#9CA3AF] shrink-0">The call</span>
      <span class="font-display text-[24px] sm:text-[28px] leading-none font-bold tracking-[0.04em]" style="color:${call.color}">${esc(call.word)}</span>
    </div>
    <div class="flex items-center gap-4 mono text-[12px] tracking-[0.12em] uppercase text-[#9CA3AF]">
      <span class="flex items-center gap-2 border border-[#1F2937] bg-[#090D16] px-2.5 py-1">
        <span id="hdr-pulse-dot" class="size-1.5 ${PULSE.alive?'bg-[#10B981] pulse-live':'bg-[#F59E0B]'}"></span>
        <span class="text-[#F9FAFB]">Pulse</span>
        <span id="hdr-pulse-age" class="text-[#F9FAFB]">0s</span>
      </span>
    </div>`;
  startPulseTicker();
}

function renderHingeOffline(err){
  PULSE.base = null; PULSE.alive = false;
  const el = document.getElementById("hdr-hinge");
  el.innerHTML = `
    <div class="flex items-center gap-x-3 gap-y-1 flex-wrap min-w-0">
      <span class="mono text-[11px] tracking-[0.22em] uppercase text-[#9CA3AF]">The call</span>
      <span class="font-display text-[24px] sm:text-[28px] leading-none font-bold tracking-[0.04em] text-[#EF4444]">Offline</span>
      <span class="mono text-[13px] text-[#9CA3AF]">${err && err.message ? esc(err.message) : 'summary unavailable'}</span>
    </div>`;
}

function startPulseTicker(){
  if (window.__pulseTick) clearInterval(window.__pulseTick);
  const tick = () => {
    const age = document.getElementById("hdr-pulse-age");
    const dot = document.getElementById("hdr-pulse-dot");
    if (!age || PULSE.base === null) return;
    const secs = Math.max(0, Math.round(Date.now()/1000 - PULSE.base));
    age.textContent = secs + "s";
    // Phase-4: the same tick drives the per-row freshness badges and dims
    // stale rows (past 60s without a telemetry update).
    tickAgeBadges();
    if (dot){
      const fresh = PULSE.alive && secs < 45;
      dot.classList.toggle("bg-[#10B981]", fresh);
      dot.classList.toggle("pulse-live", fresh);
      dot.classList.toggle("bg-[#F59E0B]", !fresh);
    }
  };
  tick();
  window.__pulseTick = setInterval(tick, 1000);
}

// ---------- data-change cues (flash / roll-up / status transitions) ----------
// Financial figures carry data-kpi + data-v; when a poll changes a value
// the cell flashes green/red (300ms) and data-rollup cells animate the
// number from the previous value to the new one (400ms, ease-out). Status
// cells carry data-state and fade+scale in when the string changes. The
// previous-value maps are empty until the first poll, so first paint never
// flashes -- only real data changes do.
const KPI_PREV = {};
const STATE_PREV = {};
const MOTION_OK = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function fmtKPI(v, kind){
  if (kind === "pct") return v.toFixed(1) + "%";
  return (v < 0 ? "-" : "+") + "$" + Math.abs(v).toFixed(2);
}

function animateNumber(el, from, to, kind){
  if (!MOTION_OK || from === to){ el.textContent = fmtKPI(to, kind); return; }
  const dur = 400, t0 = performance.now();
  const step = (t) => {
    const k = Math.min(1, (t - t0) / dur);
    const e = 1 - Math.pow(1 - k, 3);
    el.textContent = fmtKPI(from + (to - from) * e, kind);
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

function animateChanges(){
  document.querySelectorAll("[data-kpi]").forEach(el => {
    const name = el.getAttribute("data-kpi");
    const v = el.getAttribute("data-v");
    const prev = KPI_PREV[name];
    KPI_PREV[name] = v;
    if (prev === undefined || prev === v) return;
    const p = parseFloat(prev), n = parseFloat(v);
    if (isNaN(p) || isNaN(n) || p === n) return;
    el.classList.remove("flash-up", "flash-down");
    void el.offsetWidth;
    el.classList.add(n > p ? "flash-up" : "flash-down");
    if (el.hasAttribute("data-rollup")){
      const target = el.querySelector("span") || el;
      animateNumber(target, p, n, el.getAttribute("data-fmt") || "usd");
    }
  });
  document.querySelectorAll("[data-state]").forEach(el => {
    const name = el.getAttribute("data-state");
    const v = el.getAttribute("data-v");
    const prev = STATE_PREV[name];
    STATE_PREV[name] = v;
    if (prev === undefined || prev === v) return;
    el.classList.remove("status-in");
    void el.offsetWidth;
    el.classList.add("status-in");
  });
}

// ---------- market detail drawer ----------
// Clicking an active-market row slides a panel in from the right edge
// (x: 100% -> 0) showing that market's live book, P&L, markout, and
// execution log. Pure frontend: the row object and the settled exits are
// already in memory from the last poll, so nothing new is fetched.
let DRAWER_SLUG = null;
let LAST_MARKETS = [];

function openDrawer(slug){
  DRAWER_SLUG = slug;
  renderDrawer();
  document.getElementById("drawer").classList.remove("translate-x-full");
  document.getElementById("drawer").classList.add("translate-x-0");
  const b = document.getElementById("drawer-backdrop");
  b.classList.remove("opacity-0", "pointer-events-none");
  b.classList.add("opacity-100");
  const close = document.getElementById("drawer-close");
  if (close) close.focus();
}

function closeDrawer(){
  DRAWER_SLUG = null;
  document.getElementById("drawer").classList.add("translate-x-full");
  document.getElementById("drawer").classList.remove("translate-x-0");
  const b = document.getElementById("drawer-backdrop");
  b.classList.add("opacity-0", "pointer-events-none");
  b.classList.remove("opacity-100");
}

function renderDrawer(){
  const body = document.getElementById("drawer-body");
  const m = LAST_MARKETS.find(x => x.market === DRAWER_SLUG) || null;
  if (!m){
    body.innerHTML = `<div class="p-6 mono text-[13px] text-[#9CA3AF]">This market is no longer active.</div>`;
    return;
  }
  const g = settledState.grouped.find(x => x.market === m.market);
  const exits = g ? g.exits : [];
  const markout = (m.markout === null || m.markout === undefined)
    ? "--" : (m.markout * 100).toFixed(2) + "&cent;";
  body.innerHTML = `
    <div class="sticky top-0 z-10 bg-[#111827] flex items-start justify-between gap-3 px-4 h-12 border-b border-[#1F2937] shrink-0">
      <div class="min-w-0">
        <div class="font-medium text-[14px] truncate">${esc(formatMarketTitle(m.market))}</div>
        <div class="mt-1 flex items-center gap-2 flex-wrap">${getCategoryTag(m.category)} ${getStatusTag(m.status)}</div>
      </div>
      <button id="drawer-close" onclick="closeDrawer()" class="size-7 border border-[#1F2937] grid place-items-center hover:bg-[#1F2937] transition-colors mono text-[13px] shrink-0" aria-label="Close market details">&times;</button>
    </div>
    <div class="p-4 space-y-4">
      <div>
        <div class="mono text-[12px] tracking-[0.16em] uppercase text-[#9CA3AF] mb-2">Order book &middot; live mid</div>
        ${orderDepthHtml(m)}
      </div>
      <div class="grid grid-cols-4 gap-2 mono text-[12px]">
        <div class="border border-[#1F2937] p-2.5 text-center"><div class="text-[#9CA3AF] tracking-widest uppercase">Commit</div><div class="font-bold mt-1">$${m.committed.toFixed(0)}</div></div>
        <div class="border border-[#1F2937] p-2.5 text-center"><div class="text-[#9CA3AF] tracking-widest uppercase">Resting</div><div class="font-bold mt-1">${m.resting}</div></div>
        <div class="border border-[#1F2937] p-2.5 text-center"><div class="text-[#9CA3AF] tracking-widest uppercase">Fills</div><div class="font-bold mt-1">${m.fills}</div></div>
        <div class="border border-[#1F2937] p-2.5 text-center"><div class="text-[#9CA3AF] tracking-widest uppercase">Age</div><div class="font-bold mt-1">${formatAge(m.age)}</div></div>
      </div>
      <div class="grid grid-cols-2 gap-2 mono text-[12px]">
        <div class="bg-[#3B82F6]/10 border border-[#3B82F6]/20 p-3"><div class="text-[#3B82F6] tracking-[0.14em] uppercase">Unrealized</div><div class="font-bold mt-1 text-[16px]">${plainPnlPctHTML(m.unrealized, m.unrealized_pct)}</div></div>
        <div class="bg-[#10B981]/10 border border-[#10B981]/20 p-3"><div class="text-[#10B981] tracking-[0.14em] uppercase">Realized (closes)</div><div class="font-bold mt-1 text-[16px]">${m.closes ? plainPnlPctHTML(m.realized, null) : `<span class="text-[#9CA3AF]">--</span>`}</div></div>
      </div>
      <div>
        <div class="mono text-[12px] tracking-[0.16em] uppercase text-[#9CA3AF] mb-2">Markout drift</div>
        <div class="border border-[#1F2937] bg-[#090D16] px-3 py-2.5 mono text-[13px] flex items-center justify-between">
          <span class="text-[#9CA3AF]">Mean drift per filled share</span>
          <span class="font-bold ${(m.markout||0)<0?'text-[#EF4444]':'text-[#F9FAFB]'}">${markout}</span>
        </div>
        <div class="mt-1.5 mono text-[11px] leading-4 text-[#9CA3AF]">Pooled fleet-wide drift lives in the Verdict panel; this is this market's own measured mean.</div>
      </div>
      <div>
        <div class="mono text-[12px] tracking-[0.16em] uppercase text-[#9CA3AF] mb-2">Execution log &middot; ${exits.length} exit${exits.length===1?'':'s'}</div>
        ${exits.length ? `<div class="border border-[#1F2937] divide-y divide-[#1F2937] mono text-[13px]">
          ${exits.map(x => `<div class="flex items-center justify-between gap-3 px-3 py-2">
            <span class="flex items-center gap-2 min-w-0">${getMethodTag(x.method)}<span class="text-[#9CA3AF] text-[12px] truncate">${x.shares ? x.shares.toFixed(0) + ' sh' : ''} @ $${x.avg_cost===null?'--':x.avg_cost.toFixed(4)}</span></span>
            <span>${plainPnlPctHTML(x.pnl, x.pnl_pct)}</span>
          </div>`).join("")}
        </div>` : `<div class="border border-[#1F2937] bg-[#090D16] px-3 py-2.5 mono text-[12px] text-[#9CA3AF]">No closes recorded for this market yet.</div>`}
      </div>
      <div class="mono text-[11px] leading-4 text-[#9CA3AF] border-t border-[#1F2937] pt-2.5">
        <a href="https://polymarket.com/event/${encodeURIComponent(m.market)}" target="_blank" rel="noopener" class="text-blue-400 hover:underline">Open on Polymarket &nearr;</a> &middot; refreshes with each 15s poll &middot; Esc or backdrop to close.
      </div>
    </div>`;
}

// ---------- auto-refresh ----------
// Poll the four endpoints every 15s and re-render in place. Tab state and
// open settled groups survive because the panel shells keep their classes
// and renderSettled reuses settledState (expanded groups + pagination).
// A failed poll keeps the last good data on screen -- the pulse age is
// what reports staleness, turning amber after 45s.
let REFRESH_BUSY = false;

function renderSummary(s){
  LAST_STATS = s;
  // The status string appears once on this screen -- right here in the
  // header. The settled sample lives in the Verdict panel; the go-live
  // readiness lives in the Gates panel.
  document.getElementById("hdr-pills").innerHTML = `
    <span class="mono text-[12px] tracking-[0.12em] uppercase px-2.5 py-1 bg-[#090D16] font-semibold border border-[#1F2937]">${esc(s.status)}</span>`;
  document.getElementById("hdr-live").innerHTML = `
    <span class="size-2 ${s.fleet_alive?'bg-[#10B981] health-pulse':'bg-[#F59E0B]'} shrink-0"></span>
    <span class="tracking-[0.12em] uppercase text-[12px] text-[#9CA3AF]">Data health &middot; <span class="text-[#F9FAFB]">${s.fleet_alive?'Live':'Idle'}</span></span>`;
  renderHinge(s);
  renderPositions(s);
  renderVerdict(s);
  renderGauges(s);
  renderEvidence(s);
  // Scope keeps one thing the panels don't: honest freshness. The status,
  // the settled sample, and the lower bound each have a single home above.
  document.getElementById("scope-tiles").innerHTML = `
    <div class="border border-[#1F2937] p-3 text-center"><div class="text-[#9CA3AF]">Data as of</div><div class="font-semibold mt-1">${fmtClock(s.now)}</div></div>`;
}

async function refresh(){
  if (REFRESH_BUSY) return;
  REFRESH_BUSY = true;
  const main = document.querySelector("main");
  if (main) main.classList.add("sh-refreshing");
  try {
    const [st, fn, mk, s] = await Promise.all([
      fetchJSON("/api/settled").catch(() => null),
      fetchJSON("/api/funnel").catch(() => null),
      fetchJSON("/api/markets").catch(() => null),
      fetchJSON("/api/summary").catch(() => null),
    ]);
    if (st) renderSettled(st.settled, st.total_closes);
    if (fn) renderFunnel(fn);
    if (mk) renderMarkets(mk.markets);
    if (s) renderSummary(s);
    // Flash changed figures, animate status transitions, and keep an open
    // drawer live with the freshest data -- all after the re-render.
    animateChanges();
    if (DRAWER_SLUG) renderDrawer();
  } finally {
    REFRESH_BUSY = false;
    if (main) main.classList.remove("sh-refreshing");
  }
}

async function boot(){
  // Paint what is instant first and let the slower endpoints stream in --
  // the summary bundle is the heaviest read, so it renders last. Every
  // panel degrades to a visible error box instead of a silent blank section.
  fetchJSON("/api/settled").then(st => renderSettled(st.settled, st.total_closes))
    .catch(e => showSectionError("tab-settled", e));
  fetchJSON("/api/funnel").then(renderFunnel)
    .catch(e => showSectionError("tab-funnel", e));
  fetchJSON("/api/markets").then(mk => renderMarkets(mk.markets))
    .catch(e => showSectionError("tab-markets", e));

  let s;
  try {
    s = await fetchJSON("/api/summary");
    renderSummary(s);
    // Seed the previous-value maps so the FIRST poll flashes on real
    // changes rather than treating the initial paint as a change.
    animateChanges();
  } catch (e) {
    showSectionError("sec-positions", e);
    showSectionError("sec-verdict", e);
    showSectionError("sec-gauges", e);
    showSectionError("sec-evidence", e);
    renderHingeOffline(e);
    document.getElementById("hdr-live").innerHTML = `
      <span class="size-2 bg-[#EF4444] shrink-0"></span>
      <span class="tracking-[0.12em] uppercase text-[12px] text-[#9CA3AF]">Data health &middot; <span class="text-[#EF4444]">Offline</span></span>`;
    document.getElementById("scope-tiles").innerHTML =
      `<div class="col-span-3 border border-[#EF4444]/30 p-3 text-center mono text-[12px] text-[#EF4444]">Summary unavailable</div>`;
  }
  // Poll every 15s from here on. A failed first load still gets recovery
  // polls; a successful poll re-renders the whole desk in place.
  setInterval(refresh, 15000);
}
boot();
</script>
""")
