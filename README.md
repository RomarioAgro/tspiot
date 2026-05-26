# HTTPS Debug Logger

Сервис принимает HTTPS-запросы на порт `51401`, отвечает `200 OK` и сохраняет каждый запрос в отдельные файлы `txt` и `json`.
Для заданных `method + path` он может возвращать заранее описанные JSON-ответы из внешнего файла [responses.json](/D:/PythonProject/tspiot/responses.json).

## Требования

- Python `3.10+`
- TLS-сертификат в формате `PEM`
- приватный ключ в формате `PEM`
- для Windows-клиентов с обязательной проверкой отзыва нужен опубликованный `CRL`

## Быстрый запуск

Пример запуска:

```bash
python3 main.py \
  --host 0.0.0.0 \
  --port 51401 \
  --cert certs/server.crt \
  --key certs/server.key \
  --log-dir logs \
  --responses responses.json
```

После запуска сервис будет доступен по адресу:

```text
https://<server-host-or-ip>:51401
```

## Что пишет сервис

На каждый запрос создаются:

- `logs/<timestamp>_<request_id>.txt`
- `logs/<timestamp>_<request_id>.json`

В логах сохраняются:

- метод;
- путь;
- query string;
- заголовки;
- body;
- декодированное представление body;
- IP и порт клиента;
- служебные метаданные запроса;
- фактически отправленный ответ.

## Настройка ответов

Файл [responses.json](/D:/PythonProject/tspiot/responses.json) лежит рядом со скриптом запуска и задает ответы для конкретных маршрутов.

Сопоставление идет по:

- HTTP-методу;
- пути без query string.

Сейчас в конфиге преднастроены положительные ответы по документации для:

- `POST /api/v1/codes/check`
- `POST /api/v1/info`

Если маршрут не найден в конфиге, сервер по умолчанию отвечает:

```json
{"status":"ok"}
```

## Документация

- Инструкция по выпуску сертификатов на Ubuntu с `CRL`: [ubuntu-certs.md](/D:/PythonProject/tspiot/ubuntu-certs.md)
- Инструкция по запуску и проверке с Windows при обязательной проверке отзыва: [windows-run-and-test.md](/D:/PythonProject/tspiot/windows-run-and-test.md)
- Инструкция по debug proxy: [proxy.md](/D:/PythonProject/tspiot/proxy.md)
- Техническое задание: [tz.md](/D:/PythonProject/tspiot/tz.md)
- Техническое задание по debug proxy: [tz_proxy.md](/D:/PythonProject/tspiot/tz_proxy.md)
