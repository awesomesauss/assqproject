FROM python:3.9-slim-bullseye

RUN apt-get update && apt-get install -y \
    gcc \
    python3-dev \
    libcamera-tools \
    i2c-tools \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt
RUN pip install pytest pytest-asyncio

COPY . .

CMD ["python", "car_control.py"]
