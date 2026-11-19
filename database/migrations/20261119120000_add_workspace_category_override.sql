-- migrate:up
-- 审阅页换组只在本工作区生效（2026-11-19）。
--
-- 管理员和值班编辑在审阅页把一条新闻拖进另一个分组（京内/京外 × 正负面）时，
-- 过去直接改写全局 news_summaries.is_beijing_related / sentiment_label，导致一个人
-- 换组影响所有人的候选池、审阅列表、筛选与聚类。现在改为把人的判断记在工作区自己
-- 的行上：manual_reviews 按 owner_user_id 分工作区，shift_reviews 按 shift_id 分
-- 工作区，各加一个可空覆盖列。
--
-- 取值与前端/路由已有的分组键 ReviewGroupKey 同一套：
--   internal_positive / internal_negative / external_positive / external_negative
-- NULL = 跟随全局分类（news_summaries 的两个字段）。只有一个文本列而不是
-- 「京内外 + 正负面」两列，是为了避免「一个有值一个为空」的半状态。
--
-- 全局字段从此只由流水线写（geo-classify 写 is_beijing_related、enrich-summary
-- 写 sentiment_label）。历史值可能混着过去人工换组的结果，不做回填或还原。

alter table public.manual_reviews
    add column if not exists category_override text
        constraint manual_reviews_category_override_check
        check (
            category_override is null
            or category_override in (
                'internal_positive',
                'internal_negative',
                'external_positive',
                'external_negative'
            )
        );

comment on column public.manual_reviews.category_override is
    '本管理员工作区的分类覆盖值（internal/external × positive/negative 分组键）；NULL 表示跟随 news_summaries 的全局分类';

alter table public.shift_reviews
    add column if not exists category_override text
        constraint shift_reviews_category_override_check
        check (
            category_override is null
            or category_override in (
                'internal_positive',
                'internal_negative',
                'external_positive',
                'external_negative'
            )
        );

comment on column public.shift_reviews.category_override is
    '本班次工作区的分类覆盖值（internal/external × positive/negative 分组键）；NULL 表示跟随 news_summaries 的全局分类';

-- migrate:down
alter table public.manual_reviews
    drop constraint if exists manual_reviews_category_override_check;

alter table public.manual_reviews
    drop column if exists category_override;

alter table public.shift_reviews
    drop constraint if exists shift_reviews_category_override_check;

alter table public.shift_reviews
    drop column if exists category_override;
