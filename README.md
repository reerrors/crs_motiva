# crs_motiva — Documento de Contexto do Projeto

Documentação de contexto do projeto — decisões técnicas, arquitetura, status atual e próximos passos. Serve como referência para qualquer pessoa do time entender onde estamos, sem precisar reabrir debates já resolvidos.

---

## O que é o projeto

App de monitoramento de vegetação rasteira (grama) em acostamentos e canteiros de rodovia, focado num único trecho pra prototipar: **SP-330 (Anhanguera)**, em São Paulo. Objetivo: apoiar decisão de manutenção (poda/roçagem), mostrando num mapa, por km de rodovia, o estado de crescimento da vegetação — visual estilo NYC treemap (mas por km de rodovia, não por árvore), com tema visual dark/HUD tático.


## Decisões técnicas fechadas (e por quê)

1. **Não é detecção de altura literal em cm.** Só satélite gratuito disponível (sem câmera/drone). Sentinel-2 (10m/pixel) não resolve variação de cm de grama fisicamente. SAR/InSAR também descartado (grama muda estrutura rápido demais entre revisitas do radar pra manter coerência de fase).

2. **Abordagem real: NDVI em série temporal** como proxy de vigor de vegetação (não altura). Queda brusca = corte recente. Subida gradual = crescimento. ML (fase futura) classifica a curva em categorias de urgência de manutenção.

3. **Fontes de dado da malha rodoviária: DNIT (VGeo/SNV) + OpenStreetMap.** DNIT dá referência oficial de km (mas representa a via como linha única de eixo, sem separar pista por sentido — sem informação de largura). OSM mapeia vias importantes com pista separada por sentido (`oneway=True`), usado como fonte complementar.

4. **Viabilidade de sensoriamento (mixed pixel): resolvida via ESA WorldCover, não geometria.** Três tentativas geométricas (DNIT-DNIT, OSM-centróide, OSM-linha) foram abandonadas — só mediam canteiro central, não os acostamentos laterais, que são tão importantes quanto pra manutenção real. Solução final: anel (buffer 8-20m, exclui pavimento) ao redor de cada segmento, consulta cobertura de solo real via Planetary Computer, calcula fração de pixels classificados como gramínea (classe 30 do WorldCover). Classificação em **3 níveis** (`low`/`medium`/`high`, calibrados nos quartis reais da distribuição), não binário.

5. **Segmentação: 1km fixo**, usando quilometragem oficial do DNIT — **local por trecho** (reinicia a cada trecho oficial, ex: limite de município), não km global contínuo. É assim que marcos de km funcionam de verdade em campo no Brasil, não é erro.

6. **Escopo do MVP:** só a Anhanguera (SP-330), não a malha do estado inteiro.

7. **Idioma do schema/banco: inglês.** Nomes de coluna e valores de categoria (`low`/`medium`/`high`, não `baixa`/`média`/`alta`) padronizados em inglês pro time inteiro — decisão consciente pra evitar mistura de idioma conforme o projeto cresce.

8. **Classificador de urgência**: treinado sobre a série de NDVI (features: NDVI atual/anterior, variação %, tendência de 3 observações, comparação com a média da rodovia no dia, dias desde a última poda detectada por heurística). Target é a urgência da **próxima** observação (`shift(-1)`), não vazamento de dado — validado que cada feature usa só informação passada/presente. Saída: 3 categorias em inglês (`pruned_recently`/`moderate`/`attention`), aplicadas na observação mais recente de cada segmento e gravadas em `maintenance_status`.

9. **Frontend reconstruído do zero**, substituindo a primeira versão entregue (que usava Leaflet, tema claro convencional, e uma métrica de "altura em cm" fictícia — nenhum dos três alinhado com o que foi combinado). Versão atual: MapLibre GL JS, tema dark/HUD tático (com alternância dark/light), consumindo a API real (`predicted_urgency`, não mais `confidence` como proxy provisório).

## Stack definida

- **Processamento**: Python (`geopandas`, `shapely`, `rasterio`/`rioxarray`, `osmnx`, `pystac-client`, `planetary-computer`)
- **Banco**: PostgreSQL 16 + PostGIS 3.4 — criado e rodando localmente (WSL2)
- **Carga**: SQLAlchemy + `geopandas.to_postgis` / `pandas.to_sql`, com mapeamento explícito de nomes Python (português) → SQL (inglês)
- **Orquestração** (planejada, não implementada): Prefect ou Dagster
- **API**: FastAPI, containerizada junto com o banco no `docker-compose.yml`. Endpoints: `/health`, `/segments` (GeoJSON, filtros por `confidence`/`road_code`/`predicted_urgency`), `/segments/{id}`, `/segments/{id}/ndvi`
- **ML**: LightGBM (`LGBMClassifier`), features de série temporal sobre NDVI, salvo via `joblib`
- **Frontend**: MapLibre GL JS (não Leaflet) + tema dark/HUD com alternância dark/light, basemap CARTO (dark/light conforme tema). HTML/CSS/JS puro, sem framework
- **Complementares**: Open-Meteo (clima), GBIF + Flora e Funga do Brasil (espécies) — ainda não integrados ao pipeline
- **Ambiente**: WSL2/Ubuntu, VS Code Remote-WSL, venv (`.venv`)
- **Versionamento**: Git + GitHub, autenticação via SSH configurada

## Divisão de trabalho (6 pessoas)

| Pessoa | Frente | Status |
|---|---|---|
| André E. Martins | `db/schema.sql` + `pipeline/load/write_to_postgis.py` + integração final | [feito] Feito |
| Rafael Vaz | `pipeline/ml/` — features + classificador de urgência sobre a série de NDVI | [feito] Modelo treinado e validado (sem vazamento de dado), integrado ao banco via `predict_urgencia.py` |
| Kauã Peres e Kauany Ribeiro | `frontend/` (MapLibre + tema HUD) | [feito] Reconstruído (versão original não seguia a stack/dado combinados) — mapa, painel com abas, navegador de km, tema dark/light |
| Lucas Kenzo e Felipe Hui | `api/` (FastAPI) | [feito] Endpoints validados, containerizada no Docker Compose |
| André E. Martins | Docker Compose + ambiente do time | [feito] Banco + API sobem juntos, já populados |

## Arquitetura de diretórios (status atual)

```
crs_motiva/
├── data/{raw,interim,processed}/     (fora do Git — .gitignore)
├── db/
│   ├── schema.sql                     [feito] 4 tabelas: segments, viability, ndvi_observations, maintenance_status
│   └── init/01_seed.sql               [feito] dump completo (schema + dados), carregado automaticamente pelo Docker Compose
├── pipeline/
│   ├── ingest/
│   │   ├── fetch_dnit_network.py      [feito] feito e validado
│   │   └── fetch_osm_network.py       [feito] feito e validado
│   ├── process/
│   │   ├── segment_highway.py         [feito] feito e validado
│   │   ├── viability_filter.py        [feito] feito (via ESA WorldCover, 3 níveis)
│   │   └── calc_ndvi.py               [feito] feito, otimizado, rodado nos 592 segmentos completos
│   ├── ml/
│   │   ├── model/modelo_urgencia_sp330.pkl  [feito] modelo treinado (LightGBM)
│   │   └── predict_urgencia.py        [feito] aplica o modelo, grava em maintenance_status
│   └── load/
│       └── write_to_postgis.py        [feito] feito e validado
├── docker-compose.yml                   [feito] PostGIS + API, seed automático
├── orchestration/                      [pendente] não iniciado
├── api/
│   ├── main.py                          [feito] endpoints validados
│   ├── database.py                      [feito]
│   └── Dockerfile                       [feito]
├── frontend/
│   ├── index.html / style.css / script.js  [feito] reconstruído (MapLibre, tema dark/HUD)
├── notebooks/                           (scripts de exploração/execução pontual)
└── .env / .env.example / .gitignore / requirements.txt   [feito] configurados
```

## Schema do banco (nomes finais, em inglês)

**`segments`**: `segment_id` (PK), `road_code`, `km_start`, `km_end`, `track_origin_id`, `track_start_name`, `track_end_name`, `geometry(LineString, 31983)`. Índice GIST em `geometry`. Constraint `chk_continuity` (`km_end > km_start`).

**`viability`**: `viability_id` (PK), `segment_id` (FK), `grass_ratio`, `confidence` (`low`/`medium`/`high`, com CHECK), `calculus_date` (default now).

**`ndvi_observations`**: `ndvi_id` (PK), `segment_id` (FK), `date_capture`, `ndvi_avg` (CHECK entre -1 e 1), `valid_pixels`. Índice composto em `(segment_id, date_capture)`.

**`maintenance_status`**: `status_id` (PK), `segment_id` (FK), `predicted_urgency` (`pruned_recently`/`moderate`/`attention`, com CHECK), `predicted_at` (default now). Gerada pelo `predict_urgencia.py`, aplicando o modelo de ML na observação mais recente de cada segmento. Truncada e regravada a cada nova rodada de previsão (só a previsão mais atual por segmento importa).

## Pipeline de NDVI — decisões técnicas importantes

- Fonte: Sentinel-2 L2A via Microsoft Planetary Computer (mesmo catálogo usado pro WorldCover)
- **Cada cena é assinada individualmente, na hora de usar** — não em lote no início (links assinados expiram em ~1h, processar centenas de segmentos demora mais que isso; essa foi uma causa real de perda silenciosa de dado numa rodada anterior, já corrigida)
- **Leitura em lotes (chunks de 40 segmentos)**, não por segmento individual nem tudo de uma vez — equilíbrio entre poucas requisições grandes (retângulo de leitura gigante, rodovia é comprida) e muitas requisições pequenas (lento por excesso de round-trips). Essa mudança reduziu o tempo de processamento de ~62s/cena pra ~3,7s/cena
- Mascaramento de nuvem pixel a pixel via banda SCL (reamostrada de 20m pra 10m via vizinho mais próximo, pra bater com B04/B08)
- Correção do offset da baseline 04.00+ do Sentinel-2 (+1000 nos valores de banda)
- Agregação por segmento+data com média ponderada por pixels válidos (resolve sobreposição de cenas adjacentes)
- Filtro de qualidade mínima: descarta observações com menos de 100 pixels válidos
- **Salvamento incremental com capacidade de retomada**: cada cena processada é gravada imediatamente em `data/interim/ndvi_raw.csv`, com controle de progresso em arquivo `.processed_ids.txt` — se o processo for interrompido, rodar de novo pula o que já foi feito

## Resultado da rodada completa (592 segmentos, 1 ano)

- 24.687 observações totais
- 540 de 592 segmentos com pelo menos 1 observação válida (52 sem nenhuma — gap de cobertura/nuvem persistente em pontos específicos, limitação conhecida)
- Média de ~46 observações por segmento no ano
- 15 segmentos com menos de 5 observações (série curta demais pra tendência confiável — sinalizado no guia enviado pro ML)
- Padrão sazonal confirmado nos dados: NDVI mais alto no verão/chuva, mais baixo no inverno/seca — consistente com clima real de SP, valida que o sinal captura vegetação de verdade

## Docker Compose

`docker-compose.yml` sobe um container PostgreSQL 16 + PostGIS 3.4, e na primeira subida (volume vazio) carrega automaticamente `db/init/01_seed.sql` — um dump completo (schema + dados atuais) gerado via `pg_dump`. Isso permite que qualquer pessoa do time, mesmo sem Linux/WSL2, tenha o banco completo rodando com:
```bash
docker compose up -d
```
sem precisar repetir os passos manuais (criar banco, senha, extensão PostGIS) que o André fez originalmente.

**Detalhe técnico resolvido:** o dump gerado pela versão local do `pg_dump` (16.14) incluiu comandos `\restrict`/`\unrestrict`, não reconhecidos pela versão de `psql` dentro da imagem Docker usada — removidos do dump com `sed` antes de funcionar.

**Se precisar recarregar o seed** (dado mudou, quer atualizar o que o time recebe): gera um novo dump, substitui `db/init/01_seed.sql`, e roda `docker compose down -v && docker compose up -d` (o `-v` remove o volume, forçando a próxima subida a recarregar do zero).

Guia completo com instruções de conexão, schema e endpoints sugeridos: `guia_api.md`.

## API

FastAPI, containerizada junto com o banco no `docker-compose.yml` (serviço `api`, porta 8000, `depends_on: db` com `condition: service_healthy`). Endpoints validados:
- `GET /health` — checagem de conexão com banco
- `GET /segments` — GeoJSON de todos os segmentos, com `viability` e `maintenance_status` mais recentes via `LATERAL JOIN`. Filtros opcionais: `confidence`, `road_code`, `predicted_urgency`
- `GET /segments/{segment_id}` — detalhe de um segmento
- `GET /segments/{segment_id}/ndvi` — série temporal de NDVI

Queries parametrizadas (sem risco de SQL injection), erros de banco logados no servidor sem vazar detalhe pro cliente, CORS liberado. Decisão confirmada: frontend e API desacoplados (API não serve arquivos estáticos do frontend).

## Machine Learning

Modelo treinado em notebook separado (Colab), validado quanto a vazamento de dado (nenhum encontrado — a técnica de deslocar o alvo pra frente com `shift(-1)` é forecasting legítimo, todas as features usam só passado/presente). `pipeline/ml/predict_urgencia.py` recalcula as mesmas features sobre o histórico completo do banco, aplica o modelo na observação mais recente de cada segmento, e grava em `maintenance_status`.

**Resultado da última rodada**: 337 de 592 segmentos com previsão (os demais não tinham histórico suficiente pra calcular todas as features) — 176 `attention`, 119 `moderate`, 42 `pruned_recently`.

**Atenção pra quem for retreinar**: o `.pkl` é sensível à versão do `scikit-learn` usada no treino (já causou um `InconsistentVersionWarning` real por divergência de versão entre Colab e ambiente local) — sempre fixar/registrar a versão exata junto com o modelo.

## Frontend

Reconstruído do zero depois que a primeira entrega não seguiu o combinado (Leaflet em vez de MapLibre, tema claro convencional em vez de dark/HUD, e uma métrica de "altura em cm" inventada, sem correspondência com o dado real da API).

**Stack**: HTML/CSS/JS puro (sem framework), MapLibre GL JS, basemap CARTO (dark/light conforme tema ativo).

**Funcionalidades**:
- Mapa com segmentos coloridos por `predicted_urgency`
- Painel lateral com abas: **Detalhes** (segmento selecionado, série de NDVI em mini-gráfico), **Análise** (estatísticas agregadas calculadas em tempo real sobre os dados carregados), **Agendados** (lista de manutenções agendadas)
- Navegador de KM (barra inferior), complementar ao clique no mapa — mostra trecho anterior/próximo, navega sequencialmente
- Alternância de tema dark/light, incluindo troca do basemap do mapa
- Agendamento de corte: **mockado** (guardado só em memória do navegador, não persiste no backend) — sinalizado explicitamente na interface

## Entrega em vídeo

Roteiro de vídeo demonstrativo (~3 min) criado, seguindo estrutura exigida pelo edital: identificação da equipe, testes do protótipo (incluindo explicação honesta da limitação de altura em cm e a adaptação pro NDVI — o edital permite explicitamente apresentar limitações), demonstração da plataforma. Diagrama do pipeline (satélite → NDVI → viabilidade → classificador ML) gerado para uso no vídeo.

## Status geral

Todas as frentes principais concluídas: pipeline de dados, banco, API, modelo de ML integrado, frontend. Projeto funcional de ponta a ponta — do dado de satélite até a visualização no mapa com previsão de urgência.

## Pendências conhecidas (não bloqueiam a entrega)

1. `orchestration/` (Prefect/Dagster) — automação de atualização periódica, não iniciado
2. Agendamento de manutenção ainda mockado (sem endpoint/tabela de persistência)
3. 255 segmentos sem previsão de urgência (histórico insuficiente) — aceito como limitação conhecida por ora
4. Complementares (Open-Meteo, GBIF) não integrados ao pipeline nem à API

## Cadência de atualização esperada (realista, já discutida)

- Malha rodoviária (DNIT/OSM): mensal ou trimestral
- NDVI/Sentinel-2: semanal a quinzenal na prática (cobertura de nuvem reduz a cadência teórica de ~5 dias)
- Clima (Open-Meteo): pode ser diário
- D-1 não é realista pro indicador principal (vegetação não muda perceptivelmente de um dia pro outro)
