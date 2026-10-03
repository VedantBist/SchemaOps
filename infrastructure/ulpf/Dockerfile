# CausalOps ULPF: intake + API (default command), pipeline worker and log replayer share this image.
FROM python:3.12-slim
WORKDIR /app
COPY infrastructure/ulpf/requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --default-timeout=120 --retries 10 -r requirements.txt
COPY ml/__init__.py ml/__init__.py
COPY ml/ulpf ml/ulpf
ENV PYTHONUNBUFFERED=1 ULPF_VAULT_DIR=/var/lib/ulpf/vault ULPF_TLS_DIR=/var/lib/ulpf/tls
EXPOSE 8090 5514/udp 5514/tcp 6514/tcp
CMD ["uvicorn", "ml.ulpf.api:app", "--host", "0.0.0.0", "--port", "8090"]
