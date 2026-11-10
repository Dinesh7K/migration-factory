.PHONY: install data triage migrate demo test app sqlserver

install:
	pip install -r requirements.txt
data:
	python run.py data
triage:
	python run.py triage
migrate:
	python run.py migrate
demo:
	python run.py demo
test:
	pytest -q
app:
	streamlit run app/app.py
sqlserver:
	docker compose up -d
