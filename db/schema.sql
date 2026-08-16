CREATE TABLE segments(
    segment_id VARCHAR(100) NOT NULL,
    road_code VARCHAR(10) NOT NULL,
    km_start DOUBLE PRECISION NOT NULL,
    km_end  DOUBLE PRECISION NOT NULL,
    track_origin_id VARCHAR(65),
    track_start_name VARCHAR(100),
    track_end_name VARCHAR(100),
    geometry geometry(LINESTRING,31983) NOT NULL,

    CONSTRAINT chk_continuity CHECK(km_end > km_start),
    PRIMARY KEY(segment_id)
);

CREATE INDEX idx_geom
ON segments
USING GIST(geometry);

CREATE TABLE ndvi_observations(
   ndvi_id INTEGER GENERATED ALWAYS AS IDENTITY,
   segment_id VARCHAR(100) NOT NULL,
   date_capture DATE NOT NULL,
   ndvi_avg DOUBLE PRECISION NOT NULL,
   valid_pixels INTEGER NOT NULL,

   CONSTRAINT chk_ndvi_avg CHECK(ndvi_avg BETWEEN -1 AND 1),
   PRIMARY KEY(ndvi_id),
   FOREIGN KEY(segment_id) REFERENCES segments(segment_id)
);

CREATE INDEX idx_segment
ON ndvi_observations(segment_id,date_capture);


CREATE TABLE viability(
   viability_id INTEGER GENERATED ALWAYS AS IDENTITY,
   segment_id VARCHAR(100) NOT NULL,
   grass_ratio DOUBLE PRECISION,
   confidence VARCHAR(15) NOT NULL,
   calculus_date TIMESTAMP DEFAULT NOW() NOT NULL,

   CONSTRAINT chk_confidence CHECK(confidence IN ('medium','low','high')),
   PRIMARY KEY(viability_id),
   FOREIGN KEY(segment_id) REFERENCES segments(segment_id)
);
