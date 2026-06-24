FROM python:3.10-slim

# Install Node.js and FFmpeg
RUN apt-get update && apt-get install -y nodejs npm ffmpeg

WORKDIR /app
COPY . .

RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "bot.py"]
