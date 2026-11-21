# The scorer builds this on linux/amd64 and runs it with no network. The
# digest is the image we labeled every commit in: its Python and SQLite
# decide which sqlglot tests run, so keep it if your image runs them.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
WORKDIR /app
COPY requirements.lock ./
RUN python -m pip install --no-cache-dir -r requirements.lock
COPY fetch_source.py ./
RUN python fetch_source.py
ENV PYTHONDONTWRITEBYTECODE=1
COPY entry.py operand_proofs.py semantic_proofs.py attribute_dependencies.py statement_scope.py verification_plan.py verification.py verify_worker.py execution_support.py network_guard.py ./
COPY *.json.gz ./
CMD ["python", "/app/entry.py"]
