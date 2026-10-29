-- migrate:up
-- 给已存在的 llm_models 分区补上「提示词优化」步骤；只补缺失，不覆盖
-- 其他步骤的值，也不覆盖管理员已经手工配置过的 prompt_optimizer。
UPDATE public.app_settings
SET value = jsonb_set(
        value,
        '{steps,prompt_optimizer}',
        '{"model": null, "reasoning": true, "endpoint": null}'::jsonb,
        true
    ),
    updated_at = now()
WHERE section = 'llm_models'
  AND NOT (COALESCE(value -> 'steps', '{}'::jsonb) ? 'prompt_optimizer');

-- migrate:down
UPDATE public.app_settings
SET value = value #- '{steps,prompt_optimizer}',
    updated_at = now()
WHERE section = 'llm_models'
  AND value -> 'steps' ? 'prompt_optimizer';
