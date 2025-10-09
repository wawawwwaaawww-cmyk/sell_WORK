# Seller Krypto – Implementation Plan

## Rules for Maintaining this Document
- Treat this file as the single source of truth for project TODOs and roadmap.
- Every change to scope or status must update this document before coding continues.
- Use the following status keywords: `todo`, `in-progress`, `blocked`, `done`, `icebox`.
- Log blocker notes inline (e.g. `blocked – waiting for …`).
- When work is finished and merged, move the item to the **Done** section with date.
- Keep items short (one–three sentences) and reference responsible module/file when possible.

## Next Up
## Backlog
- `icebox` – (пусто, синхронизировано 2025-10-03)

## Work in Progress
- (пока нет активных задач)

## Done
- 2025-10-03 – Документирован pytest asyncio loop scope и регламент тестов (pytest.ini, docs/TESTING_GUIDE.md).
- 2025-10-03 – Проверены prod-конфиги: DATABASE_URL_SYNC, миграции, деплой-скрипты (docs/POSTGRES_MIGRATION_CHECKLIST.md, .env.example).
- 2025-10-03 – Проверка административных ролей и UI для материалов/продуктов (app/handlers/admin_full.py, docs/ADMIN_CHECKLIST.md).
- 2025-10-03 – Scheduler: довоз A/B победителя, follow-up и очистка задач (app/services/scheduler_service.py).
- 2025-10-03 – PolicyLayer fallback и эскалации LLM при низкой уверенности (app/services/llm_service.py, tests/test_llm_service.py).
- 2025-10-03 – Менеджерский Telegram UI: `/dashboard`, просмотр A/B и статусов рассылок (app/handlers/admin_full.py, app/services/analytics_formatter.py).
- 2025-10-03 – FastAPI `/analytics` с JSON/summary режимами и unit-тестами (app/api/routes/analytics.py, app/services/analytics_formatter.py, tests/test_api_analytics.py).
- 2025-10-01 – Тестовая среда переведена на PostgreSQL; удалены остатки SQLite (tests/conftest.py, app/db.py, requirements*).
- 2025-10-02 – Добавлены автоматические тесты для рассылок и A/B сервисов, детерминированное распределение вариантов (app/services/ab_testing_service.py, tests/test_broadcast_service.py).
- 2025-10-01 – Message history mode & conversation logging (app/handlers/start.py, app/handlers/survey.py, app/services/logging_service.py, README.md, .env.*).
- 2025-10-01 – Analytics metrics extension & pytest coverage plan (app/services/analytics_service.py, docs/ANALYTICS_TEST_PLAN.md).
- 2025-10-01 – PostgreSQL migration checklist & config updates (docs/POSTGRES_MIGRATION_CHECKLIST.md, README.md).
- 2025-09-27 – Payment workflow helper: лендинги для тарифов, manual handoff, обновлённый spec (app/services/payment_service.py, app/handlers/payments.py, docs/PAYMENT_WORKFLOW_HELPER.md).
- 2025-09-26 - Manager handoff flow spec и регламент передачи лида (docs/MANAGER_HANDOFF_SPEC.md).
- 2025-09-26 - Scenario engine refactor spec + milestone plan (docs/SCENARIO_ENGINE_REFACTOR_SPEC.md).
- 2025-09-26 - Course schedule awareness spec + response hook plan (docs/COURSE_SCHEDULE_AWARENESS_SPEC.md).
- 2025-09-26 - Course content ingestion spec + delta sync plan (docs/COURSE_CONTENT_INGESTION_SPEC.md).
- 2025-09-24 – Обновлен каталог материалов: новая модель БД, сервисы и миграция (app/models.py, app/repositories/material_repository.py, app/services/materials_service.py, app/services/bonus_service.py, migrations/versions/20240924_materials_catalogue.py).
- 2025-09-24 – Перенастроены command/callback хэндлеры на config-driven `process_trigger` (app/handlers/scene_dispatcher.py, app/handlers/start.py, app/handlers/survey.py, app/handlers/consultation.py, app/handlers/help_faq.py).
- 2025-09-23 – Сформировано обновлённое ТЗ (docs/TECH_SPEC_CODEX.md).
- 2025-09-23 – Завершён обзор плана и синхронизация с ТЗ.
- 2025-09-23 – Сформирована карта переходов Scenario Engine (docs/SCENARIO_ENGINE_PLAN.md).
- 2025-09-23 – Подготовлена схема материалов и стратегия хранения (docs/MATERIALS_DATA_MODEL.md).
- 2025-09-23 – Описан пайплайн импорта материалов (docs/MATERIALS_INGESTION_BLUEPRINT.md).
- 2025-09-23 – Подготовлен YAML-конфиг переходов и гайд по действиям (config/scenario_transitions.yaml, docs/SCENARIO_ENGINE_CONFIG_GUIDE.md).
- 2025-09-23 – Реализован загрузчик YAML-конфига и базовый action registry (app/scenes/config_loader.py, app/scenes/action_registry.py).
- 2025-09-23 – SceneManager переведён на конфиг и покрыт тестами (app/scenes/scene_manager.py, tests/test_scenes.py).
- 2025-09-23 – Реализованы action handlers LLM/бонусы/материалы (app/scenes/action_registry.py, app/services/*).
- 2025-09-23 – Added `/reset` flow and purge helper.
