-- migrate:up
-- 中国日报改为「账号=栏目」管理（requires_accounts）后，无账号会导致整源跳过。
-- 预插当前监控的两个栏目，保证迁移后抓取范围不变（专稿）或有扩大（地方资讯）。
INSERT INTO public.crawl_accounts (
    source, normalized_identifier, original_input, profile_url,
    display_name, enabled, display_name_synced_at
)
VALUES
    (
        'chinadaily',
        'https://cn.chinadaily.com.cn/5b753f9fa310030f813cf408/5bd54dd6a3101a87ca8ff5f8/5bd54e59a3101a87ca8ff606',
        'https://cn.chinadaily.com.cn/5b753f9fa310030f813cf408/5bd54dd6a3101a87ca8ff5f8/5bd54e59a3101a87ca8ff606',
        'https://cn.chinadaily.com.cn/5b753f9fa310030f813cf408/5bd54dd6a3101a87ca8ff5f8/5bd54e59a3101a87ca8ff606',
        '中国日报专稿',
        true,
        now()
    ),
    (
        'chinadaily',
        'https://cn.chinadaily.com.cn/6597728fa310af3247ffaeae',
        'https://cn.chinadaily.com.cn/6597728fa310af3247ffaeae',
        'https://cn.chinadaily.com.cn/6597728fa310af3247ffaeae',
        '地方资讯',
        true,
        now()
    )
ON CONFLICT (source, normalized_identifier) DO NOTHING;

-- migrate:down
DELETE FROM public.crawl_accounts
WHERE source = 'chinadaily'
  AND normalized_identifier IN (
      'https://cn.chinadaily.com.cn/5b753f9fa310030f813cf408/5bd54dd6a3101a87ca8ff5f8/5bd54e59a3101a87ca8ff606',
      'https://cn.chinadaily.com.cn/6597728fa310af3247ffaeae'
  );
