FROM python:3.14-slim
RUN apt-get update && apt-get install -y libcairo2

RUN pip install --upgrade pip

COPY requirements.txt /tmp/requirements.txt

#================================================
# PIP packages
#================================================
RUN pip install --no-cache-dir -r /tmp/requirements.txt

#================================================
# Code
#================================================
RUN useradd -m -d /proj -s /bin/bash app
COPY . /proj
WORKDIR /proj
RUN mkdir -p data && chown -R app:app /proj/data
USER app