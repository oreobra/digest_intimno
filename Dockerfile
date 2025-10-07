FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && \
    python - <<'PY' || true
import nltk
try:
    nltk.download('punkt', download_dir='/usr/local/nltk_data')
except Exception:
    pass
PY
COPY . .
ENV TZ=Europe/Amsterdam
CMD ["python", "main.py"]
