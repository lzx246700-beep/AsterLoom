-- R16: source-level worker adapter evidence stored inside the SAME cumulative source database.
CREATE TABLE IF NOT EXISTS r16_parsed_asset (
  parsed_asset_id TEXT PRIMARY KEY,
  paper_key TEXT NOT NULL,
  original_sha TEXT NOT NULL,
  original_path TEXT NOT NULL,
  source_format TEXT NOT NULL,
  backend TEXT NOT NULL,
  ir_sha TEXT NOT NULL,
  ir_path TEXT NOT NULL,
  table_count INTEGER NOT NULL,
  cell_count INTEGER NOT NULL,
  method_count INTEGER NOT NULL,
  supplement_count INTEGER NOT NULL,
  science_state TEXT NOT NULL DEFAULT 'SOURCE_ONLY',
  training_approved INTEGER NOT NULL DEFAULT 0 CHECK(training_approved=0),
  UNIQUE (paper_key,original_sha,backend)
);
CREATE TABLE IF NOT EXISTS r16_source_table (
  table_key TEXT PRIMARY KEY,
  parsed_asset_id TEXT NOT NULL REFERENCES r16_parsed_asset(parsed_asset_id),
  paper_key TEXT NOT NULL,
  table_id TEXT NOT NULL,
  caption_original TEXT,
  footnotes_json TEXT NOT NULL,
  locator TEXT,
  UNIQUE(parsed_asset_id, table_id)
);
CREATE TABLE IF NOT EXISTS r16_source_cell (
  cell_key TEXT PRIMARY KEY,
  table_key TEXT NOT NULL REFERENCES r16_source_table(table_key),
  parsed_asset_id TEXT NOT NULL REFERENCES r16_parsed_asset(parsed_asset_id),
  paper_key TEXT NOT NULL,
  cell_row INTEGER NOT NULL,
  cell_column INTEGER NOT NULL,
  cell_role TEXT NOT NULL,
  original_literal TEXT NOT NULL,
  original_locator TEXT NOT NULL,
  page_number INTEGER,
  bbox_json TEXT,
  author_sha TEXT NOT NULL,
  metadata_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_r16_source_cell_paper ON r16_source_cell(paper_key, table_key);
CREATE INDEX IF NOT EXISTS idx_r16_source_cell_sha ON r16_source_cell(author_sha);
CREATE TABLE IF NOT EXISTS r16_source_method (
  method_key TEXT PRIMARY KEY,
  parsed_asset_id TEXT NOT NULL REFERENCES r16_parsed_asset(parsed_asset_id),
  paper_key TEXT NOT NULL,
  heading_original TEXT,
  text_original TEXT NOT NULL,
  original_locator TEXT NOT NULL,
  page_number INTEGER
);
CREATE TABLE IF NOT EXISTS r16_source_supplement (
  supplement_key TEXT PRIMARY KEY,
  parsed_asset_id TEXT NOT NULL REFERENCES r16_parsed_asset(parsed_asset_id),
  paper_key TEXT NOT NULL,
  href_literal TEXT,
  original_locator TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'ACCESS_HOLD'
);
