BEGIN;

ALTER TABLE analysis_snapshots RENAME COLUMN trend TO ma_regime;

UPDATE analysis_snapshots
SET ma_regime = CASE
    WHEN ma_regime IN ('偏多', 'bullish') THEN 'bullish'
    WHEN ma_regime IN ('偏空', 'bearish') THEN 'bearish'
    ELSE 'mixed'
END;

UPDATE analysis_snapshots
SET payload_json = jsonb_strip_nulls(jsonb_build_object(
    'schema_version', 'analysis_snapshot.v2',
    'symbol', symbol,
    'interval', interval,
    'timestamp', snapshot_time,
    'current_price', current_price,
    'ma_regime', ma_regime,
    'support', support_json,
    'resistance', resistance_json
));

ALTER TABLE analysis_snapshots DROP COLUMN stance;

ALTER TABLE analysis_snapshots
ADD CONSTRAINT ck_analysis_snapshots_ma_regime
CHECK (ma_regime IN ('bullish', 'bearish', 'mixed'));

COMMIT;
