# Выпуск сертификатов на Ubuntu с поддержкой CRL

Эта инструкция предназначена для сценария, в котором Windows-клиент выполняет обязательную проверку отзыва сертификата через `schannel`.

Простого корневого `CA` без опубликованного `CRL` для такого сценария недостаточно. Нужна полноценная схема:

- корневой `CA`;
- серверный сертификат;
- `CRL Distribution Point` в сертификатах;
- опубликованный `CRL`, доступный по HTTP с Windows-клиента.

## 1. Подготовить каталог CA

Из корня проекта:

```bash
mkdir -p certs/{certs,crl,newcerts}
cd certs
touch index.txt
echo 1000 > serial
echo 1000 > crlnumber
cp ../openssl-ca.cnf.example openssl-ca.cnf
cp ../openssl-server.cnf.example server.cnf
```

## 2. Настроить адреса под ваш стенд

Открыть `openssl-ca.cnf` и `server.cnf` и заменить:

- `192.168.3.17` на реальный IP Ubuntu-сервера;
- `debug-server.local` на реальное DNS-имя сервера, если оно используется;
- при необходимости добавить дополнительные `DNS.N` и `IP.N`.

Критично:

- URL в `crlDistributionPoints` должен быть доступен с Windows-клиента;
- если клиент ходит по IP, этот IP должен быть в `SAN`;
- если клиент ходит по DNS, этот DNS должен быть в `SAN`.

Пример URL для `CRL`:

```text
http://192.168.3.17:8080/crl/rootCA.crl
```

## 3. Создать корневой CA

Создать приватный ключ:

```bash
openssl genrsa -out rootCA.key 4096
```

Создать корневой сертификат с расширениями `v3_ca`:

```bash
openssl req -x509 -new -nodes -key rootCA.key -sha256 -days 3650 -out rootCA.crt -config openssl-ca.cnf -extensions v3_ca
```

## 4. Создать первый CRL

Сгенерировать `CRL` сразу после выпуска `CA`:

```bash
openssl ca -config openssl-ca.cnf -gencrl -out crl/rootCA.crl
```

## 5. Создать серверный ключ и CSR

```bash
openssl genrsa -out server.key 2048
openssl req -new -key server.key -out server.csr -config server.cnf
```

## 6. Подписать серверный сертификат через openssl ca

```bash
openssl ca -batch -config openssl-ca.cnf -extensions server_cert -in server.csr -out server.crt
```

## 7. Перегенерировать CRL после выпуска сертификата

После любых операций выпуска или отзыва сертификатов обновляйте `CRL`:

```bash
openssl ca -config openssl-ca.cnf -gencrl -out crl/rootCA.crl
```

## 8. Проверить сертификат и CRL

Проверить наличие `SAN`:

```bash
openssl x509 -in server.crt -text -noout | grep -A 4 "Subject Alternative Name"
```

Проверить наличие `CRL Distribution Points`:

```bash
openssl x509 -in server.crt -text -noout | grep -A 4 "CRL Distribution Points"
```

Проверить подпись:

```bash
openssl verify -CAfile rootCA.crt server.crt
```

Проверить локально с использованием `CRL`:

```bash
openssl verify -crl_check -CAfile rootCA.crt -CRLfile crl/rootCA.crl server.crt
```

Ожидаемый результат:

```text
server.crt: OK
```

## 9. Опубликовать CRL по HTTP

Windows должен иметь возможность скачать `CRL` по URL, указанному в сертификате.

Пример публикации:

```bash
cd certs
python3 -m http.server 8080
```

В этом режиме файл `crl/rootCA.crl` будет доступен по адресу:

```text
http://<SERVER_IP>:8080/crl/rootCA.crl
```

Важно:

- этот HTTP-сервер должен быть доступен с Windows-клиента;
- если между клиентом и сервером есть файрвол, нужно открыть `8080/tcp`;
- при каждом обновлении `CRL` файл на этом URL должен оставаться актуальным.

## 10. Запустить HTTPS-сервис

Из корня проекта:

```bash
python3 main.py --host 0.0.0.0 --port 51401 --cert certs/server.crt --key certs/server.key --log-dir logs
```

## 11. Что передать на Windows-клиент

Нужно передать:

- `certs/rootCA.crt`

Передавать нельзя:

- `certs/rootCA.key`

## 12. Как отзывать сертификат при необходимости

Пример отзыва серверного сертификата:

```bash
openssl ca -config openssl-ca.cnf -revoke server.crt
openssl ca -config openssl-ca.cnf -gencrl -out crl/rootCA.crl
```

После этого Windows-клиент при следующей проверке сможет увидеть обновленный статус отзыва через опубликованный `CRL`.
