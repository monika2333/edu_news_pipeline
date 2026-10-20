-- migrate:up
-- 21经济网按「账号=栏目」接入：预插当前唯一要监控的「宏观」栏目，
-- 保证迁移后无需在控制台手动添加；来源开关仍由控制台控制
-- （crawl_sources 不含 21jingji，默认关闭）。
INSERT INTO public.crawl_accounts (
    source, normalized_identifier, original_input, profile_url,
    display_name, enabled, display_name_synced_at
)
VALUES (
    '21jingji',
    'https://www.21jingji.com/channel/politics/',
    'https://www.21jingji.com/channel/politics/',
    'https://www.21jingji.com/channel/politics/',
    '宏观',
    true,
    now()
)
ON CONFLICT (source, normalized_identifier) DO NOTHING;

-- migrate:down
DELETE FROM public.crawl_accounts
WHERE source = '21jingji'
  AND normalized_identifier = 'https://www.21jingji.com/channel/politics/';
