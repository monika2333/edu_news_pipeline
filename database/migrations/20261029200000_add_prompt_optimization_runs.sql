-- migrate:up
-- 夜间提示词自动优化的运行记录（sidecar，纯加法）。
-- 每个提示词每晚最多一行；无论是否生成新版本都记录，供控制台展示与两周后的
-- 阈值校准。new_revision_id 唯一：一个自动版本只能由一次成功运行产生。
CREATE TABLE public.prompt_optimization_runs (
    id bigserial PRIMARY KEY,
    prompt_key text NOT NULL,
    outcome text NOT NULL,
    started_at timestamptz NOT NULL,
    finished_at timestamptz NOT NULL DEFAULT now(),
    window_start timestamptz NOT NULL,
    baseline_revision_id bigint REFERENCES public.prompt_revisions(id) ON DELETE RESTRICT,
    new_revision_id bigint REFERENCES public.prompt_revisions(id) ON DELETE RESTRICT,
    model text NOT NULL,
    cases jsonb NOT NULL DEFAULT '[]'::jsonb,
    attempts jsonb NOT NULL DEFAULT '[]'::jsonb,
    error text,
    CONSTRAINT prompt_optimization_runs_prompt_key_check CHECK (
        prompt_key = ANY (ARRAY[
            'external_positive'::text,
            'external_negative'::text,
            'internal_positive'::text,
            'internal_negative'::text
        ])
    ),
    CONSTRAINT prompt_optimization_runs_outcome_check CHECK (
        outcome = ANY (ARRAY[
            'no_correction'::text,
            'model_declined'::text,
            'check_failed'::text,
            'revision_created'::text,
            'error'::text
        ])
    ),
    CONSTRAINT prompt_optimization_runs_new_revision_pair_check CHECK (
        (outcome = 'revision_created') = (new_revision_id IS NOT NULL)
    )
);

CREATE UNIQUE INDEX prompt_optimization_runs_new_revision_idx
    ON public.prompt_optimization_runs (new_revision_id)
    WHERE new_revision_id IS NOT NULL;

CREATE INDEX prompt_optimization_runs_key_finished_idx
    ON public.prompt_optimization_runs (prompt_key, finished_at DESC, id DESC);

-- migrate:down
DROP TABLE public.prompt_optimization_runs;
