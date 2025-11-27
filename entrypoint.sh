#!/bin/sh

# 1. Read UID and GID from Env Vars (default to 1000 if not set)
USER_ID=${PUID:-1000}
GROUP_ID=${PGID:-1000}

echo "Starting with UID: $USER_ID, GID: $GROUP_ID"

# 2. Create the group if it doesn't exist
if ! getent group appgroup > /dev/null 2>&1; then
    groupadd -g "$GROUP_ID" appgroup
fi

# 3. Create the user if it doesn't exist
if ! id -u appuser > /dev/null 2>&1; then
    useradd -u "$USER_ID" -g "$GROUP_ID" -m -s /bin/sh appuser
fi

# 4. Handle permissions
# We must ensure the non-root user owns the app and data directories
# 'DATA_PATH' is defined in your Dockerfile as /data
chown -R appuser:appgroup /app
chown -R appuser:appgroup "$DATA_PATH"

# drop root priveleges and execute main command
exec gosu appuser "$@"