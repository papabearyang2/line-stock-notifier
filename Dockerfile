FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Vercel uses api/index.py as entry point, we match that here
CMD ["uvicorn", "api.index:app", "--host", "0.0.0.0", "--port", "8000"]
