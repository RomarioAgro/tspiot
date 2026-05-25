# Запуск и проверка с Windows

Инструкция покрывает:

- импорт корневого сертификата `CA` в доверенные корневые центры;
- проверку TLS-доверия;
- отправку тестового HTTPS-запроса на сервис.

## 1. Импортировать корневой сертификат в Windows

Нужен файл:

```text
rootCA.crt
```

Открыть `PowerShell` от имени администратора и выполнить:

```powershell
certutil -addstore -f Root .\rootCA.crt
```

Проверить, что сертификат появился в хранилище:

```powershell
certutil -store Root "Debug Root CA"
```

Если сертификат импортируется только для текущего пользователя, можно использовать GUI через `certmgr.msc`, но для системного доверия надежнее `certutil` с административными правами.

## 2. Проверить сетевую доступность сервера

Проверка TCP-порта:

```powershell
Test-NetConnection <SERVER_DNS_OR_IP> -Port 51401
```

## 3. Отправить тестовый HTTPS-запрос

Пример `POST` с JSON:

```powershell
$body = @{
  source = "windows-client"
  action = "debug-test"
  value = 123
} | ConvertTo-Json

Invoke-RestMethod `
  -Uri "https://<SERVER_DNS_OR_IP>:51401/api/test?mode=probe" `
  -Method Post `
  -ContentType "application/json; charset=utf-8" `
  -Body $body
```

Ожидаемый ответ:

```json
{"status":"ok"}
```

## 4. Проверить, что TLS-доверие работает

Если корневой сертификат импортирован правильно и имя обращения совпадает с `SAN` в сертификате:

- `Invoke-RestMethod` не должен выдавать ошибку недоверенного сертификата;
- браузер при открытии `https://<SERVER_DNS_OR_IP>:51401/` не должен показывать ошибку `untrusted certificate`.

Важно:

- если обращение идет по `DNS`, в `SAN` сертификата должен быть этот `DNS`;
- если обращение идет по `IP`, в `SAN` сертификата должен быть этот `IP`;
- если `SAN` не совпадает со способом обращения, Windows будет считать сертификат некорректным даже при доверенном `CA`.

## 5. Проверить создание логов на сервере

После тестового запроса на Ubuntu-сервере в каталоге `logs` должны появиться два файла:

```text
<timestamp>_<request_id>.txt
<timestamp>_<request_id>.json
```

## 6. Удаление корневого сертификата из доверенных при необходимости

Если нужно убрать тестовый `CA`, выполнить в `PowerShell` от имени администратора:

```powershell
certutil -delstore Root "Debug Root CA"
```
