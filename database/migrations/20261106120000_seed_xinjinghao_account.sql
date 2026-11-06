-- migrate:up
-- 新京号按「账号=号主页」接入：预插当前唯一要监控的「北京援藏」，
-- 保证迁移后无需在控制台手动添加；来源开关仍由设置页控制。
INSERT INTO public.crawl_accounts (
    source, normalized_identifier, original_input, profile_url,
    display_name, enabled, display_name_synced_at
)
VALUES (
    'xinjinghao',
    '296',
    'https://m.bjnews.com.cn/mp/296',
    'https://m.bjnews.com.cn/mp/296',
    '北京援藏',
    true,
    now()
)
ON CONFLICT (source, normalized_identifier) DO NOTHING;

-- migrate:down
DELETE FROM public.crawl_accounts
WHERE source = 'xinjinghao'
  AND normalized_identifier = '296';
