FROM python:3.10.12

WORKDIR /app


COPY . .
RUN pip install pyjwt
RUN pip install python-multipart
RUN pip install --no-cache -r requirements.txt

EXPOSE 6000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "6000", "--workers", "4", "--timeout-keep-alive", "300"]
