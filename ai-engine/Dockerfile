# CausalOps AI engine. Built from the repository root so the engine code (ml/engine) is inside the image:
# the published image runs anywhere without mounting the source tree.
FROM python:3.12-slim
WORKDIR /app
COPY ai-engine/requirements.txt .
# Wheels are cached between builds; long timeouts and retries tolerate slow networks.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --default-timeout=120 --retries 10 -r requirements.txt
COPY ml/__init__.py ml/__init__.py
COPY ml/engine ml/engine
COPY ai-engine/app app
EXPOSE 8000
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000"]
