-- migrate:up
-- 评分排除名单（全局）：命中即淘汰，不参与加分与模型评分。
-- 空数组表示不排除任何文章。ON CONFLICT：设置页先于迁移写入过该 section
-- 时直接跳过，保留用户已编辑的名单。
INSERT INTO public.app_settings (section, value, version)
VALUES ('score_exclusion_keywords', '[]'::jsonb, 1)
ON CONFLICT (section) DO NOTHING;

-- migrate:down
DELETE FROM public.app_settings WHERE section = 'score_exclusion_keywords';
