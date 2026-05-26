# HTTP/HTTPS Debug Proxy

Прокси принимает входящий `HTTP` или `HTTPS` запрос, полностью логирует его, пересылает на целевой сервер из `ini`, логирует ответ целевого сервера и возвращает этот ответ исходному клиенту.

## Запуск HTTP

```bash
cp proxy.ini.example proxy.ini
python3 proxy_server.py --config proxy.ini
```

Минимальные настройки для HTTP:

```ini
[server]
mode = http
host = 0.0.0.0
port = 51400

[target]
scheme = http
host = 127.0.0.1
port = 8080
```

## Запуск HTTPS

Для HTTPS укажите сертификат и ключ:

```ini
[server]
mode = https
host = 0.0.0.0
port = 51401

[tls]
cert_file = certs/server.crt
key_file = certs/server.key
```

Запуск:

```bash
python3 proxy_server.py --config proxy.ini
```

## Проверка

Пример запроса через прокси:

```bash
curl -v -X POST "http://127.0.0.1:51400/api/v1/codes/check?x=1" \
  -H "Content-Type: application/json" \
  -d '{"test": true}'
```

Прокси сохранит на каждый запрос два файла:

```text
proxy_logs/<timestamp>_<request_id>.txt
proxy_logs/<timestamp>_<request_id>.json
```

## Поведение при ошибках

Если целевой сервер вернул HTTP-ответ, клиент получает этот же статус, заголовки и body.

Если прокси не смог подключиться к целевому серверу, дождаться ответа или выполнить пересылку, клиент получает:

```text
502 Bad Gateway
```

Body ошибки возвращается в JSON.
