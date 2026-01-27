# Use an official Python runtime as a parent image
FROM python:3.12-slim

# Install gosu for user switching at runtime
RUN apt-get update && apt-get install -y gosu && rm -rf /var/lib/apt/lists/*

# Set the working directory in the container
WORKDIR /app

# Copy the dependencies file to the working directory
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code to the working directory
COPY app.py ./
COPY static ./static
COPY templates ./templates
COPY clients ./clients

COPY version.txt ./version.txt

# remove this if becomes obsolete
COPY hashing.py ./

COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Make port 5000 available to the world outside this container
EXPOSE 5000

# Production env
ENV ADDRESS=0.0.0.0
ENV PORT=5000

# Force Docker to use root /data instead of relative ./data
ENV DATA_PATH=/data

# Create the data directory explicitly so we can chown it later
RUN mkdir -p $DATA_PATH

# Set the entrypoint to our script
ENTRYPOINT ["/app/entrypoint.sh"]

CMD exec hypercorn --bind ${ADDRESS}:${PORT} \
     --workers 1 \
     --worker-class asyncio \
     --access-logfile - \
     --error-logfile - \
     --log-level info app:app \
     --graceful-timeout 5