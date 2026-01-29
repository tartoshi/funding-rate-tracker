#!/usr/bin/env python3
"""
Hyperliquid Funding Rate Tracker
Tracks hourly funding rates for specified coins on Hyperliquid.
"""

import requests
import csv
import os
from datetime import datetime, timezone
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


def get_funding_history(coin: str, hours_back: int) -> list[dict]:
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


def annualize_rate(hourly_rate: float) -> float:
    """Convert hourly funding rate to annualized rate (8760 hours/year)."""
    return hourly_rate * 8760


def format_funding_data(data: list[dict]) -> list[dict]:
    """Format raw API data into readable format."""
    formatted = []
    for record in data:
        timestamp = datetime.fromtimestamp(record["time"] / 1000, tz=timezone.utc)
        funding_rate = float(record["fundingRate"])
        premium = float(record["premium"])
        annualized = annualize_rate(funding_rate)

        formatted.append({
            "Time (UTC)": timestamp.strftime("%Y-%m-%d %H:%M"),
            "Funding Rate": f"{funding_rate:.8f}",
            "Funding Rate %": f"{funding_rate * 100:.6f}%",
            "Annualized %": f"{annualized * 100:.2f}%",
            "Premium": f"{premium:.8f}",
            "Coin": record["coin"]
        })

    return formatted


def calculate_average_funding(data: list[dict]) -> float:
    """Calculate average funding rate from raw data."""
    if not data:
        return 0.0
    rates = [float(record["fundingRate"]) for record in data]
    return sum(rates) / len(rates)


def save_to_csv(formatted_data: list[dict], coin: str, avg_rate: float):
    """Save funding data to CSV file."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"funding_rates_{coin}_{timestamp}.csv"
    filepath = os.path.join(OUTPUT_DIR, filename)

    if not formatted_data:
        print("No data to save.")
        return None

    # Ensure output directory exists
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    avg_annualized = annualize_rate(avg_rate)

    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=formatted_data[0].keys())
        writer.writeheader()
        writer.writerows(formatted_data)

        # Add summary rows
        f.write(f"\nAverage Funding Rate (Hourly),{avg_rate:.8f},{avg_rate * 100:.6f}%,,,{coin}\n")
        f.write(f"Average Funding Rate (Annualized),,,{avg_annualized * 100:.2f}%,,{coin}\n")

    return filepath


def main():
    print("=" * 60)
    print("       Hyperliquid Funding Rate Tracker")
    print("=" * 60)
    print()

    while True:
        # Get user inputs
        coin = input("What coin on Hyperliquid (e.g., BTC, ETH, xyz:COPPER) or 'quit' to exit: ").strip()
        if coin.lower() == 'quit':
            print("Goodbye!")
            break
        if not coin:
            print("Error: Coin symbol cannot be empty.")
            print()
            continue

        try:
            hours_back = int(input("How many hours back would you like the funding rate: ").strip())
            if hours_back <= 0:
                print("Error: Hours must be a positive number.")
                print()
                continue
        except ValueError:
            print("Error: Please enter a valid number.")
            print()
            continue

        display_coin = format_coin_name(coin)
        print()
        print(f"Fetching funding rates for {display_coin} over the last {hours_back} hours...")
        print()

        try:
            # Fetch data from API
            raw_data = get_funding_history(coin, hours_back)

            if not raw_data:
                print(f"No funding rate data found for {display_coin} in the specified time range.")
                print("Please check if the coin symbol is correct (e.g., BTC, ETH, xyz:COPPER).")
                print()
                continue

            # Format and display data
            formatted_data = format_funding_data(raw_data)

            # Create table for terminal output
            table_data = [[
                row["Time (UTC)"],
                row["Funding Rate"],
                row["Funding Rate %"],
                row["Annualized %"],
                row["Premium"]
            ] for row in formatted_data]

            headers = ["Time (UTC)", "Funding Rate", "Funding Rate %", "Annualized %", "Premium"]

            print("=" * 60)
            print(f"       Funding Rates for {display_coin}")
            print("=" * 60)
            print()
            print(tabulate(table_data, headers=headers, tablefmt="grid"))
            print()

            # Calculate and display average
            avg_rate = calculate_average_funding(raw_data)
            avg_annualized = annualize_rate(avg_rate)
            print("=" * 60)
            print(f"  Average Funding Rate per Hour: {avg_rate:.8f} ({avg_rate * 100:.6f}%)")
            print(f"  Average Annualized Rate: {avg_annualized * 100:.2f}%")
            print(f"  Total Records: {len(raw_data)}")
            print("=" * 60)
            print()

            # Save to CSV
            filename = save_to_csv(formatted_data, display_coin.replace(":", "_"), avg_rate)
            if filename:
                print(f"Results saved to: {filename}")

        except requests.exceptions.HTTPError as e:
            print(f"API Error: {e}")
            print("Please check if the coin symbol is valid on Hyperliquid.")
        except requests.exceptions.RequestException as e:
            print(f"Network Error: {e}")
            print("Please check your internet connection.")

        print()
        print("-" * 60)
        print()


if __name__ == "__main__":
    main()
