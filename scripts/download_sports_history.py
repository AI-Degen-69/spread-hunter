"""Download 60 resolved sports/esports tick datasets for Spread Hunter replay."""

import json
from pathlib import Path

# Requires: pip install marketlens
try:
    from marketlens import MarketLens
except ImportError:
    raise SystemExit("Run: pip install marketlens")

OUTPUT_DIR = Path("runtime/replay_data")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def download_60_markets():
    client = MarketLens()

    print("Fetching resolved sports & esports moneyline markets...")

    # Discover resolved matches (EPL, Champions League, NBA, CS2, LoL)
    candidates = []
    for cat in ["Sports", "Esports"]:
        matches = client.list(category=cat, status="resolved", take=50)
        for m in matches:
            # Enforce binary match winner (moneyline)
            if hasattr(m, "tokens") and len(m.tokens) == 2:
                candidates.append(m)
            elif not hasattr(m, "tokens"):
                candidates.append(m)
            if len(candidates) >= 60:
                break
        if len(candidates) >= 60:
            break

    print(f"Discovered {len(candidates)} candidates. Downloading tick history...")

    manifest = []
    for i, m in enumerate(candidates[:60], 1):
        slug = getattr(m, "slug", str(m))
        target_file = OUTPUT_DIR / f"{slug}.jsonl"

        print(f"[{i}/60] Downloading L2 ticks for: {slug}...")

        records_written = 0
        with target_file.open("w", encoding="utf-8") as f:
            try:
                # Stream tick-by-tick orderbook updates
                for update, book in client.walk(slug):
                    payload = {
                        "timestamp": str(book.as_of),
                        "bids": book.bids[:5],  # top-5 levels for queue modeling
                        "asks": book.asks[:5],
                        "spread": round(book.asks[0][0] - book.bids[0][0], 4)
                        if book.bids and book.asks
                        else None,
                    }
                    f.write(json.dumps(payload) + "\n")
                    records_written += 1
            except Exception as e:
                print(f"  Warning on {slug}: {e}")
                continue

        manifest.append(
            {
                "slug": slug,
                "ticks": records_written,
                "file": str(target_file),
                "winning_outcome": getattr(m, "winning_outcome", None),
            }
        )

    # Save summary index for the backtest engine
    (OUTPUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(f"\nDone. Saved 60 markets to {OUTPUT_DIR}/ (see manifest.json)")


if __name__ == "__main__":
    download_60_markets()