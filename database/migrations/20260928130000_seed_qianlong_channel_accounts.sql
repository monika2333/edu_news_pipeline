-- migrate:up
-- 千龙网改为「账号=栏目」管理（requires_accounts）后，无账号会导致整源跳过。
-- 预插此前由 QIANLONG_BASE_URL 默认值覆盖的两个栏目（北京、教育），保证迁移后
-- 抓取范围不变。
INSERT INTO public.crawl_accounts (
    source, normalized_identifier, original_input, profile_url,
    display_name, enabled, display_name_synced_at
)
VALUES
    (
        'qianlong',
        'https://beijing.qianlong.com',
        'https://beijing.qianlong.com/',
        'https://beijing.qianlong.com',
        '千龙网-北京',
        true,
        now()
    ),
    (
        'qianlong',
        'https://edu.qianlong.com',
        'https://edu.qianlong.com/',
        'https://edu.qianlong.com',
        '千龙网-教育',
        true,
        now()
    )
ON CONFLICT (source, normalized_identifier) DO NOTHING;

-- migrate:down
DELETE FROM public.crawl_accounts
WHERE source = 'qianlong'
  AND normalized_identifier IN (
      'https://beijing.qianlong.com',
      'https://edu.qianlong.com'
  );
