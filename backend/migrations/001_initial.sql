--
-- PostgreSQL database dump
--

\restrict BfWF97FxO8IFeK2VMnDkWNNbwX7BSN5jpdaShZxAsSbwa9c83qecCpECYHRE4Dg

-- Dumped from database version 16.15
-- Dumped by pg_dump version 16.15

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: audits; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audits (
    id integer NOT NULL,
    actor character varying(100),
    action character varying(200),
    created character varying(40)
);


--
-- Name: audits_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.audits_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: audits_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.audits_id_seq OWNED BY public.audits.id;


--
-- Name: outreaches; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.outreaches (
    id integer NOT NULL,
    segment_id integer,
    cluster_id integer,
    title character varying(100),
    note text,
    members json,
    status character varying(20)
);


--
-- Name: outreaches_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.outreaches_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: outreaches_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.outreaches_id_seq OWNED BY public.outreaches.id;


--
-- Name: readers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.readers (
    id integer NOT NULL,
    code character varying(60) NOT NULL,
    weekly_reading double precision,
    completion double precision,
    night_share double precision,
    note text
);


--
-- Name: readers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.readers_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: readers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.readers_id_seq OWNED BY public.readers.id;


--
-- Name: segments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.segments (
    id integer NOT NULL,
    name character varying(100),
    k integer,
    features json,
    state character varying(20),
    result json,
    error text
);


--
-- Name: segments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.segments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: segments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.segments_id_seq OWNED BY public.segments.id;


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id integer NOT NULL,
    email character varying(100),
    password character varying(200),
    role character varying(20)
);


--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;


--
-- Name: audits id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audits ALTER COLUMN id SET DEFAULT nextval('public.audits_id_seq'::regclass);


--
-- Name: outreaches id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outreaches ALTER COLUMN id SET DEFAULT nextval('public.outreaches_id_seq'::regclass);


--
-- Name: readers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.readers ALTER COLUMN id SET DEFAULT nextval('public.readers_id_seq'::regclass);


--
-- Name: segments id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.segments ALTER COLUMN id SET DEFAULT nextval('public.segments_id_seq'::regclass);


--
-- Name: users id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- Name: audits audits_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audits
    ADD CONSTRAINT audits_pkey PRIMARY KEY (id);


--
-- Name: outreaches outreaches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outreaches
    ADD CONSTRAINT outreaches_pkey PRIMARY KEY (id);


--
-- Name: readers readers_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.readers
    ADD CONSTRAINT readers_code_key UNIQUE (code);


--
-- Name: readers readers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.readers
    ADD CONSTRAINT readers_pkey PRIMARY KEY (id);


--
-- Name: segments segments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.segments
    ADD CONSTRAINT segments_pkey PRIMARY KEY (id);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: outreaches outreaches_segment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outreaches
    ADD CONSTRAINT outreaches_segment_id_fkey FOREIGN KEY (segment_id) REFERENCES public.segments(id);


--
-- 稳定性复核与触达冻结依据（对应 src/models/entities.py 的 Review / OutreachBasis）
--

CREATE TABLE public.reviews (
    id integer NOT NULL,
    segment_id integer NOT NULL,
    state character varying(20),
    rounds integer,
    threshold double precision,
    features json,
    k integer,
    snapshot json,
    checksum character varying(64),
    data json,
    error text,
    created character varying(40),
    updated character varying(40)
);

CREATE SEQUENCE public.reviews_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.reviews_id_seq OWNED BY public.reviews.id;
ALTER TABLE ONLY public.reviews ALTER COLUMN id SET DEFAULT nextval('public.reviews_id_seq'::regclass);
ALTER TABLE ONLY public.reviews ADD CONSTRAINT reviews_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.reviews ADD CONSTRAINT reviews_segment_id_fkey FOREIGN KEY (segment_id) REFERENCES public.segments(id);

CREATE TABLE public.outreach_bases (
    id integer NOT NULL,
    outreach_id integer NOT NULL,
    review_id integer NOT NULL,
    review_digest character varying(64),
    members json,
    excluded json,
    evidence json,
    created character varying(40),
    approved_at character varying(40),
    approved_digest character varying(64)
);

CREATE SEQUENCE public.outreach_bases_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.outreach_bases_id_seq OWNED BY public.outreach_bases.id;
ALTER TABLE ONLY public.outreach_bases ALTER COLUMN id SET DEFAULT nextval('public.outreach_bases_id_seq'::regclass);
ALTER TABLE ONLY public.outreach_bases ADD CONSTRAINT outreach_bases_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.outreach_bases ADD CONSTRAINT outreach_bases_outreach_id_key UNIQUE (outreach_id);
ALTER TABLE ONLY public.outreach_bases ADD CONSTRAINT outreach_bases_outreach_id_fkey FOREIGN KEY (outreach_id) REFERENCES public.outreaches(id);
ALTER TABLE ONLY public.outreach_bases ADD CONSTRAINT outreach_bases_review_id_fkey FOREIGN KEY (review_id) REFERENCES public.reviews(id);

--
-- PostgreSQL database dump complete
--

\unrestrict BfWF97FxO8IFeK2VMnDkWNNbwX7BSN5jpdaShZxAsSbwa9c83qecCpECYHRE4Dg


