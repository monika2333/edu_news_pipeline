-- migrate:up
-- TXT 简报导出（export）功能已由人工筛选控制台与报送存档完整接管，
-- 该功能的批次/条目存储随之移除。
-- init 迁移创建的 latest_brief_items 视图依赖 brief_items；当前线上库
-- 不存在该视图，if exists 保证从零重放迁移的库也能按顺序删除。
drop view if exists public.latest_brief_items;
drop table if exists public.brief_items;
drop table if exists public.brief_batches;

-- migrate:down
-- 结构以删除前的 database/schema.sql 为准（列、默认值、主键、唯一约束、
-- 外键 on delete cascade、三个索引与两个 set_updated_at 触发器）。
create table public.brief_batches (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    report_date date NOT NULL,
    sequence_no integer DEFAULT 1 NOT NULL,
    generated_at timestamp with time zone DEFAULT now() NOT NULL,
    generated_by text,
    export_payload jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT brief_batches_pkey PRIMARY KEY (id),
    CONSTRAINT brief_batches_report_date_sequence_no_key UNIQUE (report_date, sequence_no)
);

create table public.brief_items (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    brief_batch_id uuid NOT NULL,
    article_id text,
    section text,
    order_index integer DEFAULT 0 NOT NULL,
    final_summary text,
    approved_by text,
    approved_at timestamp with time zone,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT brief_items_pkey PRIMARY KEY (id),
    CONSTRAINT brief_items_brief_batch_id_fkey FOREIGN KEY (brief_batch_id)
        REFERENCES public.brief_batches(id) ON DELETE CASCADE
);

create index brief_items_article_created_at_idx
    on public.brief_items (article_id, created_at desc);
create index brief_items_batch_idx
    on public.brief_items (brief_batch_id);
create index brief_items_section_idx
    on public.brief_items (section);

create trigger brief_batches_set_updated_at
    before update on public.brief_batches
    for each row execute function public.set_updated_at();
create trigger brief_items_set_updated_at
    before update on public.brief_items
    for each row execute function public.set_updated_at();
