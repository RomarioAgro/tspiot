# Запуск и проверка с Windows при обязательной проверке отзыва

Эта инструкция рассчитана на Windows-клиента, который не может отключить `revocation check`.

В таком режиме должны выполняться все условия:

- корневой `CA` импортирован в `Trusted Root`;
- серверный сертификат содержит правильный `SAN`;
- в сертификате указан рабочий `CRL Distribution Point`;
- Windows-клиент может скачать `CRL` по HTTP.

## 1. Импортировать корневой сертификат

Открыть `PowerShell` от имени администратора и выполнить:

```powershell
certutil -addstore -f Root .\rootCA.crt
```

Проверить, что сертификат установлен:

```powershell
certutil -store Root "Debug Root CA"
```

## 2. Проверить сетевую доступность сервера

Проверка HTTPS-порта:

```powershell
Test-NetConnection <SERVER_DNS_OR_IP> -Port 51401
```

Проверка доступности `CRL` по HTTP:

```powershell
Invoke-WebRequest -Uri "http://<SERVER_IP>:8080/crl/rootCA.crl" -OutFile "$env:TEMP\rootCA.crl"
```

Если этот шаг не работает, `curl` через `schannel` не пройдет проверку отзыва.

## 3. Проверить сертификат через Windows URL fetch

Если у вас есть локальная копия серверного сертификата, можно проверить его так:

```powershell
certutil -urlfetch -verify .\server.crt
```

Если сертификат находится только на сервере, можно сначала выгрузить его через браузер или `openssl s_client` на Ubuntu и передать на Windows для проверки.

На что смотреть:

- Windows должен видеть цепочку до `Debug Root CA`;
- Windows должен уметь скачать `CRL`;
- в результате не должно быть ошибки `0x80092012`.

## 4. Отправить тестовый HTTPS-запрос

Пример через `Invoke-RestMethod`:

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

## 5. Проверить bat-сценарий с curl.exe

Если `test.bat` использует стандартный Windows `curl.exe`, он опирается на `schannel` и тоже требует рабочую проверку отзыва.

После правильной настройки `CRL` bat-файл должен пройти без флагов `-k` и `--ssl-no-revoke`.

Если ошибка остается, проверять нужно в таком порядке:

1. доступен ли `https://<SERVER_DNS_OR_IP>:51401`;
2. совпадает ли `DNS` или `IP` с `SAN`;
3. доступен ли `http://<SERVER_IP>:8080/crl/rootCA.crl`;
4. показывает ли `certutil -urlfetch -verify` ошибку по `CRL`.

## 6. Проверить появление логов на Ubuntu

После успешного запроса на Ubuntu-сервере должны появиться:

```text
logs/<timestamp>_<request_id>.txt
logs/<timestamp>_<request_id>.json
```

## 7. Удаление корневого сертификата при необходимости

```powershell
certutil -delstore Root "Debug Root CA"
```
