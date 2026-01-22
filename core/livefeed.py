import streamlit as st
st.write("Testing imports...")

import data.data_manager
st.write("Imported data_manager.")

import data_manager  # if different module path exists
st.write("Imported data_manager directly.")


def preflight_plan(symbol: str, interval: str):
    last = get_last_saved_date(symbol, interval)
    if last is None:
        next_day = None
    else:
        next_day = last + timedelta(days=1)

    return {
        "last_saved": last.isoformat() if last else None,
        "next_day_to_fetch": next_day.isoformat() if next_day else None
    }


def list_partitions(symbol: str, interval: str):
    folder = DATA_BASE / symbol / interval
    if not folder.exists():
        return []

    parts = []
    for y in folder.glob("year=*"):
        for m in y.glob("month=*"):
            for d in m.glob("day=*"):
                f = d / "data.parquet"
                if f.exists():
                    parts.append(str(f))

    return sorted(parts)


