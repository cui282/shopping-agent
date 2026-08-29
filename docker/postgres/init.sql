CREATE TABLE IF NOT EXISTS shopping_agent_preferences (
    user_id VARCHAR(120) PRIMARY KEY,
    preferences JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS shopping_agent_preferences_expires_at_idx
    ON shopping_agent_preferences (expires_at);
