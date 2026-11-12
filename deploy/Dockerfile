FROM public.ecr.aws/docker/library/python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
COPY requirements.txt .
RUN sed '/^# M3/,$d' requirements.txt >/tmp/requirements-api.txt \
    && pip install -r /tmp/requirements-api.txt \
    && useradd --uid 1000 --create-home resume \
    && install -d -o resume -g resume /data/storage
FROM base AS worker
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-writer libseccomp2 libgomp1 fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && pip install -r requirements.txt \
    && pip install --no-deps --target /opt/resume-cv opencv-contrib-python-headless==4.10.0.84 \
    && install -d -o resume -g resume /home/resume/.paddlex
# PaddleX installs the GUI wheel transitively. Prefer the matching headless wheel in the worker.
ENV PYTHONPATH=/opt/resume-cv
COPY app/ app/
COPY worker/ worker/
USER resume
CMD ["celery","-A","worker.celery_app","worker","-l","info","--concurrency","1"]

FROM base AS api
COPY app/ app/
COPY worker/ worker/
COPY migrations/ migrations/
USER resume
EXPOSE 8000
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000"]
