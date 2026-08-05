FROM python:3.10-slim

# Install system dependencies for Playwright/Chrome
RUN apt-get update && apt-get install -y \
    wget \
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libdrm2 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers (headless)
RUN playwright install chromium
RUN playwright install-deps chromium

# Copy application code
COPY . .

# Create output directory
RUN mkdir -p /app/datasets

# Default command
ENTRYPOINT ["python", "main.py"]
CMD ["--help"]