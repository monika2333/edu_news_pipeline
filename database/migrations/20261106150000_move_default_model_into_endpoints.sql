-- migrate:up
-- 默认模型跟随接入点：llm_endpoints 的每个接入点补 default_model，
-- llm_models 删除顶层 default 并按新结构改写步骤。
-- 硬约束：迁移前后每一步解析出的「接入点 + 模型 + reasoning」必须完全一致。
--   - 默认接入点的 default_model 取旧 llm_models.default；
--   - 其他接入点取步骤目录顺序（与 src.business_config.LLM_STEPS 一致）中
--     第一个显式引用它的步骤所用的模型（jsonb 对象不保留键序，顺序在此硬编码）；
--   - 没有任何步骤引用的接入点推断不出默认模型：直接报错中止（报错含接入点
--     key），由管理员先在控制台处理后再跑迁移，不用猜测值填充；
--   - llm_models 分区不存在（全新部署，import-settings 稍后写入）时，
--     default_model 取与 settings_service._legacy_models 同一份兜底值；
--   - 「有模型、无接入点」的步骤固定到当时的默认接入点（旧语义本就落到
--     默认接入点）；模型恰等于该接入点新 default_model 的步骤把 model 置
--     null，让它以后跟随接入点默认模型（解析结果不变）；
--   - 两个分区 version 各加 1：已打开的设置页再提交旧格式草稿会触发乐观锁
--     冲突，而不是用旧结构覆盖。
DO $$
DECLARE
    step_order text[] := ARRAY[
        'summary',
        'source',
        'sentiment',
        'scoring',
        'external_filter',
        'beijing_gate',
        'duplicate_review',
        'prompt_optimizer'
    ];
    fallback_model text := 'deepseek/deepseek-v4-flash';
    endpoints jsonb;
    models jsonb;
    default_endpoint text;
    legacy_default text;
    new_items jsonb;
    new_steps jsonb;
    item jsonb;
    endpoint_key text;
    endpoint_default_model text;
    step_key text;
    step jsonb;
BEGIN
    SELECT value INTO endpoints FROM public.app_settings
    WHERE section = 'llm_endpoints'
    FOR UPDATE;
    IF endpoints IS NULL THEN
        RAISE EXCEPTION 'app_settings 缺少 llm_endpoints 分区，迁移无法为接入点补 default_model';
    END IF;
    SELECT value INTO models FROM public.app_settings
    WHERE section = 'llm_models'
    FOR UPDATE;

    default_endpoint := endpoints ->> 'default';
    legacy_default := COALESCE(models ->> 'default', fallback_model);

    -- 1) 为每个接入点推断 default_model，重建 items
    new_items := '[]'::jsonb;
    FOR item IN SELECT * FROM jsonb_array_elements(endpoints -> 'items')
    LOOP
        endpoint_key := item ->> 'key';
        IF endpoint_key = default_endpoint THEN
            endpoint_default_model := legacy_default;
        ELSE
            endpoint_default_model := NULL;
            FOREACH step_key IN ARRAY step_order
            LOOP
                step := models -> 'steps' -> step_key;
                IF step IS NOT NULL AND step ->> 'endpoint' = endpoint_key THEN
                    endpoint_default_model := COALESCE(step ->> 'model', legacy_default);
                    EXIT;
                END IF;
            END LOOP;
            IF endpoint_default_model IS NULL THEN
                RAISE EXCEPTION '接入点 % 没有任何步骤引用，无法推断默认模型；'
                    '请先在控制台把某个步骤指定到该接入点，或删除该接入点后再运行迁移',
                    endpoint_key;
            END IF;
        END IF;
        new_items := new_items || jsonb_build_object(
            'key', item -> 'key',
            'label', item -> 'label',
            'base_url', item -> 'base_url',
            'api_key_env', item -> 'api_key_env',
            'api_style', item -> 'api_style',
            'temperature_override', item -> 'temperature_override',
            'default_model', endpoint_default_model
        );
    END LOOP;

    UPDATE public.app_settings
    SET value = jsonb_build_object('default', default_endpoint, 'items', new_items),
        version = version + 1,
        updated_at = now()
    WHERE section = 'llm_endpoints';

    -- 2) 改写 llm_models（分区不存在 = 全新部署，import-settings 稍后写入）
    IF models IS NOT NULL THEN
        new_steps := '{}'::jsonb;
        FOR step_key IN SELECT jsonb_object_keys(models -> 'steps')
        LOOP
            step := models -> 'steps' -> step_key;
            -- 归一化：endpoint 字段缺失的历史行按 null 处理，补齐三个键
            step := jsonb_build_object(
                'model', step -> 'model',
                'reasoning', step -> 'reasoning',
                'endpoint', step -> 'endpoint'
            );
            IF step ->> 'model' IS NOT NULL AND step ->> 'endpoint' IS NULL THEN
                step := jsonb_set(step, '{endpoint}', to_jsonb(default_endpoint));
            END IF;
            IF step ->> 'endpoint' IS NOT NULL THEN
                FOR item IN SELECT * FROM jsonb_array_elements(new_items)
                LOOP
                    IF item ->> 'key' = step ->> 'endpoint'
                       AND step ->> 'model' = item ->> 'default_model' THEN
                        step := jsonb_set(step, '{model}', 'null'::jsonb);
                        EXIT;
                    END IF;
                END LOOP;
            END IF;
            new_steps := new_steps || jsonb_build_object(step_key, step);
        END LOOP;

        UPDATE public.app_settings
        SET value = jsonb_build_object('steps', new_steps),
            version = version + 1,
            updated_at = now()
        WHERE section = 'llm_models';
    END IF;
END $$;

-- migrate:down
-- 还原旧结构并保持解析结果不变：
--   - llm_models.default 取默认接入点的 default_model；
--   - 「指定了接入点但模型为空」的步骤补回该接入点的 default_model；
--   - 去掉各接入点的 default_model；
--   - 两个分区 version 各减 1（up 加 1 的逆操作）。
DO $$
DECLARE
    endpoints jsonb;
    models jsonb;
    default_endpoint text;
    default_model text;
    new_items jsonb;
    new_steps jsonb;
    item jsonb;
    step_key text;
    step jsonb;
    endpoint_default_model text;
BEGIN
    SELECT value INTO endpoints FROM public.app_settings
    WHERE section = 'llm_endpoints'
    FOR UPDATE;
    IF endpoints IS NULL THEN
        RAISE EXCEPTION 'app_settings 缺少 llm_endpoints 分区，down 缺少还原依据';
    END IF;
    SELECT value INTO models FROM public.app_settings
    WHERE section = 'llm_models'
    FOR UPDATE;

    default_endpoint := endpoints ->> 'default';

    -- 接入点去掉 default_model
    new_items := '[]'::jsonb;
    FOR item IN SELECT * FROM jsonb_array_elements(endpoints -> 'items')
    LOOP
        IF item ->> 'key' = default_endpoint THEN
            default_model := item ->> 'default_model';
        END IF;
        new_items := new_items || (item - 'default_model');
    END LOOP;

    UPDATE public.app_settings
    SET value = jsonb_build_object('default', default_endpoint, 'items', new_items),
        version = version - 1,
        updated_at = now()
    WHERE section = 'llm_endpoints';

    IF models IS NOT NULL THEN
        new_steps := '{}'::jsonb;
        FOR step_key IN SELECT jsonb_object_keys(models -> 'steps')
        LOOP
            step := models -> 'steps' -> step_key;
            IF step ->> 'endpoint' IS NOT NULL AND step ->> 'model' IS NULL THEN
                endpoint_default_model := NULL;
                FOR item IN SELECT * FROM jsonb_array_elements(endpoints -> 'items')
                LOOP
                    IF item ->> 'key' = step ->> 'endpoint' THEN
                        endpoint_default_model := item ->> 'default_model';
                        EXIT;
                    END IF;
                END LOOP;
                IF endpoint_default_model IS NOT NULL THEN
                    step := jsonb_set(step, '{model}', to_jsonb(endpoint_default_model));
                END IF;
            END IF;
            new_steps := new_steps || jsonb_build_object(step_key, step);
        END LOOP;

        UPDATE public.app_settings
        SET value = jsonb_build_object('default', default_model, 'steps', new_steps),
            version = version - 1,
            updated_at = now()
        WHERE section = 'llm_models';
    END IF;
END $$;
