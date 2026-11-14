-- migrate:up
-- 电子报启用列表：由服务器上唯一的电子报计划任务读取，决定每轮抓哪些电子报。
-- 种子值是迁移前各自单独跑的三个来源，保证上线后行为不中断。
-- ON CONFLICT：设置页先于迁移写入过该 section 时直接跳过，保留用户已编辑的启用列表。
INSERT INTO public.app_settings (section, value, version)
VALUES (
    'epaper_sources',
    '["bjrb","ldwb","zgjsb"]'::jsonb,
    1
)
ON CONFLICT (section) DO NOTHING;

-- migrate:down
DELETE FROM public.app_settings WHERE section = 'epaper_sources';
