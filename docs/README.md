# Отчет по заказам - Тестовое задание

## Описание
Решение тестового задания для позиции Junior аналитик. Проект включает генерацию тестовых данных, 
SQL выгрузку с дедупликацией и создание Excel отчета.

### Требования
- Python 3.11+
- pip install pandas xlsxwriter

### Структура
- data/generate_data.py — генерация CSV (sellers.csv, orders.csv, order_items.csv)
- sql/export.sql — SELECT с дедупликацией по external_id
- py/build_report.py — сборка Excel
- excel/Report.xlsx — итоговый отчёт (генерируется)

Команда для генерации данных:
```
python3 data/generate_data.py --email test@email.com --orders 1000 --days 90
```

Команда для сборки отчёта (бд в оперативной памяти):
```
python3 py/build_report.py --days 90 --out excel/Report.xlsx --db :memory:

```
Команда для сборки отчёта (если нужна бд)

```
python3 py/build_report.py --days 90 --out excel/Report2.xlsx --db data/report.sqlite
```

Что делает скрипт
- Создаёт SQLite (in-memory по умолчанию), включает PRAGMA WAL/NORMAL.
- Создаёт таблицы по схеме, загружает CSV executemany.
- Применяет индексы:
  - orders(external_id), orders(date), orders(delivered_at), orders(updated_at), orders(seller_id)
  - order_items(order_id)
- Выполняет sql/export.sql с параметром :days (окна SQLite), дедуп на уровне orders:
  - delivered_at IS NOT NULL ⇒ берём запись с максимальным delivered_at
  - иначе — с максимальным updated_at
- Пишет Excel:
  - Orders — строки позиций (order_id × sku) + margin
  - Summary — агрегаты по channel и seller (revenue, cost, margin; число позиций; уникальные заказы)
  - Dashboard — графики: маржа по каналам; конверсия (paid/created, prod_started/paid, shipped/prod_started, delivered/shipped, delivered/created)
  - Checks — проблемные записи: qty ≤ 0; margin < 0; пропущенный seller/channel; дубли external_id (по разным order_id — ожидается 0)

Особенности данных
- Встречаются qty ≤ 0 (около 1%), отрицательные маржи (~2%), seller_id вне справочника (~0.5%).
- delivered_at в CSV пустой для недоставленных — при загрузке приводится к NULL.

Замечания по производительности
- На 8k заказов/40k позиций всё работает мгновенно благодаря индексам:
  - orders(external_id) — ускоряет PARTITION BY + join по external_id
  - orders(date) — фильтр периода
  - orders(delivered_at), orders(updated_at) — сортировки в оконной функции
  - order_items(order_id) — join позиций

Промпты/ИИ
- Задание и обсуждение требований — этот чат.
- Автогенерация кода: sql/export.sql, py/build_report.py, правка data/generate_data.py.
