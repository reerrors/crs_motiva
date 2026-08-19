"""
pipeline/ml/predict_urgencia.py

Aplica o modelo treinado (modelo_urgencia_sp330.pkl) na observação
MAIS RECENTE de cada segmento, gerando uma previsão de urgência de
manutenção. Recalcula as mesmas features usadas no treino (precisa do
histórico completo por segmento pra isso -- rolling, shift, ffill de
poda -- mas só a última linha de cada segmento é efetivamente usada
como entrada do modelo).

O resultado é gravado numa tabela nova no banco (maintenance_status),
pronta pra API expor e o frontend consumir.
"""

import numpy as np
import pandas as pd
import joblib
from sqlalchemy import text

FEATURES = [
    "ndvi_medio", "ndvi_anterior", "dias_entre_fotos", "variacao_ndvi_pct",
    "tendencia_ndvi_3obs", "ndvi_vs_media_rodovia", "dias_desde_ultima_poda",
]

DENOMINADOR_MINIMO = 0.02

# mapeamento da saída numérica do modelo (0/1/2) pro rótulo em inglês
# que vai pro banco -- consistente com o resto do schema
URGENCY_LABELS = {
    0: "pruned_recently",
    1: "moderate",
    2: "attention",
}


def _calcular_features(df: pd.DataFrame) -> pd.DataFrame:
    """Reaplica a mesma engenharia de features usada no treino."""
    df = df.copy()
    df["data"] = pd.to_datetime(df["data"])
    df = df.sort_values(by=["segment_id", "data"]).reset_index(drop=True)

    df["ndvi_anterior"] = df.groupby("segment_id")["ndvi_medio"].shift(1)
    df["data_anterior"] = df.groupby("segment_id")["data"].shift(1)
    df["dias_entre_fotos"] = (df["data"] - df["data_anterior"]).dt.days

    denominador_seguro = df["ndvi_anterior"].where(
        df["ndvi_anterior"].abs() >= DENOMINADOR_MINIMO, np.nan
    )
    df["variacao_ndvi_pct"] = (df["ndvi_medio"] - df["ndvi_anterior"]) / denominador_seguro

    df["tendencia_ndvi_3obs"] = df.groupby("segment_id")["ndvi_medio"].transform(
        lambda x: x.rolling(3, min_periods=1).mean()
    )

    media_rodovia_dia = df.groupby("data")["ndvi_medio"].transform("mean")
    df["ndvi_vs_media_rodovia"] = df["ndvi_medio"] - media_rodovia_dia

    limite_queda = -0.20
    df["teve_poda"] = np.where(
        (df["variacao_ndvi_pct"] <= limite_queda) & (df["dias_entre_fotos"] <= 20), 1, 0
    )
    df["data_ultima_poda"] = df.apply(
        lambda x: x["data"] if x["teve_poda"] == 1 else pd.NaT, axis=1
    )
    df["data_ultima_poda"] = df.groupby("segment_id")["data_ultima_poda"].ffill()
    df["dias_desde_ultima_poda"] = (df["data"] - df["data_ultima_poda"]).dt.days

    return df


def predict_urgencia_atual(
    df_ndvi: pd.DataFrame,
    modelo_path: str = "pipeline/ml/model/modelo_urgencia_sp330.pkl",
) -> pd.DataFrame:
    """
    Recebe o histórico completo de NDVI (todas as observações, todos
    os segmentos), calcula as features, e prevê a urgência a partir da
    observação mais recente de cada segmento.

    Retorna um DataFrame com: segment_id, predicted_urgency
    """
    df_features = _calcular_features(df_ndvi)

    # pega só a última observação de cada segmento -- é o "estado atual"
    ultima_obs = (
        df_features.sort_values("data").groupby("segment_id").tail(1).copy()
    )

    # remove segmentos sem histórico suficiente pra calcular todas as
    # features (ex: só 1 observação nunca teria ndvi_anterior)
    ultima_obs = ultima_obs.dropna(subset=FEATURES)

    if ultima_obs.empty:
        return pd.DataFrame(columns=["segment_id", "predicted_urgency"])

    modelo = joblib.load(modelo_path)
    previsoes_num = modelo.predict(ultima_obs[FEATURES])

    resultado = pd.DataFrame({
        "segment_id": ultima_obs["segment_id"].values,
        "predicted_urgency": [URGENCY_LABELS[p] for p in previsoes_num],
    })
    return resultado


def write_maintenance_status(df_status: pd.DataFrame, engine, if_exists: str = "append"):
    """
    Grava as previsões na tabela maintenance_status. Como é um
    resultado que muda a cada rodada, limpa a tabela antes de gravar
    de novo -- só a previsão mais recente por segmento importa.
    """
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE maintenance_status"))
    df_status.to_sql("maintenance_status", engine, if_exists="append", index=False)
    print(f"[predict_urgencia] {len(df_status)} previsão(ões) gravada(s) em 'maintenance_status'.")


if __name__ == "__main__":
    import pandas as pd
    from pipeline.load.write_to_postgis import get_engine

    engine = get_engine()

    df_ndvi = pd.read_sql(
        "SELECT segment_id, date_capture AS data, ndvi_avg AS ndvi_medio FROM ndvi_observations",
        engine,
    )

    df_status = predict_urgencia_atual(df_ndvi)
    print(df_status["predicted_urgency"].value_counts())

    write_maintenance_status(df_status, engine)
