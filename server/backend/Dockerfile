# 기본 이미지도 패치 버전까지 고정 (재빌드 시 Python·OS 패키지가 바뀌지 않도록)
FROM python:3.11.16-slim

WORKDIR /app

# 시스템 의존성
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && rm -rf /var/lib/apt/lists/*

# 간접 의존성까지 constraints.txt 로 고정해 캐시 없이 빌드해도 같은 버전이 설치되게 한다
COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt

COPY app/ ./app/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
