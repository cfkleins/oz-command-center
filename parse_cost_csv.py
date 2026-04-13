#!/usr/bin/env python3
"""
CODE-008 Phase 1 — Cost CSV Parser
Reads Anthropic console CSV export and updates data.json with cost section.

Usage:
  python3 parse_cost_csv.py [--csv PATH] [--data PATH] [--budget AMOUNT]

Anthropic CSV format (actual export from console.anthropic.com):
  usage_date_utc, model, workspace, api_key, usage_type, context_window,
  token_type, cost_usd, list_price_usd, cost_type, inference_geo, speed

Each row is one token_type (input_no_cache, input_cache_read,
input_cache_write_5m, output) for one model on one day.
Script aggregates into daily totals and model breakdowns.
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path


DEFAULT_CSV_DIR = "G:/My Drive/cfk master/Counsel/Cost-data"
FALLBACK_CSV_DIR = "~/vault/cost-data"


def find_latest_csv(directory=None):
    """Find the most recently modified CSV in the directory."""
    dirs_to_try = []
    if directory:
        dirs_to_try.append(directory)
    dirs_to_try.extend([DEFAULT_CSV_DIR, FALLBACK_CSV_DIR])

    for d in dirs_to_try:
        csv_dir = Path(d).expanduser()
        if not csv_dir.exists():
            continue
        csvs = sorted(csv_dir.glob("*.csv"), key=lambda f: f.stat().st_mtime, reverse=True)
        if csvs:
            return csvs[0]

    print("No CSV files found. Searched:")
    for d in dirs_to_try:
        print(f"  {d}")
    print("\nExport CSV from console.anthropic.com -> Cost page -> Export button")
    sys.exit(1)


def parse_csv(csv_path):
    """Parse Anthropic cost CSV into structured records.

    Handles the real export format where each row is a single
    token_type (input_no_cache, input_cache_read, input_cache_write_5m, output)
    for one model on one day, with cost_usd as the cost column.
    """
    # Accumulate: (date, model) -> { cost, token_type_costs }
    day_model = {}

    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = [h.strip() for h in reader.fieldnames]

        for row in reader:
            # Normalize keys
            r = {k.strip(): v.strip() for k, v in row.items()}

            date = r.get("usage_date_utc", r.get("date", r.get("Date", "")))
            model = r.get("model", r.get("Model", "unknown"))
            token_type = r.get("token_type", "")

            # Get cost — try cost_usd first, fall back to other columns
            cost_str = r.get("cost_usd", r.get("cost", r.get("Cost", "0")))
            try:
                cost = float(cost_str.replace(",", "").replace("$", ""))
            except ValueError:
                cost = 0.0

            if not date:
                continue

            key = (date, model)
            if key not in day_model:
                day_model[key] = {
                    "date": date,
                    "model": model,
                    "cost": 0.0,
                    "api_key": r.get("api_key", ""),
                    "by_type": {}
                }

            day_model[key]["cost"] += cost

            if token_type:
                if token_type not in day_model[key]["by_type"]:
                    day_model[key]["by_type"][token_type] = 0.0
                day_model[key]["by_type"][token_type] += cost

    return list(day_model.values())


def aggregate(records):
    """Aggregate records into daily totals and model breakdown."""
    daily = {}
    models = {}
    total_cost = 0

    for r in records:
        date = r["date"]
        cost = r["cost"]
        model = r["model"]

        # Daily aggregation
        if date not in daily:
            daily[date] = {"date": date, "cost": 0.0, "models": {}}
        daily[date]["cost"] += cost

        if model not in daily[date]["models"]:
            daily[date]["models"][model] = 0.0
        daily[date]["models"][model] += cost

        # Model totals
        if model not in models:
            models[model] = {"cost": 0.0, "by_type": {}}
        models[model]["cost"] += cost

        # Merge token type breakdowns
        for tt, tc in r.get("by_type", {}).items():
            if tt not in models[model]["by_type"]:
                models[model]["by_type"][tt] = 0.0
            models[model]["by_type"][tt] += tc

        total_cost += cost

    # Round everything
    for d in daily.values():
        d["cost"] = round(d["cost"], 4)
        d["models"] = {m: round(c, 4) for m, c in d["models"].items()}

    for m in models.values():
        m["cost"] = round(m["cost"], 4)
        m["by_type"] = {t: round(c, 4) for t, c in m["by_type"].items()}

    daily_sorted = sorted(daily.values(), key=lambda d: d["date"])

    return {
        "daily": daily_sorted,
        "models": models,
        "totalCost": round(total_cost, 2),
    }


def compute_projection(daily_data):
    """30-day projection based on trailing 7-day average."""
    if not daily_data:
        return 0
    recent = daily_data[-7:]
    avg_daily = sum(d["cost"] for d in recent) / len(recent)
    return round(avg_daily * 30, 2)


def compute_mtd(daily_data):
    """Month-to-date spend from daily data."""
    if not daily_data:
        return 0
    today = datetime.now()
    current_month = today.strftime("%Y-%m")
    mtd = sum(d["cost"] for d in daily_data if d["date"].startswith(current_month))
    return round(mtd, 2)


def build_cost_section(agg, budget=None, csv_path=None):
    """Build the cost section for data.json."""
    daily = agg["daily"]

    mtd = compute_mtd(daily)
    projection = compute_projection(daily)
    trailing_7_avg = round(sum(d["cost"] for d in daily[-7:]) / max(len(daily[-7:]), 1), 2)

    dates = [d["date"] for d in daily]
    date_range = {"start": dates[0], "end": dates[-1]} if dates else {}

    # Find the heaviest day
    peak_day = max(daily, key=lambda d: d["cost"]) if daily else None

    cost_data = {
        "lastUpdated": datetime.now().astimezone().isoformat(),
        "csvFile": os.path.basename(csv_path) if csv_path else "unknown",
        "dateRange": date_range,
        "stream": "API Console",
        "monthToDate": mtd,
        "projectedMonthly": projection,
        "trailing7DayAvg": trailing_7_avg,
        "totalCost": agg["totalCost"],
        "peakDay": {"date": peak_day["date"], "cost": round(peak_day["cost"], 2)} if peak_day else None,
        "budget": budget,
        "budgetStatus": "ok" if (budget is None or projection <= budget) else "warning",
        "modelBreakdown": [
            {
                "model": m,
                "cost": round(d["cost"], 4),
                "byType": d["by_type"],
            }
            for m, d in sorted(agg["models"].items(), key=lambda x: -x[1]["cost"])
        ],
        "daily": [
            {"date": d["date"], "cost": d["cost"], "models": d["models"]}
            for d in daily
        ],
        "proTier": {
            "note": "Manual entry - update via script or data.json directly",
            "monthlySubscription": None,
            "extraCredits": None,
            "extraConsumed": None
        }
    }

    return cost_data


def update_data_json(data_json_path, cost_data):
    """Merge cost section into data.json."""
    data_path = Path(data_json_path)

    if data_path.exists():
        with open(data_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {}

    data["cost"] = cost_data

    if "_meta" in data:
        data["_meta"]["lastUpdated"] = datetime.now().astimezone().isoformat()

    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"\nUpdated {data_path}")
    print(f"  MTD:          ${cost_data['monthToDate']:.2f}")
    print(f"  Projected:    ${cost_data['projectedMonthly']:.2f}/month")
    print(f"  7-day avg:    ${cost_data['trailing7DayAvg']:.2f}/day")
    print(f"  Total:        ${cost_data['totalCost']:.2f}")
    if cost_data.get("peakDay"):
        print(f"  Peak day:     {cost_data['peakDay']['date']} (${cost_data['peakDay']['cost']:.2f})")
    print(f"  Models:       {len(cost_data['modelBreakdown'])}")
    print(f"  Days:         {len(cost_data['daily'])}")
    if cost_data["budget"]:
        status = "OK" if cost_data["budgetStatus"] == "ok" else "WARNING - projected over budget!"
        print(f"  Budget:       ${cost_data['budget']:.2f}/month - {status}")
    print()

    # Print model breakdown
    print("  Model breakdown:")
    for m in cost_data["modelBreakdown"]:
        pct = (m["cost"] / cost_data["totalCost"] * 100) if cost_data["totalCost"] else 0
        print(f"    {m['model']:30s}  ${m['cost']:>8.2f}  ({pct:.1f}%)")
        for tt, tc in sorted(m.get("byType", {}).items(), key=lambda x: -x[1]):
            if tc > 0:
                print(f"      {tt:28s}  ${tc:>8.2f}")


def main():
    parser = argparse.ArgumentParser(description="Parse Anthropic cost CSV and update data.json")
    parser.add_argument("--csv", help="Path to CSV file (default: latest in cost-data dirs)")
    parser.add_argument("--csv-dir", help="Directory to search for CSV files")
    parser.add_argument("--data", default=os.path.expanduser("~/oz-command-center/data.json"),
                        help="Path to data.json")
    parser.add_argument("--budget", type=float, default=None,
                        help="Monthly budget target in dollars")
    args = parser.parse_args()

    if args.csv:
        csv_path = Path(args.csv)
        if not csv_path.exists():
            print(f"Error: CSV file not found: {csv_path}")
            sys.exit(1)
    else:
        csv_path = find_latest_csv(args.csv_dir)

    print(f"Reading CSV: {csv_path}")

    records = parse_csv(csv_path)
    if not records:
        print("No records found in CSV.")
        sys.exit(1)

    print(f"Parsed {len(records)} date-model groups")

    agg = aggregate(records)
    cost_data = build_cost_section(agg, budget=args.budget, csv_path=str(csv_path))
    update_data_json(args.data, cost_data)
    print("Done. Commit and push to trigger Vercel deploy.")


if __name__ == "__main__":
    main()
