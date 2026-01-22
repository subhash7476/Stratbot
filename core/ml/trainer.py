import pandas as pd
from pathlib import Path

def load_parquet(path):
    return pd.read_parquet(path)

def save_parquet(df, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
