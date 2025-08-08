FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \
    && apt-get install -y nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir /code

WORKDIR /code

RUN pip install poetry

# Copy Poetry files and install Python dependencies
COPY pyproject.toml poetry.lock ./

RUN poetry install --no-root

# Copy package.json and install Node dependencies
COPY package*.json ./
RUN npm install

# Copy all project files
COPY . .
RUN chmod 755 /code/start-django.sh

# Build Tailwind CSS for production
RUN npm run build-css-prod

EXPOSE 8000

ENTRYPOINT [ "/code/start-django.sh" ]