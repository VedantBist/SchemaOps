FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
# Wheels are cached between builds; long timeouts and retries tolerate slow networks.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --default-timeout=120 --retries 10 -r requirements.txt
COPY app app
EXPOSE 8000
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000"]
