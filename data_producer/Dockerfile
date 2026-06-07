FROM python:3.9-slim
WORKDIR /app
# Unbuffered Python stdout so per-reading print()s reach docker logs live.
ENV PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "producer.py"]
