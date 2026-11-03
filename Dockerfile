# Bara web app: forecast, triage, dispatch and the field-report pipeline on port 8600.
# The processed tables in data/ are not in the repository (see README, "Run it"); mount them,
# and mount logs/ so the Meta API spend ledger (and its cap) is shared with the host:
#   docker build -t bara .
#   docker run -p 8600:8600 -v "$(pwd)/data:/app/data" -v "$(pwd)/logs:/app/logs" --env-file .env bara
FROM python:3.12-slim

# libgomp1 for LightGBM, ffmpeg to turn WhatsApp voice notes (ogg) into wav
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
ENV FFMPEG=ffmpeg
EXPOSE 8600
CMD ["uvicorn", "server:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8600"]
