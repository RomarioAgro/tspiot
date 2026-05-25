# HTTPS Debug Logger

Сервис принимает HTTPS-запросы на порт `51401`, отвечает `200 OK` и сохраняет каждый запрос в отдельные файлы `txt` и `json`.

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
  --log-dir logs
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
- служебные метаданные запроса.

## Документация

- Инструкция по выпуску сертификатов на Ubuntu с `CRL`: [ubuntu-certs.md](/D:/PythonProject/tspiot/ubuntu-certs.md)
- Инструкция по запуску и проверке с Windows при обязательной проверке отзыва: [windows-run-and-test.md](/D:/PythonProject/tspiot/windows-run-and-test.md)
- Техническое задание: [tz.md](/D:/PythonProject/tspiot/tz.md)
