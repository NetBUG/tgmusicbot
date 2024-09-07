FROM python:3.12-alpine

ARG OUTPUT_FOLDER
ARG TG_TOKEN

WORKDIR /app

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache python3.12 -m pip install -r /app/requirements.txt

COPY . .

ENTRYPOINT ["python3.12", "app.py"]