# Выпуск сертификатов на Ubuntu

Инструкция ниже создает:

- корневой сертификат `CA`;
- приватный ключ `CA`;
- приватный ключ сервера;
- `CSR` сервера;
- серверный сертификат с `SAN` для `DNS` и `IP`.

Все команды рассчитаны на `Ubuntu` и `OpenSSL`.

## 1. Подготовить рабочий каталог

```bash
mkdir -p certs
cd certs
```

## 2. Создать корневой CA

Создать приватный ключ корневого `CA`:

```bash
openssl genrsa -out rootCA.key 4096
```

Создать корневой сертификат `CA` сроком на 10 лет:

```bash
openssl req -x509 -new -nodes -key rootCA.key -sha256 -days 3650 -out rootCA.crt \
  -subj "/C=RU/ST=Moscow/L=Moscow/O=Debug HTTPS Logger/OU=Integration Debug/CN=Debug Root CA"
```

## 3. Подготовить конфигурацию SAN

Скопировать пример конфига:

```bash
cp ../openssl-server.cnf.example server.cnf
```

Открыть `server.cnf` и заменить значения:

- `CN` на DNS-имя сервера;
- `DNS.1` на DNS-имя сервера;
- `IP.1` на IP-адрес сервера.

Если нужно больше имен и адресов, добавить строки вида:

```text
DNS.2 = debug-server
IP.2 = 192.168.1.50
```

## 4. Создать серверный ключ и CSR

Создать приватный ключ сервера:

```bash
openssl genrsa -out server.key 2048
```

Создать `CSR`:

```bash
openssl req -new -key server.key -out server.csr -config server.cnf
```

## 5. Подписать серверный сертификат корневым CA

```bash
openssl x509 -req -in server.csr -CA rootCA.crt -CAkey rootCA.key -CAcreateserial \
  -out server.crt -days 825 -sha256 -extensions req_ext -extfile server.cnf
```

## 6. Проверить SAN и цепочку

Проверить, что в сертификате есть `DNS` и `IP`:

```bash
openssl x509 -in server.crt -text -noout | grep -A 2 "Subject Alternative Name"
```

Проверить, что сертификат подписан вашим `CA`:

```bash
openssl verify -CAfile rootCA.crt server.crt
```

Ожидаемый результат:

```text
server.crt: OK
```

## 7. Запустить Python-сервис

Из корня проекта:

```bash
python3 main.py --host 0.0.0.0 --port 51401 --cert certs/server.crt --key certs/server.key --log-dir logs
```

Если на Ubuntu порт закрыт файрволом, открыть его отдельно штатным способом вашей системы.

## 8. Что передать на Windows-клиент

На Windows нужно передать только публичный корневой сертификат:

```text
certs/rootCA.crt
```

Файл `rootCA.key` передавать на клиент нельзя.
