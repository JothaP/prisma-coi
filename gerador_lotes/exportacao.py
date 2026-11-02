import io

import pandas as pd


def dataframe_para_excel(
    df,
    nome_aba="Resultado"
):

    if df is None or df.empty:
        return None

    buffer = io.BytesIO()

    with pd.ExcelWriter(
        buffer,
        engine="openpyxl"
    ) as writer:

        df.to_excel(
            writer,
            index=False,
            sheet_name=nome_aba
        )

    buffer.seek(0)

    return buffer.getvalue()
