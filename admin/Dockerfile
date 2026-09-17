FROM python:3.12-alpine

RUN pip install --no-cache-dir fastapi==0.115.12 uvicorn==0.34.2

# non-root; /config write access comes from the host dir's permissions
RUN adduser -D -u 1000 mtv
USER mtv

WORKDIR /app
COPY main.py index.html ./

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
