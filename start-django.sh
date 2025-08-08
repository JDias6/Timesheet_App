#!/bin/bash
poetry run python manage.py collectstatic --no-input
echo " Running database migrations"
poetry run python manage.py migrate

if [[ "$ENV_STATE" == "production" ]]; then
    poetry run gunicorn timesheet_app.wsgi --workers $GUNICORN_WORKERS --bind 0.0.0.0:$PORT --forwarded-allow-ips "*"
else
    echo " Starting Django server "
    poetry run python manage.py runserver 0.0.0.0:8000
fi