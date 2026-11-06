.PHONY: install data train test demo up down
install:
	pip install -r requirements.txt
data:
	python -m scripts.generate_data --seed 42 --per-scenario 20
train:
	python -m scripts.train_model
test:
	python -m pytest -q
demo:        # no Kafka/Postgres/API keys needed; synthetic data, simulated vision
	streamlit run frontend/app.py
up:
	docker compose up --build
down:
	docker compose down -v
