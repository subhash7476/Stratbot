"""
Daily Review Script
-------------------
Automates the post-session review process.
"""
import argparse
from datetime import datetime

def run_review(date_str: str):
    print(f"Running daily review for {date_str}...")
    # 1. Fetch trades
    # 2. Link to TradeTruth
    # 3. Generate Markdown summary
    print("✅ Review complete. See logs/sessions/")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    args = parser.parse_args()
    run_review(args.date)
