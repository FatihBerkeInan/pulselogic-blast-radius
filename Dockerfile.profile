FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
WORKDIR /app
COPY requirements.lock fetch_source.py ./
RUN python -m pip install --no-cache-dir -r requirements.lock
RUN python fetch_source.py
COPY profile_baseline.py operand_proofs.py execution_support.py network_guard.py dependencies.json.gz ./
ENV PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
RUN --network=none python -u profile_baseline.py
