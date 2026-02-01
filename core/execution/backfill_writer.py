"""
Backfill Writer
---------------
Saves backfill artifacts to disk (CSV/JSON).
"""
import pandas as pd
from pathlib import Path
from typing import List, Dict, Any

class BackfillWriter:
    def __init__(self, output_dir: str = "backfills"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write_results(self, strategy_id: str, symbol: str, run_id: str, trades: List[Dict[str, Any]]):
        path = self.output_dir / strategy_id / symbol / run_id
        path.mkdir(parents=True, exist_ok=True)
        
        if trades:
            df = pd.DataFrame(trades)
            df.to_csv(path / "trades.csv", index=False)
