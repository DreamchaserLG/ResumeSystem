FROM pgvector/pgvector:0.8.7-pg16 AS pgvector
FROM public.ecr.aws/docker/library/postgres:16
COPY --from=pgvector /usr/lib/postgresql/16/lib/vector.so /usr/lib/postgresql/16/lib/
COPY --from=pgvector /usr/share/postgresql/16/extension/vector* /usr/share/postgresql/16/extension/
