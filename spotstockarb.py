#!/usr/bin/env python3
"""
Stock/ETF vs Hyperliquid Perp Funding Rate Arbitrage Calculator
Calculates PnL from going long a stock/ETF and short a Hyperliquid perp.
"""

import requests
import yfinance as yf
import csv
import os
from datetime import datetime, timezone, timedelta
from tabulate import tabulate


# Output directory for CSV files
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")


API_URL = "https://api.hyperliquid.xyz/info"


def format_coin_name(coin: str) -> str:
    """Format coin name, preserving HIP-3 prefix format (e.g., xyz:COPPER)."""
    if ":" in coin:
        prefix, name = coin.split(":", 1)
        return f"{prefix}:{name.upper()}"
    return coin.upper()


def get_hl_candles(coin: str, hours_back: int) -> list[dict]:
    """Fetch hourly candle data from Hyperliquid API."""
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = now_ms - (hours_back * 60 * 60 * 1000)

    formatted_coin = format_coin_name(coin)
    payload = {
        "type": "candleSnapshot",
        "req": {
            "coin": formatted_coin,
            "interval": "1h",
            "startTime": start_ms,
            "endTime": now_ms
        }
    }

    headers = {"Content-Type": "application/json"}
    response = requests.post(API_URL, json=payload, headers=headers)
    response.raise_for_status()

    return response.json()


def get_hl_funding_history(coin: str, hours_back: int) -> list[dict]:
    """Fetch funding rate history from Hyperliquid API."""
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = now_ms - (hours_back * 60 * 60 * 1000)

    formatted_coin = format_coin_name(coin)
    payload = {
        "type": "fundingHistory",
        "coin": formatted_coin,
        "startTime": start_ms,
        "endTime": now_ms
    }

    headers = {"Content-Type": "application/json"}
    response = requests.post(API_URL, json=payload, headers=headers)
    response.raise_for_status()

    return response.json()


def get_stock_data(ticker: str, hours_back: int) -> dict:
    """Fetch hourly stock data from Yahoo Finance."""
    stock = yf.Ticker(ticker)

    # Calculate period needed (yfinance uses period strings)
    days_needed = max(1, (hours_back // 24) + 2)  # Add buffer for market hours
    if days_needed <= 5:
        period = "5d"
    elif days_needed <= 30:
        period = "1mo"
    else:
        period = "3mo"

    data = stock.history(period=period, interval="1h")
    return data


def align_data(stock_data, hl_candles, funding_data, hours_back: int) -> list[dict]:
    """Align stock and HL data by hour. During non-market hours, stock price is held constant."""
    aligned = []

    # Convert HL candles to dict by hour timestamp
    hl_prices = {}
    for candle in hl_candles:
        ts = candle["t"] // 1000  # Convert ms to seconds
        hour_ts = (ts // 3600) * 3600  # Round to hour
        hl_prices[hour_ts] = {
            "open": float(candle["o"]),
            "close": float(candle["c"])
        }

    # Convert funding data to dict by hour timestamp
    funding_rates = {}
    for record in funding_data:
        ts = record["time"] // 1000
        hour_ts = (ts // 3600) * 3600
        funding_rates[hour_ts] = float(record["fundingRate"])

    # Convert stock data to dict by hour timestamp
    stock_prices = {}
    for idx, row in stock_data.iterrows():
        ts = int(idx.timestamp())
        hour_ts = (ts // 3600) * 3600
        stock_prices[hour_ts] = {
            "open": float(row["Open"]),
            "close": float(row["Close"])
        }

    # Use all hours where we have HL data (crypto trades 24/7)
    all_hl_hours = sorted(hl_prices.keys())

    if not all_hl_hours:
        return aligned

    # Track last known stock price for non-market hours
    last_stock_price = None

    for hour_ts in all_hl_hours:
        # Check if we have stock data for this hour
        if hour_ts in stock_prices:
            stock_open = stock_prices[hour_ts]["open"]
            stock_close = stock_prices[hour_ts]["close"]
            last_stock_price = stock_close
            market_open = True
        elif last_stock_price is not None:
            # Non-market hours: stock position is frozen
            stock_open = last_stock_price
            stock_close = last_stock_price
            market_open = False
        else:
            # No stock data yet, try to find the most recent stock price before this hour
            earlier_stock_hours = [h for h in stock_prices.keys() if h < hour_ts]
            if earlier_stock_hours:
                latest_hour = max(earlier_stock_hours)
                last_stock_price = stock_prices[latest_hour]["close"]
                stock_open = last_stock_price
                stock_close = last_stock_price
                market_open = False
            else:
                # No stock data available yet, skip this hour
                continue

        entry = {
            "timestamp": hour_ts,
            "datetime": datetime.fromtimestamp(hour_ts, tz=timezone.utc),
            "stock_open": stock_open,
            "stock_close": stock_close,
            "hl_open": hl_prices[hour_ts]["open"],
            "hl_close": hl_prices[hour_ts]["close"],
            "funding_rate": funding_rates.get(hour_ts, 0.0),
            "market_open": market_open
        }
        aligned.append(entry)

    return aligned


def calculate_arb_pnl(aligned_data: list[dict], starting_amount: float) -> list[dict]:
    """Calculate PnL for each hour of the arbitrage trade."""
    half_amount = starting_amount / 2
    results = []

    cumulative_pnl = 0.0
    cumulative_funding = 0.0

    for i, entry in enumerate(aligned_data):
        # Stock PnL (long position): profit when price goes up
        # During non-market hours, stock_open == stock_close, so PnL is 0
        stock_pct_change = (entry["stock_close"] - entry["stock_open"]) / entry["stock_open"]
        stock_pnl = half_amount * stock_pct_change

        # HL PnL (short position): profit when price goes down
        hl_pct_change = (entry["hl_close"] - entry["hl_open"]) / entry["hl_open"]
        hl_pnl = -half_amount * hl_pct_change  # Negative because we're short

        # Funding profit (short receives funding when rate is positive)
        funding_rate = entry["funding_rate"]
        funding_profit = half_amount * funding_rate  # Short receives positive funding

        # Total PnL for this hour (excluding funding)
        hour_pnl = stock_pnl + hl_pnl

        cumulative_pnl += hour_pnl
        cumulative_funding += funding_profit

        results.append({
            "datetime": entry["datetime"],
            "stock_price": entry["stock_close"],
            "hl_price": entry["hl_close"],
            "stock_pnl": stock_pnl,
            "hl_pnl": hl_pnl,
            "hour_pnl": hour_pnl,
            "funding_rate": funding_rate,
            "funding_profit": funding_profit,
            "cumulative_pnl": cumulative_pnl,
            "cumulative_funding": cumulative_funding,
            "market_open": entry.get("market_open", True)
        })

    return results


def save_to_csv(results: list[dict], stock_ticker: str, hl_ticker: str, starting_amount: float):
    """Save results to CSV file."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_hl = hl_ticker.replace(":", "_")
    filename = f"arb_{stock_ticker}_{safe_hl}_{timestamp}.csv"
    filepath = os.path.join(OUTPUT_DIR, filename)

    # Ensure output directory exists
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    with open(filepath, "w", newline="") as f:
        fieldnames = [
            "Time (UTC)", "Market Open", "Stock Price", "HL Price",
            "Stock PnL", "HL PnL", "Hour PnL",
            "Funding Rate", "Funding Profit",
            "Cumulative PnL", "Cumulative Funding"
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for row in results:
            writer.writerow({
                "Time (UTC)": row["datetime"].strftime("%Y-%m-%d %H:%M"),
                "Market Open": "Yes" if row.get("market_open", True) else "No",
                "Stock Price": f"{row['stock_price']:.4f}",
                "HL Price": f"{row['hl_price']:.4f}",
                "Stock PnL": f"{row['stock_pnl']:.2f}",
                "HL PnL": f"{row['hl_pnl']:.2f}",
                "Hour PnL": f"{row['hour_pnl']:.2f}",
                "Funding Rate": f"{row['funding_rate']:.8f}",
                "Funding Profit": f"{row['funding_profit']:.2f}",
                "Cumulative PnL": f"{row['cumulative_pnl']:.2f}",
                "Cumulative Funding": f"{row['cumulative_funding']:.2f}"
            })

        # Summary
        if results:
            total_pnl = results[-1]["cumulative_pnl"]
            total_funding = results[-1]["cumulative_funding"]
            total_profit = total_pnl + total_funding
            market_hours = sum(1 for r in results if r.get("market_open", True))
            non_market_hours = len(results) - market_hours
            f.write(f"\nSummary,,,,,,,,,,\n")
            f.write(f"Starting Amount,{starting_amount:.2f},,,,,,,,,\n")
            f.write(f"Total Hours,{len(results)} ({market_hours} market / {non_market_hours} non-market),,,,,,,,,\n")
            f.write(f"Total Price PnL,{total_pnl:.2f},,,,,,,,,\n")
            f.write(f"Total Funding Profit,{total_funding:.2f},,,,,,,,,\n")
            f.write(f"Total Overall Profit,{total_profit:.2f},,,,,,,,,\n")
            f.write(f"Return %,{(total_profit/starting_amount)*100:.2f}%,,,,,,,,,\n")

    return filepath


def main():
    print("=" * 70)
    print("       Stock/ETF vs Hyperliquid Perp Arbitrage Calculator")
    print("=" * 70)
    print()
    print("This tool calculates the PnL from going LONG a stock/ETF")
    print("and SHORT a Hyperliquid perpetual contract.")
    print()

    while True:
        # Get user inputs
        try:
            starting_amount = input("Starting amount ($) or 'quit' to exit: ").strip()
            if starting_amount.lower() == 'quit':
                print("Goodbye!")
                break
            starting_amount = float(starting_amount.replace(",", "").replace("$", ""))
            if starting_amount <= 0:
                print("Error: Starting amount must be positive.")
                print()
                continue
        except ValueError:
            print("Error: Please enter a valid number.")
            print()
            continue

        stock_ticker = input("What stock ticker (long): ").strip().upper()
        if not stock_ticker:
            print("Error: Stock ticker cannot be empty.")
            print()
            continue

        hl_ticker = input("What Hyperliquid ticker (short, e.g., BTC, xyz:COPPER): ").strip()
        if not hl_ticker:
            print("Error: Hyperliquid ticker cannot be empty.")
            print()
            continue

        try:
            hours_back = int(input("Length of trade (hrs): ").strip())
            if hours_back <= 0:
                print("Error: Hours must be a positive number.")
                print()
                continue
        except ValueError:
            print("Error: Please enter a valid number.")
            print()
            continue

        display_hl = format_coin_name(hl_ticker)
        print()
        print(f"Fetching data for {stock_ticker} (long) vs {display_hl} (short)...")
        print()

        try:
            # Fetch all data
            print("  Fetching stock data...")
            stock_data = get_stock_data(stock_ticker, hours_back)
            if stock_data.empty:
                print(f"Error: No data found for stock ticker {stock_ticker}")
                print()
                continue

            print("  Fetching Hyperliquid candle data...")
            hl_candles = get_hl_candles(hl_ticker, hours_back)
            if not hl_candles:
                print(f"Error: No candle data found for {display_hl}")
                print()
                continue

            print("  Fetching Hyperliquid funding data...")
            funding_data = get_hl_funding_history(hl_ticker, hours_back)

            # Align data
            print("  Aligning data...")
            aligned_data = align_data(stock_data, hl_candles, funding_data, hours_back)

            if not aligned_data:
                print("Error: No overlapping data between stock and Hyperliquid.")
                print("Note: Stock markets are only open during market hours (9:30 AM - 4:00 PM ET).")
                print("Crypto trades 24/7, so there may be limited overlap.")
                print()
                continue

            # Calculate PnL
            results = calculate_arb_pnl(aligned_data, starting_amount)

            # Display table
            table_data = [[
                row["datetime"].strftime("%Y-%m-%d %H:%M"),
                f"${row['stock_price']:.2f}" + ("" if row.get("market_open", True) else "*"),
                f"${row['hl_price']:.2f}",
                f"${row['hour_pnl']:.2f}",
                f"${row['funding_profit']:.2f}",
                f"${row['cumulative_pnl']:.2f}",
                f"${row['cumulative_funding']:.2f}"
            ] for row in results]

            headers = ["Time (UTC)", "Stock $", "HL $", "Hour PnL", "Funding Profit", "Cum. PnL", "Cum. Funding"]

            print()
            print("=" * 70)
            print(f"  Arbitrage Results: {stock_ticker} (Long) vs {display_hl} (Short)")
            print(f"  Starting Amount: ${starting_amount:,.2f} (${starting_amount/2:,.2f} each side)")
            print("=" * 70)
            print()
            print(tabulate(table_data, headers=headers, tablefmt="grid"))
            print()

            # Summary
            total_pnl = results[-1]["cumulative_pnl"]
            total_funding = results[-1]["cumulative_funding"]
            total_profit = total_pnl + total_funding
            return_pct = (total_profit / starting_amount) * 100

            # Count market hours vs non-market hours
            market_hours = sum(1 for r in results if r.get("market_open", True))
            non_market_hours = len(results) - market_hours

            print("=" * 70)
            print("  SUMMARY")
            print("=" * 70)
            print(f"  Hours Analyzed:        {len(results)} ({market_hours} market, {non_market_hours} non-market)")
            print(f"  Total Price PnL:       ${total_pnl:,.2f}")
            print(f"  Total Funding Profit:  ${total_funding:,.2f}")
            print("-" * 70)
            print(f"  TOTAL OVERALL PROFIT:  ${total_profit:,.2f} ({return_pct:.4f}%)")
            print("=" * 70)
            if non_market_hours > 0:
                print("  * = Non-market hours (stock price frozen, HL still trading)")
            print()

            # Save to CSV
            filename = save_to_csv(results, stock_ticker, display_hl, starting_amount)
            print(f"Results saved to: {filename}")

        except requests.exceptions.HTTPError as e:
            print(f"API Error: {e}")
        except Exception as e:
            print(f"Error: {e}")

        print()
        print("-" * 70)
        print()


if __name__ == "__main__":
    main()
