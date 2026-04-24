from __future__ import annotations

import pandas as pd


def dataframe_to_html(df: pd.DataFrame) -> str:
    return df.to_html(index=False, border=0, justify="center")

