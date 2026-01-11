\restrict sas1vez51DCxa5kwENjNVsmZPPNHJa6Jd7npkfKM9fvhJIVktNiTeDlWDTFbcTy

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
    report_type text DEFAULT 'zongbao'::text NOT NULL,
    bucket_key text NOT NULL,
    cluster_id text NOT NULL,
    item_ids text[] NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT manual_clusters_bucket_key_check CHECK ((bucket_key = ANY (ARRAY['internal_positive'::text, 'internal_negative'::text, 'external_positive'::text, 'external_negative'::text])))
);


--
-- Name: manual_export_batches; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.manual_export_batches (
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
-- Name: manual_export_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.manual_export_items (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    manual_export_batch_id uuid NOT NULL,
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
    CONSTRAINT manual_reviews_report_type_check CHECK ((report_type = ANY (ARRAY['zongbao'::text, 'wanbao'::text]))),
    CONSTRAINT manual_reviews_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'selected'::text, 'backup'::text, 'discarded'::text, 'exported'::text])))
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
    beijing_gate_fail_count integer DEFAULT 0 NOT NULL
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
-- Name: schema_migrations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schema_migrations (
    version character varying(128) NOT NULL
);


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
-- Name: manual_export_batches manual_export_batches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manual_export_batches
    ADD CONSTRAINT manual_export_batches_pkey PRIMARY KEY (id);


--
-- Name: manual_export_batches manual_export_batches_report_date_sequence_no_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manual_export_batches
    ADD CONSTRAINT manual_export_batches_report_date_sequence_no_key UNIQUE (report_date, sequence_no);


--
-- Name: manual_export_items manual_export_items_manual_export_batch_id_article_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manual_export_items
    ADD CONSTRAINT manual_export_items_manual_export_batch_id_article_id_key UNIQUE (manual_export_batch_id, article_id);


--
-- Name: manual_export_items manual_export_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manual_export_items
    ADD CONSTRAINT manual_export_items_pkey PRIMARY KEY (id);


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
-- Name: schema_migrations schema_migrations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schema_migrations
    ADD CONSTRAINT schema_migrations_pkey PRIMARY KEY (version);


--
-- Name: brief_items_batch_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX brief_items_batch_idx ON public.brief_items USING btree (brief_batch_id);


--
-- Name: brief_items_section_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX brief_items_section_idx ON public.brief_items USING btree (section);


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
-- Name: manual_export_items_batch_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX manual_export_items_batch_idx ON public.manual_export_items USING btree (manual_export_batch_id);


--
-- Name: manual_export_items_section_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX manual_export_items_section_idx ON public.manual_export_items USING btree (section);


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
-- Name: news_summaries_external_filter_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX news_summaries_external_filter_idx ON public.news_summaries USING btree (is_beijing_related, sentiment_label, external_importance_status);


--
-- Name: news_summaries_score_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX news_summaries_score_idx ON public.news_summaries USING btree (score DESC NULLS LAST);


--
-- Name: news_summaries_sentiment_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX news_summaries_sentiment_idx ON public.news_summaries USING btree (sentiment_label);


--
-- Name: news_summaries_status_attempt_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX news_summaries_status_attempt_idx ON public.news_summaries USING btree (summary_status, summary_attempted_at);


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
-- Name: manual_export_batches manual_export_batches_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER manual_export_batches_set_updated_at BEFORE UPDATE ON public.manual_export_batches FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: manual_export_items manual_export_items_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER manual_export_items_set_updated_at BEFORE UPDATE ON public.manual_export_items FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


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
-- Name: manual_export_items manual_export_items_manual_export_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manual_export_items
    ADD CONSTRAINT manual_export_items_manual_export_batch_id_fkey FOREIGN KEY (manual_export_batch_id) REFERENCES public.manual_export_batches(id) ON DELETE CASCADE;


--
-- Name: manual_reviews manual_reviews_article_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manual_reviews
    ADD CONSTRAINT manual_reviews_article_id_fkey FOREIGN KEY (article_id) REFERENCES public.news_summaries(article_id) ON DELETE CASCADE;


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
-- PostgreSQL database dump complete
--

\unrestrict sas1vez51DCxa5kwENjNVsmZPPNHJa6Jd7npkfKM9fvhJIVktNiTeDlWDTFbcTy


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
    ('20260111090000');
