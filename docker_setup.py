"""
Docker Configuration for Project Aletheia
Provides a reproducible environment with headless Chrome support.
"""

# Dockerfile content
dockerfile_content = """
FROM python:3.10-slim

# Install system dependencies for Playwright/Chrome
RUN apt-get update && apt-get install -y \\
    wget \\
    ca-certificates \\
    fonts-liberation \\
    libasound2 \\
    libatk-bridge2.0-0 \\
    libdrm2 \\
    libgtk-3-0 \\
    libnspr4 \\
    libnss3 \\
    libxcomposite1 \\
    libxdamage1 \\
    libxfixes3 \\
    libxrandr2 \\
    xdg-utils \\
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
"""

# docker-compose.yml content
docker_compose_content = """
version: '3.8'

services:
  aletheia:
    build: .
    volumes:
      # Mount local datasets folder to persist output
      - ./datasets:/app/datasets
      # Mount local corpus if needed
      - ./corpus:/app/corpus
    environment:
      - PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
      - PYTHONUNBUFFERED=1
    # Run as non-root for security (optional, requires user setup)
    # user: "1000:1000"
    command: ["--mode", "dry-run", "--text", "Sample text for testing."]
"""

# .dockerignore content
dockerignore_content = """
__pycache__/
*.pyc
*.pyo
.git
.gitignore
datasets/
*.md
!requirements.txt
.env
venv/
.env
"""

def write_docker_files():
    import os
    print("Writing Docker configuration files...")
    
    with open("Dockerfile", "w") as f:
        f.write(dockerfile_content.strip())
    print("✅ Created Dockerfile")
    
    with open("docker-compose.yml", "w") as f:
        f.write(docker_compose_content.strip())
    print("✅ Created docker-compose.yml")
    
    with open(".dockerignore", "w") as f:
        f.write(dockerignore_content.strip())
    print("✅ Created .dockerignore")
    
    print("\n🐳 To build and run:")
    print("   docker-compose build")
    print("   docker-compose run aletheia --text \"Your text here\" --mode dry-run")

if __name__ == "__main__":
    write_docker_files()
