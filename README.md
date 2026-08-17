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

## Stack definida

- **Processamento**: Python (`geopandas`, `shapely`, `rasterio`/`rioxarray`, `osmnx`, `pystac-client`, `planetary-computer`)
- **Banco**: PostgreSQL 16 + PostGIS 3.4 — criado e rodando localmente (WSL2)
- **Carga**: SQLAlchemy + `geopandas.to_postgis` / `pandas.to_sql`, com mapeamento explícito de nomes Python (português) → SQL (inglês)
- **Orquestração** (planejada, não implementada): Prefect ou Dagster
- **API**: FastAPI (não iniciado, ninguém alocado ainda)
- **Frontend**: MapLibre GL JS (não Leaflet — WebGL, fork open source do Mapbox GL JS antes da mudança de licença) + tema dark/HUD. Guia enviado pra pessoa responsável, com `segments_exemplo.geojson`
- **Complementares**: Open-Meteo (clima), GBIF + Flora e Funga do Brasil (espécies) — ainda não integrados ao pipeline
- **Ambiente**: WSL2/Ubuntu, VS Code Remote-WSL, venv (`.venv`)
- **Versionamento**: Git + GitHub, autenticação via SSH configurada

## Divisão de trabalho (6 pessoas)

| Pessoa | Frente | Status |
|---|---|---|
| André | `db/schema.sql` + `pipeline/load/write_to_postgis.py` | [feito] Feito |
| 1 (ML) | `pipeline/ml/` — features + classificador de urgência sobre a série de NDVI | [em andamento] Recebeu `ndvi_para_ml.csv` + guia, começando |
| 1 (Frontend) | `frontend/` (MapLibre + tema HUD) | [em andamento] Recebeu guia + `segments_exemplo.geojson`, começando sem esperar API |
| 1-2 (API) | `api/` (FastAPI) | [pendente] Não alocado ainda |
| 1 (Infra) | Docker Compose + ambiente do time | [pendente] Não iniciado — importante pra quem não tem WSL2/Linux conseguir rodar o banco |

## Arquitetura de diretórios (status atual)

```
crs_motiva/
├── data/{raw,interim,processed}/     (fora do Git — .gitignore)
├── db/
│   └── schema.sql                     [feito] 3 tabelas: segments, viability, ndvi_observations
├── pipeline/
│   ├── ingest/
│   │   ├── fetch_dnit_network.py      [feito] feito e validado
│   │   └── fetch_osm_network.py       [feito] feito e validado
│   ├── process/
│   │   ├── segment_highway.py         [feito] feito e validado
│   │   ├── viability_filter.py        [feito] feito (via ESA WorldCover, 3 níveis)
│   │   └── calc_ndvi.py               [feito] feito, otimizado, rodado nos 592 segmentos completos
│   ├── ml/                             [pendente] começando agora (colega de ML)
│   └── load/
│       └── write_to_postgis.py        [feito] feito e validado
├── orchestration/                      [pendente] não iniciado
├── api/                                 [pendente] não iniciado
├── frontend/                            [em andamento] começando agora
├── notebooks/                           (scripts de exploração/execução pontual)
└── .env / .env.example / .gitignore    [feito] configurados
```

## Schema do banco (nomes finais, em inglês)

**`segments`**: `segment_id` (PK), `road_code`, `km_start`, `km_end`, `track_origin_id`, `track_start_name`, `track_end_name`, `geometry(LineString, 31983)`. Índice GIST em `geometry`. Constraint `chk_continuity` (`km_end > km_start`).

**`viability`**: `viability_id` (PK), `segment_id` (FK), `grass_ratio`, `confidence` (`low`/`medium`/`high`, com CHECK), `calculus_date` (default now).

**`ndvi_observations`**: `ndvi_id` (PK), `segment_id` (FK), `date_capture`, `ndvi_avg` (CHECK entre -1 e 1), `valid_pixels`. Índice composto em `(segment_id, date_capture)`.

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

## Próximos passos imediatos

1. Alocar pessoa(s) pra `api/` (FastAPI) — depende de ter acesso ao banco (Linux/WSL2 direto, ou aguardar Docker Compose)
2. Escrever `docker-compose.yml` (PostgreSQL/PostGIS) — prioridade pra quem do time não tem Linux
3. ML: colega já com dataset em mãos, construindo features e primeiro classificador
4. Frontend: colega já com dado de exemplo, construindo mapa MapLibre + tema HUD
5. Mais pra frente: `orchestration/` (Prefect/Dagster) pra automatizar atualização periódica do NDVI

## Cadência de atualização esperada (realista, já discutida)

- Malha rodoviária (DNIT/OSM): mensal ou trimestral
- NDVI/Sentinel-2: semanal a quinzenal na prática (cobertura de nuvem reduz a cadência teórica de ~5 dias)
- Clima (Open-Meteo): pode ser diário
- D-1 não é realista pro indicador principal (vegetação não muda perceptivelmente de um dia pro outro)
