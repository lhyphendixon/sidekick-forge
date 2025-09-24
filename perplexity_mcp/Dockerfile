FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8081

WORKDIR /app

RUN python -m pip install --no-cache-dir --upgrade pip

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY . ./

EXPOSE ${PORT}

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8081"]
