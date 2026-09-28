-- migrate:up
-- ON CONFLICT：应用先于迁移写入过该 section 时直接跳过，保留现有端点配置。
INSERT INTO public.app_settings (section, value, version)
VALUES (
    'llm_endpoints',
    '{"default":"openrouter","items":[{"key":"openrouter","label":"OpenRouter","base_url":"https://openrouter.ai/api/v1","api_key_env":"LLM_API_KEY","api_style":"openrouter","temperature_override":null}]}'::jsonb,
    1
)
ON CONFLICT (section) DO NOTHING;

-- migrate:down
DELETE FROM public.app_settings WHERE section = 'llm_endpoints';
