FROM python:3.11-slim
WORKDIR /app


ENV PORT=80

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
EXPOSE 80

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "${PORT}"]