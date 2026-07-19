#!/bin/sh

# 1. Read UID and GID from Env Vars (default to 1000 if not set)
USER_ID=${PUID:-1000}
GROUP_ID=${PGID:-1000}

case "$USER_ID" in
    ''|*[!0-9]*) echo "ERROR: PUID must be a non-negative integer, got '$USER_ID'" >&2; exit 1 ;;
esac
case "$GROUP_ID" in
    ''|*[!0-9]*) echo "ERROR: PGID must be a non-negative integer, got '$GROUP_ID'" >&2; exit 1 ;;
esac

echo "Starting with UID: $USER_ID, GID: $GROUP_ID"

if [ "$USER_ID" = "0" ] || [ "$GROUP_ID" = "0" ]; then
    echo "WARNING: PUID/PGID 0 runs the app with root privileges" >&2
fi

# 2. Create the group only if the numeric GID is unused
# (e.g. Synology's PGID=100 already exists here as 'users' — reuse it)
if ! getent group "$GROUP_ID" > /dev/null 2>&1; then
    groupadd -g "$GROUP_ID" appgroup || { echo "ERROR: failed to create group with GID $GROUP_ID" >&2; exit 1; }
fi

# 3. Create the user if it doesn't exist
# -o allows the UID to coexist with a base-image user (e.g. PUID=65534 'nobody')
if ! id -u appuser > /dev/null 2>&1; then
    useradd -o -u "$USER_ID" -g "$GROUP_ID" -m -s /bin/sh appuser || { echo "ERROR: failed to create user with UID $USER_ID" >&2; exit 1; }
fi

# 4. Handle permissions
# Chown by numeric ID so it never depends on what the group/user is named.
# Non-fatal: on root-squashed NFS chown fails even when permissions are fine.
chown -R "$USER_ID:$GROUP_ID" /app "$DATA_PATH" || \
    echo "WARNING: chown failed (read-only or root-squashed mount?); continuing" >&2

# Verify the actual preconditions so a bad mount fails here with a clear
# message instead of a Python traceback from hypercorn
if ! gosu appuser sh -c "test -r /app/app.py && test -w '$DATA_PATH'"; then
    echo "ERROR: UID $USER_ID cannot read /app or write $DATA_PATH — check volume permissions" >&2
    exit 1
fi

# drop root priveleges and execute main command
exec gosu appuser "$@"
