#!/bin/bash

# Wait for database to be ready
echo "Waiting for database to be ready..."
max_retries=30
retry_count=0

while [ $retry_count -lt $max_retries ]; do
    if poetry run python manage.py showmigrations > /dev/null 2>&1; then
        echo "Database is ready!"
        break
    fi
    retry_count=$((retry_count + 1))
    echo "Database not ready yet, attempt $retry_count of $max_retries..."
    sleep 2
done

if [ $retry_count -eq $max_retries ]; then
    echo "Failed to connect to database after $max_retries attempts"
    exit 1
fi

# Collect static files
poetry run python manage.py collectstatic --no-input

echo " Running database migrations"
poetry run python manage.py migrate

if [[ "$ENV_STATE" == "production" ]]; then
    poetry run gunicorn timesheet_app.wsgi --workers ${GUNICORN_WORKERS:-4} --bind 0.0.0.0:$PORT --forwarded-allow-ips "*"
else
    echo " Starting Django server "
    poetry run python manage.py runserver 0.0.0.0:8000
fi