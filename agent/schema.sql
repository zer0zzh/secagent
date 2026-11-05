-- 阶段1 数据模型 · 与 docs/phase1/02-design.md 一一对应
-- 改字段前先改设计文档，不要直接改这里。

CREATE TABLE IF NOT EXISTS runs (
  id            INTEGER PRIMARY KEY,
  run_key       TEXT UNIQUE NOT NULL,
  started_at    TEXT NOT NULL,
  finished_at   TEXT,
  task          TEXT NOT NULL,
  profile       TEXT NOT NULL,
  model         TEXT NOT NULL,
  status        TEXT NOT NULL,
  steps         INTEGER DEFAULT 0,
  tool_calls    INTEGER DEFAULT 0,
  final_answer  TEXT,
  trace_path    TEXT,
  total_tokens  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS steps (
  id            INTEGER PRIMARY KEY,
  run_id        INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  idx           INTEGER NOT NULL,
  role          TEXT DEFAULT 'agent',
  thought       TEXT,
  action        TEXT,
  args_json     TEXT,
  raw_text      TEXT,
  latency_ms    INTEGER,
  prompt_tokens INTEGER,
  completion_tokens INTEGER,
  finish_reason TEXT,
  UNIQUE(run_id, idx)
);

CREATE TABLE IF NOT EXISTS tool_calls (
  id            INTEGER PRIMARY KEY,
  run_id        INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  step_id       INTEGER REFERENCES steps(id),
  tool          TEXT NOT NULL,
  args_json     TEXT NOT NULL,
  ok            INTEGER NOT NULL,
  duration_ms   INTEGER,
  result_json   TEXT,
  error         TEXT,
  truncated     INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS evidence (
  id            INTEGER PRIMARY KEY,
  run_id        INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  step_id       INTEGER REFERENCES steps(id),
  tool_call_id  INTEGER REFERENCES tool_calls(id),
  kind          TEXT NOT NULL,
  claim         TEXT NOT NULL,
  subject       TEXT,
  payload_json  TEXT,
  observed_json TEXT,
  repeat_count  INTEGER DEFAULT 1,
  created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS verifications (
  id            INTEGER PRIMARY KEY,
  run_id        INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  claim         TEXT NOT NULL,
  verifier      TEXT NOT NULL,
  passed        INTEGER NOT NULL,
  detail        TEXT,
  evidence_ids  TEXT,
  created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS verdicts (
  id                 INTEGER PRIMARY KEY,
  run_id             INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  claim              TEXT NOT NULL,
  verdict            TEXT NOT NULL,
  confidence         REAL,
  evidence_ids       TEXT NOT NULL CHECK (json_array_length(evidence_ids) > 0),
  model_answer       TEXT,
  human_review       INTEGER DEFAULT 0,
  created_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS eval_cases (
  id            INTEGER PRIMARY KEY,
  name          TEXT UNIQUE NOT NULL,
  target        TEXT NOT NULL,
  category      TEXT NOT NULL,
  is_negative   INTEGER NOT NULL,
  expected      TEXT NOT NULL,
  ground_truth  TEXT,
  notes         TEXT
);

CREATE TABLE IF NOT EXISTS eval_results (
  id            INTEGER PRIMARY KEY,
  run_id        INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  case_id       INTEGER NOT NULL REFERENCES eval_cases(id),
  predicted     TEXT NOT NULL,
  correct       INTEGER NOT NULL,
  false_positive INTEGER DEFAULT 0,
  steps         INTEGER,
  latency_ms    INTEGER,
  tokens        INTEGER
);

CREATE INDEX IF NOT EXISTS idx_steps_run ON steps(run_id, idx);
CREATE INDEX IF NOT EXISTS idx_tc_run ON tool_calls(run_id);
CREATE INDEX IF NOT EXISTS idx_ev_run ON evidence(run_id);
CREATE INDEX IF NOT EXISTS idx_er_run ON eval_results(run_id);
