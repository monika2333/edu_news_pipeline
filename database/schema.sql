\restrict X7rgTp0FCMQhEMmikZA1KKtk1iPM8AzJwoqXiHxRouckR6b9KmjXTFgBy1CiDi0

-- Dumped from database version 18.0
-- Dumped by pg_dump version 18.0

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: pg_trgm; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;


--
-- Name: EXTENSION pg_trgm; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pg_trgm IS 'text similarity measurement and index searching based on trigrams';


--
-- Name: pgcrypto; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;


--
-- Name: EXTENSION pgcrypto; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pgcrypto IS 'cryptographic functions';


--
-- Name: set_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.set_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
begin
    new.updated_at = now();
    return new;
end;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: brief_batches; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.brief_batches (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    report_date date NOT NULL,
    sequence_no integer DEFAULT 1 NOT NULL,
    generated_at timestamp with time zone DEFAULT now() NOT NULL,
    generated_by text,
    export_payload jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: brief_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.brief_items (
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
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: console_user_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.console_user_sessions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    token_hash text NOT NULL,
    csrf_token_hash text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    last_seen_at timestamp with time zone,
    revoked_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: console_users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.console_users (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    username text NOT NULL,
    display_name text NOT NULL,
    password_hash text NOT NULL,
    role text NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    password_changed_at timestamp with time zone,
    last_login_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    preferred_weekday smallint,
    deleted_at timestamp with time zone,
    CONSTRAINT console_users_display_name_not_blank CHECK ((btrim(display_name) <> ''::text)),
    CONSTRAINT console_users_preferred_weekday_check CHECK (((preferred_weekday IS NULL) OR ((preferred_weekday >= 0) AND (preferred_weekday <= 6)))),
    CONSTRAINT console_users_role_check CHECK ((role = ANY (ARRAY['admin'::text, 'duty_editor'::text]))),
    CONSTRAINT console_users_username_not_blank CHECK ((btrim(username) <> ''::text))
);


--
-- Name: duty_schedules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.duty_schedules (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    weekday smallint NOT NULL,
    user_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT duty_schedules_weekday_check CHECK (((weekday >= 0) AND (weekday <= 6)))
);


--
-- Name: duty_shifts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.duty_shifts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    starts_at timestamp with time zone NOT NULL,
    ends_at timestamp with time zone NOT NULL,
    cancelled_at timestamp with time zone,
    notes text,
    created_by_user_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT duty_shifts_range_check CHECK ((ends_at > starts_at))
);


--
-- Name: filtered_articles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.filtered_articles (
    article_id text NOT NULL,
    keywords text[] DEFAULT '{}'::text[] NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    title text,
    source text,
    publish_time bigint,
    publish_time_iso timestamp with time zone,
    url text,
    content_markdown text,
    content_hash text,
    simhash text,
    primary_article_id text,
    inserted_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    simhash_bigint bigint,
    simhash_band1 integer,
    simhash_band2 integer,
    simhash_band3 integer,
    simhash_band4 integer
);


--
-- Name: manual_clusters; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.manual_clusters (
    bucket_key text NOT NULL,
    cluster_id text NOT NULL,
    item_ids text[] NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT manual_clusters_bucket_key_check CHECK ((bucket_key = ANY (ARRAY['internal_positive'::text, 'internal_negative'::text, 'external_positive'::text, 'external_negative'::text])))
);


--
-- Name: manual_reviews; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.manual_reviews (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    article_id text NOT NULL,
    status text NOT NULL,
    summary text,
    rank double precision,
    notes text,
    score numeric(6,3),
    decided_by text,
    decided_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    manual_llm_source text,
    report_type text,
    decided_by_user_id uuid,
    version integer DEFAULT 1 NOT NULL,
    CONSTRAINT manual_reviews_report_type_check CHECK ((report_type = ANY (ARRAY['zongbao'::text, 'wanbao'::text]))),
    CONSTRAINT manual_reviews_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'selected'::text, 'backup'::text, 'discarded'::text, 'exported'::text]))),
    CONSTRAINT manual_reviews_version_check CHECK ((version > 0))
);


--
-- Name: news_summaries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.news_summaries (
    article_id text NOT NULL,
    title text,
    source text,
    publish_time bigint,
    publish_time_iso timestamp with time zone,
    url text,
    content_markdown text,
    llm_summary text,
    summary_generated_at timestamp with time zone DEFAULT now() NOT NULL,
    fetched_at timestamp with time zone,
    llm_keywords text[] DEFAULT '{}'::text[],
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    llm_source text,
    summary_status text DEFAULT 'pending'::text NOT NULL,
    summary_attempted_at timestamp with time zone,
    summary_fail_count integer DEFAULT 0 NOT NULL,
    is_beijing_related boolean,
    score numeric(6,3),
    status text DEFAULT 'pending'::text NOT NULL,
    sentiment_label text,
    sentiment_confidence double precision,
    raw_relevance_score numeric(6,3),
    keyword_bonus_score numeric(6,3),
    score_details jsonb DEFAULT '{}'::jsonb NOT NULL,
    external_importance_status text DEFAULT 'pending'::text NOT NULL,
    external_importance_score numeric(6,3),
    external_importance_checked_at timestamp with time zone,
    external_importance_raw jsonb,
    external_filter_attempted_at timestamp with time zone,
    external_filter_fail_count integer DEFAULT 0 NOT NULL,
    is_beijing_related_llm boolean,
    beijing_gate_checked_at timestamp with time zone,
    beijing_gate_raw jsonb,
    beijing_gate_attempted_at timestamp with time zone,
    beijing_gate_fail_count integer DEFAULT 0 NOT NULL,
    dedup_embedding bytea,
    dedup_embedding_model text,
    dedup_source_hash text,
    dedup_embedded_at timestamp with time zone
);


--
-- Name: COLUMN news_summaries.llm_source; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.news_summaries.llm_source IS 'LLM-detected source for the article';


--
-- Name: COLUMN news_summaries.is_beijing_related; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.news_summaries.is_beijing_related IS 'True when the article is related to Beijing; NULL when not evaluated';


--
-- Name: COLUMN news_summaries.is_beijing_related_llm; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.news_summaries.is_beijing_related_llm IS 'LLM-based Beijing relevance decision; NULL when not evaluated';


--
-- Name: COLUMN news_summaries.beijing_gate_checked_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.news_summaries.beijing_gate_checked_at IS 'Timestamp when the LLM Beijing gate returned a definitive result';


--
-- Name: COLUMN news_summaries.dedup_embedding; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.news_summaries.dedup_embedding IS 'Normalized title-plus-summary embedding used by submission-dedup.';


--
-- Name: COLUMN news_summaries.dedup_embedding_model; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.news_summaries.dedup_embedding_model IS 'Embedding model used for dedup_embedding; must match the submission-dedup model constant.';


--
-- Name: COLUMN news_summaries.dedup_source_hash; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.news_summaries.dedup_source_hash IS 'SHA-256 of the exact title-plus-truncated-summary text encoded for submission-dedup.';


--
-- Name: COLUMN news_summaries.dedup_embedded_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.news_summaries.dedup_embedded_at IS 'Timestamp when submission-dedup last refreshed the cached news embedding.';


--
-- Name: news_title_embeddings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.news_title_embeddings (
    article_id text NOT NULL,
    embedding bytea NOT NULL,
    model text NOT NULL,
    title_hash text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: pipeline_run_steps; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pipeline_run_steps (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    run_id text NOT NULL,
    order_index integer NOT NULL,
    step_name text NOT NULL,
    status text NOT NULL,
    started_at timestamp with time zone NOT NULL,
    finished_at timestamp with time zone NOT NULL,
    duration_seconds numeric(12,3),
    error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: pipeline_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pipeline_runs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    run_id text NOT NULL,
    status text NOT NULL,
    trigger_source text,
    plan jsonb DEFAULT '[]'::jsonb NOT NULL,
    started_at timestamp with time zone NOT NULL,
    finished_at timestamp with time zone,
    steps_completed integer DEFAULT 0 NOT NULL,
    artifacts jsonb,
    error_summary text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: primary_articles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.primary_articles (
    article_id text NOT NULL,
    primary_article_id text NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    score numeric(6,3),
    score_updated_at timestamp with time zone,
    title text,
    source text,
    publish_time bigint,
    publish_time_iso timestamp with time zone,
    url text,
    content_markdown text,
    keywords text[] DEFAULT '{}'::text[] NOT NULL,
    content_hash text,
    simhash text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    raw_relevance_score numeric(6,3),
    keyword_bonus_score numeric(6,3),
    score_details jsonb DEFAULT '{}'::jsonb NOT NULL
);


--
-- Name: raw_articles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.raw_articles (
    token text,
    profile_url text,
    article_id text NOT NULL,
    title text,
    source text,
    publish_time bigint,
    publish_time_iso timestamp with time zone,
    url text,
    summary text,
    comment_count integer,
    digg_count integer,
    content_markdown text,
    detail_fetched_at timestamp with time zone,
    fetched_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: review_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.review_events (
    id bigint NOT NULL,
    actor_user_id uuid,
    action text NOT NULL,
    target_type text NOT NULL,
    target_id text,
    before_data jsonb,
    after_data jsonb,
    request_id text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: review_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.review_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: review_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.review_events_id_seq OWNED BY public.review_events.id;


--
-- Name: schema_migrations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schema_migrations (
    version character varying(128) NOT NULL
);


--
-- Name: score_feedbacks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.score_feedbacks (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    article_id text NOT NULL,
    feedback_type text NOT NULL,
    score_value numeric(6,3) NOT NULL,
    prompt_key text NOT NULL,
    prompt_version text NOT NULL,
    notes text,
    score_context jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    submitted_by text,
    submitted_by_user_id uuid,
    CONSTRAINT score_feedbacks_feedback_type_check CHECK ((feedback_type = ANY (ARRAY['too_high'::text, 'too_low'::text]))),
    CONSTRAINT score_feedbacks_notes_length_check CHECK (((notes IS NULL) OR (char_length(notes) <= 500))),
    CONSTRAINT score_feedbacks_prompt_key_check CHECK ((prompt_key = ANY (ARRAY['external_positive'::text, 'external_negative'::text, 'internal_positive'::text, 'internal_negative'::text]))),
    CONSTRAINT score_feedbacks_prompt_version_check CHECK ((btrim(prompt_version) <> ''::text))
);


--
-- Name: shift_review_finalization_batches; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.shift_review_finalization_batches (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    shift_id uuid NOT NULL,
    report_type text NOT NULL,
    finalized_by_user_id uuid NOT NULL,
    finalized_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT shift_review_finalization_batches_report_type_check CHECK ((report_type = ANY (ARRAY['zongbao'::text, 'wanbao'::text])))
);


--
-- Name: shift_reviews; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.shift_reviews (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    shift_id uuid NOT NULL,
    article_id text NOT NULL,
    created_by_user_id uuid NOT NULL,
    updated_by_user_id uuid NOT NULL,
    report_type text,
    decision text DEFAULT 'pending'::text NOT NULL,
    rank integer,
    excerpt_text text,
    edited_summary text,
    manual_llm_source text,
    notes text,
    version integer DEFAULT 1 NOT NULL,
    decided_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    admin_discarded_at timestamp with time zone,
    admin_discarded_by_user_id uuid,
    finalized_batch_id uuid,
    finalized_rank integer,
    CONSTRAINT shift_reviews_decision_check CHECK ((decision = ANY (ARRAY['pending'::text, 'selected'::text, 'backup'::text, 'discarded'::text]))),
    CONSTRAINT shift_reviews_finalization_pair_check CHECK ((((finalized_batch_id IS NULL) AND (finalized_rank IS NULL)) OR ((finalized_batch_id IS NOT NULL) AND (finalized_rank IS NOT NULL)))),
    CONSTRAINT shift_reviews_finalized_rank_check CHECK (((finalized_rank IS NULL) OR (finalized_rank > 0))),
    CONSTRAINT shift_reviews_rank_check CHECK (((rank IS NULL) OR (rank > 0))),
    CONSTRAINT shift_reviews_report_type_check CHECK (((report_type IS NULL) OR (report_type = ANY (ARRAY['zongbao'::text, 'wanbao'::text])))),
    CONSTRAINT shift_reviews_version_check CHECK ((version > 0))
);


--
-- Name: submission_duplicate_matches; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.submission_duplicate_matches (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    article_id text NOT NULL,
    item_id uuid NOT NULL,
    similarity numeric(5,4) NOT NULL,
    match_method text NOT NULL,
    state text DEFAULT 'suspected'::text NOT NULL,
    decided_by uuid,
    decided_at timestamp with time zone,
    detected_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT submission_duplicate_matches_method_check CHECK ((match_method = ANY (ARRAY['exact'::text, 'vector'::text, 'llm'::text, 'manual'::text]))),
    CONSTRAINT submission_duplicate_matches_state_check CHECK ((state = ANY (ARRAY['suspected'::text, 'confirmed'::text, 'dismissed'::text])))
);


--
-- Name: submitted_report_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.submitted_report_items (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    report_id uuid NOT NULL,
    section text,
    marker text,
    order_index integer DEFAULT 0 NOT NULL,
    title text NOT NULL,
    body text DEFAULT ''::text NOT NULL,
    source text,
    urls text[] DEFAULT '{}'::text[] NOT NULL,
    norm_title text NOT NULL,
    norm_title_hash text NOT NULL,
    embedding bytea,
    embedding_model text,
    embedded_at timestamp with time zone,
    article_id text,
    link_status text DEFAULT 'processing'::text NOT NULL,
    link_title_score numeric(5,4),
    link_body_score numeric(5,4),
    link_combined_score numeric(5,4),
    best_candidate_article_id text,
    link_matched_at timestamp with time zone,
    link_decided_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT submitted_report_items_link_status_check CHECK ((link_status = ANY (ARRAY['processing'::text, 'pending'::text, 'matched'::text, 'unmatched'::text, 'rejected'::text])))
);


--
-- Name: submitted_reports; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.submitted_reports (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    report_type text NOT NULL,
    report_date date NOT NULL,
    compiled_date date NOT NULL,
    issue_no text,
    title_line text,
    pasted_text text NOT NULL,
    item_count integer DEFAULT 0 NOT NULL,
    imported_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT submitted_reports_type_check CHECK ((report_type = ANY (ARRAY['zongbao'::text, 'wanbao'::text, 'feedback'::text])))
);


--
-- Name: review_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.review_events ALTER COLUMN id SET DEFAULT nextval('public.review_events_id_seq'::regclass);


--
-- Name: brief_batches brief_batches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.brief_batches
    ADD CONSTRAINT brief_batches_pkey PRIMARY KEY (id);


--
-- Name: brief_batches brief_batches_report_date_sequence_no_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.brief_batches
    ADD CONSTRAINT brief_batches_report_date_sequence_no_key UNIQUE (report_date, sequence_no);


--
-- Name: brief_items brief_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.brief_items
    ADD CONSTRAINT brief_items_pkey PRIMARY KEY (id);


--
-- Name: console_user_sessions console_user_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.console_user_sessions
    ADD CONSTRAINT console_user_sessions_pkey PRIMARY KEY (id);


--
-- Name: console_user_sessions console_user_sessions_token_hash_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.console_user_sessions
    ADD CONSTRAINT console_user_sessions_token_hash_unique UNIQUE (token_hash);


--
-- Name: console_users console_users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.console_users
    ADD CONSTRAINT console_users_pkey PRIMARY KEY (id);


--
-- Name: duty_schedules duty_schedules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.duty_schedules
    ADD CONSTRAINT duty_schedules_pkey PRIMARY KEY (id);


--
-- Name: duty_schedules duty_schedules_weekday_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.duty_schedules
    ADD CONSTRAINT duty_schedules_weekday_unique UNIQUE (weekday);


--
-- Name: duty_shifts duty_shifts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.duty_shifts
    ADD CONSTRAINT duty_shifts_pkey PRIMARY KEY (id);


--
-- Name: duty_shifts duty_shifts_starts_at_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.duty_shifts
    ADD CONSTRAINT duty_shifts_starts_at_unique UNIQUE (starts_at);


--
-- Name: filtered_articles filtered_articles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.filtered_articles
    ADD CONSTRAINT filtered_articles_pkey PRIMARY KEY (article_id);


--
-- Name: manual_clusters manual_clusters_cluster_id_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manual_clusters
    ADD CONSTRAINT manual_clusters_cluster_id_unique UNIQUE (cluster_id);


--
-- Name: manual_reviews manual_reviews_article_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manual_reviews
    ADD CONSTRAINT manual_reviews_article_id_key UNIQUE (article_id);


--
-- Name: manual_reviews manual_reviews_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manual_reviews
    ADD CONSTRAINT manual_reviews_pkey PRIMARY KEY (id);


--
-- Name: news_summaries news_summaries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.news_summaries
    ADD CONSTRAINT news_summaries_pkey PRIMARY KEY (article_id);


--
-- Name: news_title_embeddings news_title_embeddings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.news_title_embeddings
    ADD CONSTRAINT news_title_embeddings_pkey PRIMARY KEY (article_id);


--
-- Name: pipeline_run_steps pipeline_run_steps_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_run_steps
    ADD CONSTRAINT pipeline_run_steps_pkey PRIMARY KEY (id);


--
-- Name: pipeline_runs pipeline_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_runs
    ADD CONSTRAINT pipeline_runs_pkey PRIMARY KEY (id);


--
-- Name: pipeline_runs pipeline_runs_run_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_runs
    ADD CONSTRAINT pipeline_runs_run_id_key UNIQUE (run_id);


--
-- Name: primary_articles primary_articles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.primary_articles
    ADD CONSTRAINT primary_articles_pkey PRIMARY KEY (article_id);


--
-- Name: raw_articles raw_articles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_articles
    ADD CONSTRAINT raw_articles_pkey PRIMARY KEY (article_id);


--
-- Name: review_events review_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.review_events
    ADD CONSTRAINT review_events_pkey PRIMARY KEY (id);


--
-- Name: schema_migrations schema_migrations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schema_migrations
    ADD CONSTRAINT schema_migrations_pkey PRIMARY KEY (version);


--
-- Name: score_feedbacks score_feedbacks_article_prompt_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.score_feedbacks
    ADD CONSTRAINT score_feedbacks_article_prompt_unique UNIQUE (article_id, prompt_key, prompt_version);


--
-- Name: score_feedbacks score_feedbacks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.score_feedbacks
    ADD CONSTRAINT score_feedbacks_pkey PRIMARY KEY (id);


--
-- Name: shift_review_finalization_batches shift_review_finalization_batches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shift_review_finalization_batches
    ADD CONSTRAINT shift_review_finalization_batches_pkey PRIMARY KEY (id);


--
-- Name: shift_reviews shift_reviews_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shift_reviews
    ADD CONSTRAINT shift_reviews_pkey PRIMARY KEY (id);


--
-- Name: shift_reviews shift_reviews_shift_article_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shift_reviews
    ADD CONSTRAINT shift_reviews_shift_article_unique UNIQUE (shift_id, article_id);


--
-- Name: submission_duplicate_matches submission_duplicate_matches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_duplicate_matches
    ADD CONSTRAINT submission_duplicate_matches_pkey PRIMARY KEY (id);


--
-- Name: submission_duplicate_matches submission_duplicate_matches_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_duplicate_matches
    ADD CONSTRAINT submission_duplicate_matches_unique UNIQUE (article_id, item_id);


--
-- Name: submitted_report_items submitted_report_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submitted_report_items
    ADD CONSTRAINT submitted_report_items_pkey PRIMARY KEY (id);


--
-- Name: submitted_reports submitted_reports_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submitted_reports
    ADD CONSTRAINT submitted_reports_pkey PRIMARY KEY (id);


--
-- Name: brief_items_article_created_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX brief_items_article_created_at_idx ON public.brief_items USING btree (article_id, created_at DESC);


--
-- Name: brief_items_batch_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX brief_items_batch_idx ON public.brief_items USING btree (brief_batch_id);


--
-- Name: brief_items_section_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX brief_items_section_idx ON public.brief_items USING btree (section);


--
-- Name: console_user_sessions_expires_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX console_user_sessions_expires_at_idx ON public.console_user_sessions USING btree (expires_at);


--
-- Name: console_user_sessions_user_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX console_user_sessions_user_id_idx ON public.console_user_sessions USING btree (user_id);


--
-- Name: console_users_username_lower_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX console_users_username_lower_idx ON public.console_users USING btree (lower(username));


--
-- Name: duty_shifts_starts_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX duty_shifts_starts_at_idx ON public.duty_shifts USING btree (starts_at DESC);


--
-- Name: duty_shifts_user_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX duty_shifts_user_id_idx ON public.duty_shifts USING btree (user_id);


--
-- Name: filtered_articles_content_hash_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filtered_articles_content_hash_idx ON public.filtered_articles USING btree (content_hash) WHERE (content_hash IS NOT NULL);


--
-- Name: filtered_articles_primary_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filtered_articles_primary_idx ON public.filtered_articles USING btree (primary_article_id);


--
-- Name: filtered_articles_simhash_band1_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filtered_articles_simhash_band1_idx ON public.filtered_articles USING btree (simhash_band1);


--
-- Name: filtered_articles_simhash_band2_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filtered_articles_simhash_band2_idx ON public.filtered_articles USING btree (simhash_band2);


--
-- Name: filtered_articles_simhash_band3_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filtered_articles_simhash_band3_idx ON public.filtered_articles USING btree (simhash_band3);


--
-- Name: filtered_articles_simhash_band4_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filtered_articles_simhash_band4_idx ON public.filtered_articles USING btree (simhash_band4);


--
-- Name: filtered_articles_simhash_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filtered_articles_simhash_idx ON public.filtered_articles USING btree (simhash) WHERE (simhash IS NOT NULL);


--
-- Name: filtered_articles_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filtered_articles_status_idx ON public.filtered_articles USING btree (status);


--
-- Name: manual_clusters_bucket_key_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX manual_clusters_bucket_key_idx ON public.manual_clusters USING btree (bucket_key);


--
-- Name: manual_reviews_pending_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX manual_reviews_pending_idx ON public.manual_reviews USING btree (COALESCE(report_type, 'zongbao'::text), rank, article_id) WHERE (status = 'pending'::text);


--
-- Name: manual_reviews_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX manual_reviews_status_idx ON public.manual_reviews USING btree (status, COALESCE(report_type, 'zongbao'::text));


--
-- Name: manual_reviews_status_report_type_rank_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX manual_reviews_status_report_type_rank_idx ON public.manual_reviews USING btree (status, COALESCE(report_type, 'zongbao'::text), rank, article_id);


--
-- Name: news_summaries_beijing_gate_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX news_summaries_beijing_gate_idx ON public.news_summaries USING btree (beijing_gate_attempted_at, summary_generated_at) WHERE ((status = 'pending_beijing_gate'::text) AND (summary_status = 'completed'::text));


--
-- Name: news_summaries_created_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX news_summaries_created_at_idx ON public.news_summaries USING btree (created_at);


--
-- Name: news_summaries_external_filter_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX news_summaries_external_filter_idx ON public.news_summaries USING btree (is_beijing_related, sentiment_label, external_importance_status);


--
-- Name: news_summaries_score_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX news_summaries_score_idx ON public.news_summaries USING btree (score DESC NULLS LAST);


--
-- Name: news_summaries_search_expr_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX news_summaries_search_expr_trgm ON public.news_summaries USING gin ((((((COALESCE(title, ''::text) || ' '::text) || COALESCE(llm_summary, ''::text)) || ' '::text) || COALESCE(content_markdown, ''::text))) public.gin_trgm_ops);


--
-- Name: news_summaries_sentiment_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX news_summaries_sentiment_idx ON public.news_summaries USING btree (sentiment_label);


--
-- Name: news_summaries_status_attempt_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX news_summaries_status_attempt_idx ON public.news_summaries USING btree (summary_status, summary_attempted_at);


--
-- Name: news_summaries_status_created_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX news_summaries_status_created_at_idx ON public.news_summaries USING btree (status, created_at);


--
-- Name: news_summaries_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX news_summaries_status_idx ON public.news_summaries USING btree (status);


--
-- Name: news_summaries_summary_generated_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX news_summaries_summary_generated_idx ON public.news_summaries USING btree (summary_generated_at);


--
-- Name: pipeline_run_steps_run_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX pipeline_run_steps_run_id_idx ON public.pipeline_run_steps USING btree (run_id);


--
-- Name: pipeline_run_steps_step_name_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX pipeline_run_steps_step_name_idx ON public.pipeline_run_steps USING btree (step_name);


--
-- Name: primary_articles_primary_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX primary_articles_primary_idx ON public.primary_articles USING btree (primary_article_id);


--
-- Name: primary_articles_score_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX primary_articles_score_idx ON public.primary_articles USING btree (score DESC NULLS LAST);


--
-- Name: primary_articles_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX primary_articles_status_idx ON public.primary_articles USING btree (status);


--
-- Name: raw_articles_detail_fetched_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX raw_articles_detail_fetched_idx ON public.raw_articles USING btree (detail_fetched_at DESC);


--
-- Name: raw_articles_fetched_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX raw_articles_fetched_at_idx ON public.raw_articles USING btree (fetched_at DESC);


--
-- Name: raw_articles_search_expr_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX raw_articles_search_expr_trgm ON public.raw_articles USING gin ((((COALESCE(title, ''::text) || ' '::text) || COALESCE(content_markdown, ''::text))) public.gin_trgm_ops);


--
-- Name: review_events_actor_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX review_events_actor_idx ON public.review_events USING btree (actor_user_id);


--
-- Name: review_events_created_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX review_events_created_at_idx ON public.review_events USING btree (created_at DESC);


--
-- Name: review_events_target_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX review_events_target_idx ON public.review_events USING btree (target_type, target_id);


--
-- Name: score_feedbacks_prompt_version_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX score_feedbacks_prompt_version_idx ON public.score_feedbacks USING btree (prompt_key, prompt_version);


--
-- Name: score_feedbacks_submitted_by_user_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX score_feedbacks_submitted_by_user_idx ON public.score_feedbacks USING btree (submitted_by_user_id);


--
-- Name: shift_review_finalization_batches_shift_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX shift_review_finalization_batches_shift_idx ON public.shift_review_finalization_batches USING btree (shift_id, report_type, finalized_at);


--
-- Name: shift_reviews_admin_discarded_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX shift_reviews_admin_discarded_idx ON public.shift_reviews USING btree (shift_id, admin_discarded_at DESC) WHERE (admin_discarded_at IS NOT NULL);


--
-- Name: shift_reviews_article_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX shift_reviews_article_id_idx ON public.shift_reviews USING btree (article_id);


--
-- Name: shift_reviews_created_by_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX shift_reviews_created_by_idx ON public.shift_reviews USING btree (created_by_user_id);


--
-- Name: shift_reviews_current_selected_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX shift_reviews_current_selected_idx ON public.shift_reviews USING btree (shift_id, report_type, rank) WHERE ((decision = 'selected'::text) AND (finalized_batch_id IS NULL));


--
-- Name: shift_reviews_finalized_batch_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX shift_reviews_finalized_batch_idx ON public.shift_reviews USING btree (finalized_batch_id, finalized_rank) WHERE (finalized_batch_id IS NOT NULL);


--
-- Name: shift_reviews_shift_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX shift_reviews_shift_id_idx ON public.shift_reviews USING btree (shift_id);


--
-- Name: shift_reviews_updated_by_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX shift_reviews_updated_by_idx ON public.shift_reviews USING btree (updated_by_user_id);


--
-- Name: submission_duplicate_matches_article_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_duplicate_matches_article_idx ON public.submission_duplicate_matches USING btree (article_id);


--
-- Name: submission_duplicate_matches_state_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submission_duplicate_matches_state_idx ON public.submission_duplicate_matches USING btree (state);


--
-- Name: submitted_report_items_article_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submitted_report_items_article_idx ON public.submitted_report_items USING btree (article_id) WHERE (article_id IS NOT NULL);


--
-- Name: submitted_report_items_link_pending_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submitted_report_items_link_pending_idx ON public.submitted_report_items USING btree (link_status) WHERE (link_status = 'pending'::text);


--
-- Name: submitted_report_items_norm_hash_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submitted_report_items_norm_hash_idx ON public.submitted_report_items USING btree (norm_title_hash);


--
-- Name: submitted_report_items_report_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submitted_report_items_report_idx ON public.submitted_report_items USING btree (report_id, order_index);


--
-- Name: submitted_reports_report_date_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submitted_reports_report_date_idx ON public.submitted_reports USING btree (report_date DESC);


--
-- Name: submitted_reports_type_date_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX submitted_reports_type_date_idx ON public.submitted_reports USING btree (report_type, report_date DESC);


--
-- Name: brief_batches brief_batches_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER brief_batches_set_updated_at BEFORE UPDATE ON public.brief_batches FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: brief_items brief_items_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER brief_items_set_updated_at BEFORE UPDATE ON public.brief_items FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: filtered_articles filtered_articles_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER filtered_articles_set_updated_at BEFORE UPDATE ON public.filtered_articles FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: news_summaries news_summaries_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER news_summaries_set_updated_at BEFORE UPDATE ON public.news_summaries FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: pipeline_runs pipeline_runs_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER pipeline_runs_set_updated_at BEFORE UPDATE ON public.pipeline_runs FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: primary_articles primary_articles_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER primary_articles_set_updated_at BEFORE UPDATE ON public.primary_articles FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: raw_articles raw_articles_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER raw_articles_set_updated_at BEFORE UPDATE ON public.raw_articles FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: brief_items brief_items_brief_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.brief_items
    ADD CONSTRAINT brief_items_brief_batch_id_fkey FOREIGN KEY (brief_batch_id) REFERENCES public.brief_batches(id) ON DELETE CASCADE;


--
-- Name: console_user_sessions console_user_sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.console_user_sessions
    ADD CONSTRAINT console_user_sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.console_users(id) ON DELETE RESTRICT;


--
-- Name: duty_schedules duty_schedules_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.duty_schedules
    ADD CONSTRAINT duty_schedules_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.console_users(id) ON DELETE RESTRICT;


--
-- Name: duty_shifts duty_shifts_created_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.duty_shifts
    ADD CONSTRAINT duty_shifts_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.console_users(id) ON DELETE RESTRICT;


--
-- Name: duty_shifts duty_shifts_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.duty_shifts
    ADD CONSTRAINT duty_shifts_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.console_users(id) ON DELETE RESTRICT;


--
-- Name: filtered_articles filtered_articles_primary_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.filtered_articles
    ADD CONSTRAINT filtered_articles_primary_fk FOREIGN KEY (primary_article_id) REFERENCES public.filtered_articles(article_id) ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED;


--
-- Name: filtered_articles filtered_articles_raw_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.filtered_articles
    ADD CONSTRAINT filtered_articles_raw_fk FOREIGN KEY (article_id) REFERENCES public.raw_articles(article_id) ON DELETE CASCADE;


--
-- Name: manual_reviews manual_reviews_article_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manual_reviews
    ADD CONSTRAINT manual_reviews_article_id_fkey FOREIGN KEY (article_id) REFERENCES public.news_summaries(article_id) ON DELETE CASCADE;


--
-- Name: manual_reviews manual_reviews_decided_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manual_reviews
    ADD CONSTRAINT manual_reviews_decided_by_user_id_fkey FOREIGN KEY (decided_by_user_id) REFERENCES public.console_users(id) ON DELETE RESTRICT;


--
-- Name: news_title_embeddings news_title_embeddings_article_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.news_title_embeddings
    ADD CONSTRAINT news_title_embeddings_article_id_fkey FOREIGN KEY (article_id) REFERENCES public.news_summaries(article_id) ON DELETE CASCADE;


--
-- Name: pipeline_run_steps pipeline_run_steps_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_run_steps
    ADD CONSTRAINT pipeline_run_steps_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.pipeline_runs(run_id) ON DELETE CASCADE;


--
-- Name: primary_articles primary_articles_filtered_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.primary_articles
    ADD CONSTRAINT primary_articles_filtered_fk FOREIGN KEY (article_id) REFERENCES public.filtered_articles(article_id) ON DELETE CASCADE;


--
-- Name: primary_articles primary_articles_primary_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.primary_articles
    ADD CONSTRAINT primary_articles_primary_fk FOREIGN KEY (primary_article_id) REFERENCES public.filtered_articles(article_id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED;


--
-- Name: score_feedbacks score_feedbacks_article_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.score_feedbacks
    ADD CONSTRAINT score_feedbacks_article_id_fkey FOREIGN KEY (article_id) REFERENCES public.news_summaries(article_id) ON DELETE CASCADE;


--
-- Name: score_feedbacks score_feedbacks_submitted_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.score_feedbacks
    ADD CONSTRAINT score_feedbacks_submitted_by_user_id_fkey FOREIGN KEY (submitted_by_user_id) REFERENCES public.console_users(id) ON DELETE RESTRICT;


--
-- Name: shift_review_finalization_batches shift_review_finalization_batches_finalized_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shift_review_finalization_batches
    ADD CONSTRAINT shift_review_finalization_batches_finalized_by_user_id_fkey FOREIGN KEY (finalized_by_user_id) REFERENCES public.console_users(id) ON DELETE RESTRICT;


--
-- Name: shift_review_finalization_batches shift_review_finalization_batches_shift_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shift_review_finalization_batches
    ADD CONSTRAINT shift_review_finalization_batches_shift_id_fkey FOREIGN KEY (shift_id) REFERENCES public.duty_shifts(id) ON DELETE RESTRICT;


--
-- Name: shift_reviews shift_reviews_admin_discarded_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shift_reviews
    ADD CONSTRAINT shift_reviews_admin_discarded_by_user_id_fkey FOREIGN KEY (admin_discarded_by_user_id) REFERENCES public.console_users(id) ON DELETE RESTRICT;


--
-- Name: shift_reviews shift_reviews_created_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shift_reviews
    ADD CONSTRAINT shift_reviews_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.console_users(id) ON DELETE RESTRICT;


--
-- Name: shift_reviews shift_reviews_finalized_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shift_reviews
    ADD CONSTRAINT shift_reviews_finalized_batch_id_fkey FOREIGN KEY (finalized_batch_id) REFERENCES public.shift_review_finalization_batches(id) ON DELETE RESTRICT;


--
-- Name: shift_reviews shift_reviews_shift_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shift_reviews
    ADD CONSTRAINT shift_reviews_shift_id_fkey FOREIGN KEY (shift_id) REFERENCES public.duty_shifts(id) ON DELETE RESTRICT;


--
-- Name: shift_reviews shift_reviews_updated_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shift_reviews
    ADD CONSTRAINT shift_reviews_updated_by_user_id_fkey FOREIGN KEY (updated_by_user_id) REFERENCES public.console_users(id) ON DELETE RESTRICT;


--
-- Name: submission_duplicate_matches submission_duplicate_matches_decided_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_duplicate_matches
    ADD CONSTRAINT submission_duplicate_matches_decided_by_fkey FOREIGN KEY (decided_by) REFERENCES public.console_users(id) ON DELETE SET NULL;


--
-- Name: submission_duplicate_matches submission_duplicate_matches_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submission_duplicate_matches
    ADD CONSTRAINT submission_duplicate_matches_item_id_fkey FOREIGN KEY (item_id) REFERENCES public.submitted_report_items(id) ON DELETE CASCADE;


--
-- Name: submitted_report_items submitted_report_items_link_decided_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submitted_report_items
    ADD CONSTRAINT submitted_report_items_link_decided_by_fkey FOREIGN KEY (link_decided_by) REFERENCES public.console_users(id) ON DELETE SET NULL;


--
-- Name: submitted_report_items submitted_report_items_report_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submitted_report_items
    ADD CONSTRAINT submitted_report_items_report_id_fkey FOREIGN KEY (report_id) REFERENCES public.submitted_reports(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict X7rgTp0FCMQhEMmikZA1KKtk1iPM8AzJwoqXiHxRouckR6b9KmjXTFgBy1CiDi0


--
-- Dbmate schema migrations
--

INSERT INTO public.schema_migrations (version) VALUES
    ('20241112105800'),
    ('20250219090000'),
    ('20250304090000'),
    ('20250926172450'),
    ('20251001151834'),
    ('20251006090000'),
    ('20251006113000'),
    ('20251007194500'),
    ('20251008153000'),
    ('20251008160000'),
    ('20251018120000'),
    ('20251018121500'),
    ('20251018143000'),
    ('20251018170010'),
    ('20251021103000'),
    ('20251104120000'),
    ('20251104133000'),
    ('20251105100000'),
    ('20251130090000'),
    ('20251130101000'),
    ('20251201093000'),
    ('20251201100000'),
    ('20251202090000'),
    ('20260111090000'),
    ('20260722090000'),
    ('20260724190000'),
    ('20260724200000'),
    ('20260724210000'),
    ('20260725100000'),
    ('20260726110000'),
    ('20260726130000'),
    ('20260727100000'),
    ('20260727203000'),
    ('20260728100000'),
    ('20260728120000'),
    ('20260729120000'),
    ('20260729130000'),
    ('20260729130100'),
    ('20260729130200'),
    ('20260729220000'),
    ('20260731232631'),
    ('20260804180449'),
    ('20260810120000'),
    ('20260812120000');
