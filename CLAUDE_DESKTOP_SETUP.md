# Quick Start: Connecting to Claude Desktop

## Option 1: Docker Server (для вашего случая)

### 1. Запустите Docker сервер:
```bash
docker-compose up -d
```

### 2. Найдите конфигурационный файл Claude Desktop:
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
- **Linux**: `~/.config/Claude/claude_desktop_config.json`

### 3. Откройте файл и добавьте (или замените содержимое):

```json
{
  "mcpServers": {
    "icloud": {
      "transport": {
        "type": "sse",
        "url": "http://localhost:8000/sse"
      },
      "env": {
        "ICLOUD_EMAIL": "your-email@icloud.com",
        "ICLOUD_APP_SPECIFIC_PASSWORD": "xxxx-xxxx-xxxx-xxxx"
      }
    }
  }
}
```

**Важно**: Замените:
- `your-email@icloud.com` на ваш email iCloud
- `xxxx-xxxx-xxxx-xxxx` на App-Specific Password (создайте на https://appleid.apple.com/account/manage)

### 4. Перезапустите Claude Desktop

### 5. Проверьте:
- Откройте Claude Desktop
- Посмотрите в правый нижний угол на иконку 🔨 (hammer/tools)
- Должен отображаться сервер "icloud"
- Попробуйте: "Покажи мои календари" или "List my calendars"

---

## Option 2: Локальный запуск (без Docker)

### 1. Установите зависимости:
```bash
pip install -r requirements.txt
```

### 2. Отредактируйте конфиг Claude Desktop:

```json
{
  "mcpServers": {
    "icloud": {
      "command": "python",
      "args": ["/полный/путь/к/icloud-mcp/run.py"],
      "env": {
        "ICLOUD_EMAIL": "your-email@icloud.com",
        "ICLOUD_APP_SPECIFIC_PASSWORD": "xxxx-xxxx-xxxx-xxxx"
      }
    }
  }
}
```

**Замените** `/полный/путь/к/icloud-mcp/` на реальный путь к проекту.

### 3. Перезапустите Claude Desktop

---

## Troubleshooting

### Сервер не появляется в Claude Desktop:
- Проверьте синтаксис JSON (используйте JSONLint)
- Проверьте логи Claude Desktop (Help → Show Logs)
- Убедитесь что сервер запущен: `curl http://localhost:8000/health` (для Docker)

### Ошибка 401 при использовании инструментов:
- Проверьте правильность email и App-Specific Password
- Убедитесь что используете именно App-Specific Password, а не обычный пароль

### Сервер не запускается:
```bash
# Проверьте статус Docker
docker ps

# Посмотрите логи
docker-compose logs -f

# Перезапустите
docker-compose restart
```

## Доступные инструменты

После подключения вы можете использовать команды типа:
- "Покажи мои календари"
- "Создай событие на завтра в 10:00 - встреча с командой"
- "Покажи контакты"
- "Покажи непрочитанные письма"
- "Отправь письмо на test@example.com с темой 'Тест'"
