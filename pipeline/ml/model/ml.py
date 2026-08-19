# -*- coding: utf-8 -*-
"""
treino_modelo_urgencia_revisado.py

Baseado no notebook original do colega de ML -- lógica de feature
engineering, heurística de poda e definição de target mantidas
intactas (revisão confirmou: não há vazamento de dado real, a técnica
de shift(-1) no alvo é forecasting legítimo, cada feature usa só
informação passada/presente).

Duas correções de robustez adicionadas:
1. Proteção contra divisão por valor muito pequeno em variacao_ndvi_pct
   (evita percentuais explosivos quando ndvi_anterior é próximo de zero)
2. Checagem do suporte de cada classe no conjunto de teste, pra saber
   se o classification_report é confiável pra todas as 3 categorias
"""

import pandas as pd
import numpy as np
from datetime import timedelta

# ---------------------------------------------------------
# 1. CARREGAMENTO E LIMPEZA INICIAL
# ---------------------------------------------------------
df = pd.read_csv('data/processed/ndvi_para_ml.csv')

df['data'] = pd.to_datetime(df['data'])
df = df.sort_values(by=['segment_id', 'data']).reset_index(drop=True)

# Filtra segmentos com confiança alta e com pelo menos 5 observações.
# Decisão intencional (documentada pelo autor original): validar
# primeiro no sinal mais limpo antes de generalizar pra medium/low.
df = df[df['confianca_viabilidade'] == 'high'].copy()
contagem = df['segment_id'].value_counts()
segmentos_validos = contagem[contagem >= 5].index
df = df[df['segment_id'].isin(segmentos_validos)].copy()

# ---------------------------------------------------------
# 2. ENGENHARIA DE FEATURES
# ---------------------------------------------------------
df['ndvi_anterior'] = df.groupby('segment_id')['ndvi_medio'].shift(1)
df['data_anterior'] = df.groupby('segment_id')['data'].shift(1)
df['dias_entre_fotos'] = (df['data'] - df['data_anterior']).dt.days

# CORREÇÃO: proteção contra denominador muito pequeno -- evita
# percentual explosivo quando ndvi_anterior está perto de zero
DENOMINADOR_MINIMO = 0.02
denominador_seguro = df['ndvi_anterior'].where(
    df['ndvi_anterior'].abs() >= DENOMINADOR_MINIMO, np.nan
)
df['variacao_ndvi_pct'] = (df['ndvi_medio'] - df['ndvi_anterior']) / denominador_seguro

df['tendencia_ndvi_3obs'] = df.groupby('segment_id')['ndvi_medio'].transform(
    lambda x: x.rolling(3, min_periods=1).mean()
)

media_rodovia_dia = df.groupby('data')['ndvi_medio'].transform('mean')
df['ndvi_vs_media_rodovia'] = df['ndvi_medio'] - media_rodovia_dia

# ---------------------------------------------------------
# 3. HEURÍSTICA DE PODA E TEMPO
# ---------------------------------------------------------
limite_queda = -0.20
df['teve_poda'] = np.where(
    (df['variacao_ndvi_pct'] <= limite_queda) & (df['dias_entre_fotos'] <= 20), 1, 0
)

df['data_ultima_poda'] = df.apply(
    lambda x: x['data'] if x['teve_poda'] == 1 else pd.NaT, axis=1
)
df['data_ultima_poda'] = df.groupby('segment_id')['data_ultima_poda'].ffill()
df['dias_desde_ultima_poda'] = (df['data'] - df['data_ultima_poda']).dt.days

# ---------------------------------------------------------
# 4. CRIAÇÃO DO TARGET
# ---------------------------------------------------------
def definir_urgencia(row):
    dias = row['dias_desde_ultima_poda']
    ndvi = row['ndvi_medio']

    if pd.isna(dias):
        return 1 if ndvi <= 0.4 else 2

    if dias <= 15:
        return 0  # Recém-podado
    elif dias >= 60 and ndvi > 0.35:
        return 2  # Atenção
    else:
        return 1  # Moderado

df['target_urgencia'] = df.apply(definir_urgencia, axis=1)
df_final = df.dropna(subset=['ndvi_anterior']).copy()

print("Distribuição do target (dataset completo, antes do split):")
print(df_final['target_urgencia'].value_counts())
print()

# ---------------------------------------------------------
# 5. FEATURES E TARGET FUTURO
# ---------------------------------------------------------
from lightgbm import LGBMClassifier
from sklearn.metrics import classification_report
import joblib

features = [
    'ndvi_medio', 'ndvi_anterior', 'dias_entre_fotos', 'variacao_ndvi_pct',
    'tendencia_ndvi_3obs', 'ndvi_vs_media_rodovia', 'dias_desde_ultima_poda',
]

df_modelo = df_final.sort_values(by=['segment_id', 'data']).reset_index(drop=True)

# Alvo = urgência da PRÓXIMA observação daquele segmento. Não é
# vazamento: as features de entrada usam só info passada/presente,
# só o alvo é deslocado -- é o setup padrão de forecasting 1-passo-à-frente.
df_modelo['urgencia_futura'] = df_modelo.groupby('segment_id')['target_urgencia'].shift(-1)
df_modelo = df_modelo.dropna(subset=['urgencia_futura'] + features).copy()

y = df_modelo['urgencia_futura']

# Split temporal global (treino no passado, teste no futuro)
df_modelo = df_modelo.sort_values('data').reset_index(drop=True)
tamanho_treino = int(len(df_modelo) * 0.80)

treino = df_modelo.iloc[:tamanho_treino]
teste = df_modelo.iloc[tamanho_treino:]

X_train, y_train = treino[features], treino['urgencia_futura']
X_test, y_test = teste[features], teste['urgencia_futura']

print(f"Treino: {treino['data'].min().date()} até {treino['data'].max().date()} ({len(treino)} registros)")
print(f"Teste: {teste['data'].min().date()} até {teste['data'].max().date()} ({len(teste)} registros)\n")

# CORREÇÃO: checa suporte de cada classe no teste ANTES de treinar --
# se alguma classe tiver poucas/nenhuma observação no teste, o
# classification_report não é confiável pra ela
print("Suporte de cada classe no conjunto de TESTE:")
print(y_test.value_counts().sort_index())
print()

# ---------------------------------------------------------
# 6. TREINAMENTO
# ---------------------------------------------------------
modelo = LGBMClassifier(
    n_estimators=150, learning_rate=0.05, max_depth=5,
    class_weight='balanced', random_state=42,
)
modelo.fit(X_train, y_train)

# ---------------------------------------------------------
# 7. AVALIAÇÃO
# ---------------------------------------------------------
previsoes = modelo.predict(X_test)
print("=== RELATÓRIO DE CLASSIFICAÇÃO (PREVENDO O FUTURO) ===")
print(classification_report(
    y_test, previsoes, target_names=['0: Recém-podado', '1: Moderado', '2: Atenção']
))

importancia = pd.DataFrame({
    'Feature': features, 'Importância': modelo.feature_importances_,
}).sort_values(by='Importância', ascending=False).reset_index(drop=True)
print("\n=== IMPORTÂNCIA DAS VARIÁVEIS ===")
print(importancia)

# ---------------------------------------------------------
# 8. SALVAR O MODELO
# ---------------------------------------------------------
caminho_arquivo = 'pipeline/ml/model/modelo_urgencia_sp330.pkl'
joblib.dump(modelo, caminho_arquivo)
print(f"\nModelo salvo em: {caminho_arquivo}")
