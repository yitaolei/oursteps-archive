FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY archive.py ./
COPY oursteps ./oursteps
COPY SPEC.md ./
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "archive.py", "--data", "/data"]
CMD ["report"]
