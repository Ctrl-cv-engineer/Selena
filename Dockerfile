FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY DialogueSystem ./DialogueSystem
COPY MemorySystem ./MemorySystem
COPY project_config.py logging_utils.py ./

RUN mkdir -p DialogueSystem/data DialogueSystem/history DialogueSystem/logs

EXPOSE 8000 5173

CMD ["python", "-m", "DialogueSystem.main"]
