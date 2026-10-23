FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Credentials are supplied at runtime, never copied into image layers.
COPY server.py .

EXPOSE 8000

CMD ["python", "server.py"]
