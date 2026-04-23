UV ?= uv

# Ruff
lint: ## Проверяет линтерами код в репозитории
	ruff check .

fix: ## Запуск автоформатера
	ruff check --fix .

up-develop: ## Запустить окружение
	$(UV) run docker-compose -f docker-compose.develop.yml up -d

down-develop: ## Остановить окружение
	$(UV) run docker-compose -f docker-compose.develop.yml down

down-v: ## Остановить окружение с очисткой хранилищ
	$(UV) run docker-compose -f docker-compose.develop.yml down -v

makemigrations: ## Сделать миграции
	$(UV) run python manage.py makemigrations

migrate: ## Применить миграции
	$(UV) run python manage.py migrate

runserver-observability: ## Запустить Django для локального Prometheus/Grafana
	$(UV) run python manage.py runserver 0.0.0.0:8000

admin: ## Создать администратора
	$(UV) run python manage.py createsuperuser

tailwind: ## Запустить сервер tailwind
	@if [ -x .venv/bin/python ]; then \
		.venv/bin/python manage.py tailwind watch; \
	else \
		python manage.py tailwind watch; \
	fi

celery-worker: ## Запустить воркер celery
	$(UV) run celery -A config worker -l info

celery-beat: ## Запустить celery beat
	$(UV) run celery -A config beat -l info

prod-compose-build: ## Собрать production-образ (контекст — корень репозитория)
	docker compose -f deploy/docker-compose.prod.yml build web

prod-compose-up: ## Поднять production stack (нужен deploy/.env)
	cd deploy && docker compose -f docker-compose.prod.yml up -d

list: ## Отображает список доступных команд и их описания
	@echo "Cписок доступных команд:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}'
