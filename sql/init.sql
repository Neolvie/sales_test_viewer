-- SalesTester schema: themes, prompts, sessions, prompt_tests

CREATE TABLE IF NOT EXISTS themes (
    id SERIAL PRIMARY KEY,
    name VARCHAR(500) NOT NULL,
    reference_text TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS prompts (
    id SERIAL PRIMARY KEY,
    version INTEGER NOT NULL,
    content TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT false,
    is_draft BOOLEAN NOT NULL DEFAULT false,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Only one prompt can be active at a time
CREATE UNIQUE INDEX IF NOT EXISTS idx_only_one_active_prompt ON prompts((1)) WHERE is_active = true;

CREATE TABLE IF NOT EXISTS sessions (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    answered_at TIMESTAMPTZ,
    state SMALLINT NOT NULL DEFAULT 0,
    theme_id INTEGER NOT NULL REFERENCES themes(id),
    full_name VARCHAR(500) NOT NULL,
    user_answer TEXT,
    result TEXT,
    prompt_id INTEGER REFERENCES prompts(id),
    score SMALLINT,
    CONSTRAINT valid_state CHECK (state IN (0, 1, 2))
);

CREATE TABLE IF NOT EXISTS prompt_tests (
    id SERIAL PRIMARY KEY,
    prompt_id INTEGER NOT NULL REFERENCES prompts(id),
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    result TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sessions_theme_id ON sessions(theme_id);
CREATE INDEX IF NOT EXISTS idx_sessions_state ON sessions(state);
CREATE INDEX IF NOT EXISTS idx_sessions_created_at ON sessions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_full_name ON sessions(full_name);
CREATE INDEX IF NOT EXISTS idx_sessions_score ON sessions(score);
CREATE INDEX IF NOT EXISTS idx_themes_is_active ON themes(is_active);
CREATE INDEX IF NOT EXISTS idx_prompts_is_active ON prompts(is_active);

COMMENT ON TABLE themes IS 'Ценностные предложения (темы для зачета)';
COMMENT ON TABLE prompts IS 'Версионируемые промты для проверки знаний';
COMMENT ON TABLE sessions IS 'Сессии тестирования продавцов';
COMMENT ON TABLE prompt_tests IS 'Результаты повторной проверки ответов draft-промтом';
