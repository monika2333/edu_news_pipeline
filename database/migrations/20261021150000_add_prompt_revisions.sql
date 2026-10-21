-- migrate:up
-- 提示词版本表：只追加、不修改历史。当前版本 = 该 prompt_key 的最大 revision。
-- content 只保存 <prompt> 标签内的有效模板；控制台保存时由服务端做同样的提取校验。
-- 文件（config/prompts）只在表内没有该 prompt_key 的记录时作为回退来源。
CREATE TABLE public.prompt_revisions (
    id bigserial PRIMARY KEY,
    prompt_key text NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    content text NOT NULL CHECK (btrim(content) <> ''),
    note text,
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by_user_id uuid REFERENCES public.console_users(id) ON DELETE RESTRICT,
    CONSTRAINT prompt_revisions_key_revision_unique UNIQUE (prompt_key, revision)
);

-- migrate:down
DROP TABLE public.prompt_revisions;
