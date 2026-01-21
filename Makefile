UV ?= uv

# Ruff
lint: ## Проверяет линтерами код в репозитории
	ruff check .

format: ## Запуск автоформатера
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

admin: ## Создать администратора
	$(UV) run python manage.py createsuperuser

list: ## Отображает список доступных команд и их описания
	@echo "Cписок доступных команд:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}'
