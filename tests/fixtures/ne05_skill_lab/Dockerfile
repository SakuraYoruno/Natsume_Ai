FROM python:3.12-alpine

ARG LAB_VERSION=unversioned
ENV NE05_LAB_VERSION=${LAB_VERSION} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY lab_server.py /app/lab_server.py
COPY lab_worker.py /app/lab_worker.py

USER 65532:65532
EXPOSE 8080
HEALTHCHECK --interval=2s --timeout=1s --retries=15 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=0.5).read()"]
ENTRYPOINT ["python", "/app/lab_server.py"]
