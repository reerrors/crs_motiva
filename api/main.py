import json
import logging
import os
from datetime import date
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from api.database import get_db

logger = logging.getLogger(__name__)


class GeoJSONResponse(JSONResponse):
    media_type = "application/geo+json"


class HealthResponse(BaseModel):
    status: str
    database: str


class NDVIObservation(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    date_capture: date
    ndvi_avg: float
    valid_pixels: int


class NDVISeriesResponse(BaseModel):
    segment_id: str
    observations: list[NDVIObservation]


app = FastAPI(
    title="CRS Motiva API",
    description="API de segmentos rodoviários e série temporal de NDVI da SP-330.",
    version="1.0.0",
)

cors_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "*").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)


SEGMENT_SELECT = """
    SELECT
        s.segment_id,
        s.road_code,
        s.km_start,
        s.km_end,
        s.track_origin_id,
        s.track_start_name,
        s.track_end_name,
        v.grass_ratio,
        v.confidence,
        v.calculus_date,
        m.predicted_urgency,
        m.predicted_at,
        ST_AsGeoJSON(ST_Transform(s.geometry, 4326)) AS geometry
    FROM segments AS s
    LEFT JOIN LATERAL (
        SELECT grass_ratio, confidence, calculus_date
        FROM viability
        WHERE segment_id = s.segment_id
        ORDER BY calculus_date DESC, viability_id DESC
        LIMIT 1
    ) AS v ON TRUE
    LEFT JOIN LATERAL (
        SELECT predicted_urgency, predicted_at
        FROM maintenance_status
        WHERE segment_id = s.segment_id
        ORDER BY predicted_at DESC, status_id DESC
        LIMIT 1
    ) AS m ON TRUE
"""


def _feature_from_row(row: object) -> dict:
    values = dict(row._mapping)
    geometry = values.pop("geometry")
    if isinstance(geometry, str):
        geometry = json.loads(geometry)
    return {
        "type": "Feature",
        "id": values["segment_id"],
        "geometry": geometry,
        "properties": values,
    }


@app.exception_handler(SQLAlchemyError)
async def database_error_handler(_request, exc: SQLAlchemyError) -> JSONResponse:
    # registra o erro real no log do servidor -- a resposta pro
    # cliente continua genérica de propósito (não vaza detalhe
    # interno de banco), mas sem isso ninguém saberia o que quebrou
    logger.error("Erro de banco de dados: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=503,
        content={"detail": "Banco de dados temporariamente indisponível."},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"detail": jsonable_encoder(exc.errors())},
    )


@app.get("/", tags=["Metadados"])
def root() -> dict:
    return {
        "name": "CRS Motiva API",
        "version": "1.0.0",
        "docs": "/docs",
    }


@app.get("/health", response_model=HealthResponse, tags=["Metadados"])
def health(db: Annotated[Session, Depends(get_db)]) -> HealthResponse:
    db.execute(text("SELECT 1"))
    return HealthResponse(status="ok", database="connected")


@app.get("/segments", response_class=GeoJSONResponse, tags=["Segmentos"])
def list_segments(
    db: Annotated[Session, Depends(get_db)],
    confidence: Annotated[
        Literal["low", "medium", "high"] | None,
        Query(description="Filtra pela confiança da viabilidade mais recente."),
    ] = None,
    road_code: Annotated[
        str | None,
        Query(min_length=1, max_length=10, description="Filtra pelo código da rodovia."),
    ] = None,
    predicted_urgency: Annotated[
        Literal["pruned_recently", "moderate", "attention"] | None,
        Query(description="Filtra pela urgência de manutenção prevista mais recente."),
    ] = None,
) -> dict:
    conditions: list[str] = []
    params: dict[str, object] = {}

    if confidence is not None:
        conditions.append("v.confidence = :confidence")
        params["confidence"] = confidence
    if road_code is not None:
        conditions.append("s.road_code = :road_code")
        params["road_code"] = road_code
    if predicted_urgency is not None:
        conditions.append("m.predicted_urgency = :predicted_urgency")
        params["predicted_urgency"] = predicted_urgency

    query = SEGMENT_SELECT
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY s.road_code, s.track_origin_id NULLS LAST, s.km_start, s.segment_id"

    rows = db.execute(text(query), params).all()
    features = [_feature_from_row(row) for row in rows]
    return {
        "type": "FeatureCollection",
        "features": features,
        "numberReturned": len(features),
    }


@app.get(
    "/segments/{segment_id}",
    response_class=GeoJSONResponse,
    tags=["Segmentos"],
)
def get_segment(segment_id: str, db: Annotated[Session, Depends(get_db)]) -> dict:
    query = SEGMENT_SELECT + " WHERE s.segment_id = :segment_id"
    row = db.execute(text(query), {"segment_id": segment_id}).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Segmento não encontrado.")
    return _feature_from_row(row)


@app.get(
    "/segments/{segment_id}/ndvi",
    response_model=NDVISeriesResponse,
    tags=["NDVI"],
)
def get_segment_ndvi(
    segment_id: str, db: Annotated[Session, Depends(get_db)]
) -> NDVISeriesResponse:
    exists = db.execute(
        text("SELECT EXISTS(SELECT 1 FROM segments WHERE segment_id = :segment_id)"),
        {"segment_id": segment_id},
    ).scalar_one()
    if not exists:
        raise HTTPException(status_code=404, detail="Segmento não encontrado.")

    rows = db.execute(
        text(
            """
            SELECT date_capture, ndvi_avg, valid_pixels
            FROM ndvi_observations
            WHERE segment_id = :segment_id
            ORDER BY date_capture
            """
        ),
        {"segment_id": segment_id},
    ).mappings()
    observations = [NDVIObservation.model_validate(row) for row in rows]
    return NDVISeriesResponse(segment_id=segment_id, observations=observations)
